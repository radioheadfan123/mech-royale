"""Static map: terrain + prisms rasterized to a 1 m grid (stdlib only)."""
import array
import json
import math
import os

INF = 1e9


class World:
    def __init__(self, map_dir):
        w = json.load(open(os.path.join(map_dir, "v2/world.json")))
        t = w["terrain"]
        self.nx, self.ny = t["nx"], t["ny"]
        self.x0, self.y0, self.step = t["minX"], t["minY"], t["step"]
        a = array.array("h")
        with open(os.path.join(map_dir, "v2", t["file"]), "rb") as f:
            a.frombytes(f.read())
        sc = t.get("scale", 0.01)
        self.ground = array.array("f", (v * sc for v in a))
        n = self.nx * self.ny
        self.sb = array.array("f", [INF]) * n   # lowest solid base in the cell
        self.st = array.array("f", [-INF]) * n  # highest solid top in the cell
        for p in w["prisms"]:
            self._fill(p)

    def _fill(self, p):
        rings = [p["ring"]] + list(p.get("holes") or [])
        edges = []
        for r in rings:
            k = len(r) // 2
            for i in range(k):
                edges.append((r[2 * i], r[2 * i + 1], r[2 * ((i + 1) % k)], r[2 * ((i + 1) % k) + 1]))
        ys = p["ring"][1::2]
        j0 = max(0, math.ceil((min(ys) - self.y0) / self.step))
        j1 = min(self.ny - 1, math.floor((max(ys) - self.y0) / self.step))
        base, top, nx, sb, st = p["base"], p["top"], self.nx, self.sb, self.st
        for j in range(j0, j1 + 1):
            y = self.y0 + j * self.step
            xs = sorted(ax + (y - ay) * (bx - ax) / (by - ay)
                        for ax, ay, bx, by in edges if (ay <= y) != (by <= y))
            for k in range(0, len(xs) - 1, 2):
                i0 = max(0, math.ceil((xs[k] - self.x0) / self.step))
                i1 = min(nx - 1, math.floor((xs[k + 1] - self.x0) / self.step))
                for i in range(i0, i1 + 1):
                    c = j * nx + i
                    if base < sb[c]:
                        sb[c] = base
                    if top > st[c]:
                        st[c] = top

    def cell(self, x, y):
        i = int((x - self.x0) / self.step + 0.5)
        j = int((y - self.y0) / self.step + 0.5)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return j * self.nx + i
        return -1

    def gz(self, x, y):
        c = self.cell(x, y)
        return self.ground[c] if c >= 0 else 0.0

    def blocked(self, x, y, z):
        """A point is inside terrain or a solid."""
        c = self.cell(x, y)
        if c < 0:
            return True
        return z < self.ground[c] or self.sb[c] <= z <= self.st[c]

    def los(self, ax, ay, az, bx, by, bz, skip=3.0, end_skip=2.0):
        d = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2 + (bz - az) ** 2)
        if d < 1e-6:
            return True
        n = int(d)
        dx, dy, dz = (bx - ax) / d, (by - ay) / d, (bz - az) / d
        s = skip
        lim = d - end_skip
        while s < lim:
            if self.blocked(ax + dx * s, ay + dy * s, az + dz * s):
                return False
            s += 1.0
        return True

    def wall_ahead(self, x, y, z, dx, dy, dist=7.0, step_up=1.2, head=9.0):
        """First distance along (dx, dy) where something taller than a step and lower than our head stands."""
        s = 2.0
        while s <= dist:
            c = self.cell(x + dx * s, y + dy * s)
            if c < 0:
                return s
            g = self.ground[c]
            if g > z + step_up + s * 0.8:  # steep terrain
                return s
            if self.st[c] > z + step_up and self.sb[c] < z + head and self.st[c] > g + 0.3:
                return s
            s += 1.0
        return None

    def wall_top(self, x, y, z, dx, dy, dist=7.0):
        best = -INF
        s = 2.0
        while s <= dist:
            c = self.cell(x + dx * s, y + dy * s)
            if c >= 0:
                best = max(best, self.st[c], self.ground[c])
            s += 1.0
        return best


class Nav:
    """4 m walk grid + BFS distance field to the safe circle, built a slice at a time."""
    G = 4

    def __init__(self, w):
        self.w = w
        G = self.G
        self.cx, self.cy = w.nx // G, w.ny // G
        free = bytearray(self.cx * self.cy)
        for j in range(self.cy):
            for i in range(self.cx):
                tall = 0
                for b in range(G):
                    row = (j * G + b) * w.nx + i * G
                    for a in range(G):
                        c = row + a
                        g = w.ground[c]
                        if w.st[c] - g > 3 and w.sb[c] < g + 9:
                            tall += 1
                free[j * self.cx + i] = tall < 6
        self.free = free
        self.dist = None
        self.key = None
        self.job = None

    def idx(self, x, y):
        i = int((x - self.w.x0) / (self.w.step * self.G))
        j = int((y - self.w.y0) / (self.w.step * self.G))
        if 0 <= i < self.cx and 0 <= j < self.cy:
            return j * self.cx + i
        return -1

    def center(self, k):
        s = self.w.step * self.G
        return self.w.x0 + (k % self.cx + 0.5) * s, self.w.y0 + (k // self.cx + 0.5) * s

    def target(self, cx, cy, r):
        """Ask for a field toward circle (cx, cy, r); work happens in work()."""
        key = (round(cx), round(cy), round(r))
        if key != self.key:
            self.key = key
            self.job = self._bfs(cx, cy, r)

    def _bfs(self, cx, cy, r):
        n = self.cx * self.cy
        dist = array.array("i", [-1]) * n
        q = []
        r = max(r, 6.0)
        for k in range(n):
            if self.free[k]:
                x, y = self.center(k)
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    dist[k] = 0
                    q.append(k)
            if k % 4000 == 0:
                yield
        W, H, free = self.cx, self.cy, self.free
        h = 0
        while h < len(q):
            k = q[h]
            h += 1
            d = dist[k] + 1
            i, j = k % W, k // W
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                a, b = i + di, j + dj
                if 0 <= a < W and 0 <= b < H:
                    m = b * W + a
                    if dist[m] < 0 and free[m]:
                        dist[m] = d
                        q.append(m)
            if h % 3000 == 0:
                yield
        self.dist = dist

    def work(self, budget_s=0.008):
        import time
        if self.job is None:
            return
        t0 = time.perf_counter()
        for _ in self.job:
            if time.perf_counter() - t0 > budget_s:
                return
        self.job = None

    def step(self, x, y, look=4):
        """Direction (ux, uy) along the field from (x, y), or None if not ready / off-grid."""
        if self.dist is None or self.job is not None:
            return None
        k = self.idx(x, y)
        if k < 0:
            return None
        W, H, dist = self.cx, self.cy, self.dist
        if dist[k] < 0:  # standing in a blocked cell: take the best neighbour
            best = None
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    m = k + dj * W + di
                    if 0 <= m < len(dist) and dist[m] >= 0 and (best is None or dist[m] < dist[best]):
                        best = m
            if best is None:
                return None
            k = best
        if dist[k] == 0:
            return None
        for _ in range(look):
            i, j = k % W, k // W
            nxt = k
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                a, b = i + di, j + dj
                if 0 <= a < W and 0 <= b < H:
                    m = b * W + a
                    if 0 <= dist[m] < dist[nxt]:
                        if di and dj and not (self.free[j * W + a] and self.free[b * W + i]):
                            continue
                        nxt = m
            if nxt == k:
                break
            k = nxt
        tx, ty = self.center(k)
        d = math.hypot(tx - x, ty - y)
        return ((tx - x) / d, (ty - y) / d) if d > 1e-6 else None


if __name__ == "__main__":
    import sys
    import time
    w = World(sys.argv[1] if len(sys.argv) > 1 else "arena")
    t0 = time.perf_counter()
    nav = Nav(w)
    print("nav build s", round(time.perf_counter() - t0, 2), "free", sum(nav.free), "of", len(nav.free))
    nav.target(41.3, -72.8, 140)
    ticks, worst = 0, 0.0
    while nav.job is not None:
        t1 = time.perf_counter()
        nav.work()
        worst = max(worst, time.perf_counter() - t1)
        ticks += 1
    print("bfs ticks", ticks, "worst ms", round(worst * 1000, 1))
    assert nav.step(41.3, -72.8) is None  # inside: no move
    d = nav.step(-217, 64)
    print("dir from stuck spot", d)
    assert d is not None
