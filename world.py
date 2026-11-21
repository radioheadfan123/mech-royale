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
