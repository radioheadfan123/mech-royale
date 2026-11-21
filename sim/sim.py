"""Rough local Mech Royale sim: one bot under test (slot 0, a subprocess) vs 15 house bots that hunt it.
ponytail: approximates the server (no NPCs, crates, mortar, drops, trees, upgrades); good for A/B, not for absolute scores.
Usage: python3 sim.py MAP_DIR SEED -- python3 -u bot.py   -> prints JSON {seed, place, t, kills}
"""
import gzip
import json
import math
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from world import World  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TAU = 2 * math.pi
DT = 0.05
TIERS = ["easy"] * 4 + ["medium"] * 4 + ["hard"] * 4 + ["expert"] * 3
# per tier: aim noise (deg), lead fraction, reaction s, takes lasers
TIER = {"easy": (3.0, 0.0, 0.7, False), "medium": (2.0, 0.6, 0.45, False),
        "hard": (1.2, 0.9, 0.3, True), "expert": (0.7, 1.0, 0.2, True)}


TRACE = os.environ.get('TRACE')


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def load_hello():
    with gzip.open(os.path.join(HERE, "..", "check", "stream.jsonl.gz"), "rt") as f:
        return json.loads(f.readline())


class Mech:
    def __init__(self, i, pos, cfg):
        m = cfg["mech"]
        self.id, self.pos, self.vel = i, list(pos), [0.0, 0.0, 0.0]
        self.aim, self.cmd_aim = [0.0, 0.0], [0.0, 0.0]
        self.hp = self.max_hp = float(m["hp"])
        self.energy, self.e_idle = float(m["energy"]), 0.0
        self.grounded, self.alive = True, True
        g = cfg["weapons"]["gun"]
        self.weapons = [{"kind": "gun", "cooldown": 0.0, "ammo": g["mag"], "reload": 0.0, "stats": dict(g)}, None]
        self.last_dmg = -99.0
        self.move, self.boost, self.jump, self.fire = (0.0, 0.0), False, False, [False, False]
        self.kills, self.died_t = 0, None
        self.seen = {}  # id -> last seen t (bot memory)


class Sim:
    def __init__(self, map_dir, seed, cmd):
        self.rng = random.Random(seed)
        self.seed = seed
        self.hello = load_hello()
        self.cfg = self.hello["config"]
        self.hello["map"]["dir"] = map_dir
        self.w = World(map_dir)
        self.t, self.tick = 0.0, 0
        self.proj, self.pickups, self.sounds, self.beams, self.events = [], [], [], [], []
        z = self.cfg["zone"]
        self.zc, self.zr = list(z["start_center"]), z["start_radius"]
        self.phases = self._zone_plan()
        self.mechs = []
        spots, sep = [], self.cfg["match"]["spawn_min_separation"]
        while len(spots) < 16:
            p = self.open_spot(self.zc, self.zr * self.cfg["match"]["spawn_zone_fraction"])
            if all(math.dist(p[:2], q[:2]) > sep for q in spots):
                spots.append(p)
            else:
                sep *= 0.999  # ponytail: relax slowly if the map is crowded
        for i, p in enumerate(spots):
            self.mechs.append(Mech(i, p, self.cfg))
        order = list(range(1, 16))
        self.rng.shuffle(order)
        self.tier = {mid: TIERS[k] for k, mid in enumerate(order)}
        self.hunt_order = sorted(order, key=lambda m: ["easy", "medium", "hard", "expert"].index(self.tier[m]))
        self.react = {m: 0.0 for m in order}
        for k in range(self.cfg["loot"]["floor_count"]):
            self.pickups.append({"id": 8000 + k, "kind": self.rng.choice(["mortar", "laser", "lightning"]),
                                 "pos": self.open_spot(self.zc, self.zr)})
        self.next_pick = 8100
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.send(dict(self.hello, slot=0, seed=seed))
        r = json.loads(self.proc.stdout.readline())
        assert r.get("type") == "ready", r
        self.late = 0
        self.max_ms = 0.0

    def open_spot(self, c, r):
        while True:
            a, d = self.rng.uniform(0, TAU), r * math.sqrt(self.rng.random())
            x, y = c[0] + d * math.cos(a), c[1] + d * math.sin(a)
            k = self.w.cell(x, y)
            if k >= 0 and self.w.st[k] < -1e8:
                return [x, y, self.w.ground[k]]

    def _zone_plan(self):
        z = self.cfg["zone"]
        out, t, c, r = [], 0.0, list(z["start_center"]), z["start_radius"]
        for ph in z["phases"]:
            nr = ph["radius"]
            a, d = self.rng.uniform(0, TAU), (r - nr) * math.sqrt(self.rng.random()) if nr > 0 else 0
            nc = [c[0] + d * math.cos(a), c[1] + d * math.sin(a)]
            out.append((t + ph["hold_s"], t + ph["hold_s"] + ph["shrink_s"], c, r, nc, nr, ph["dps"]))
            t += ph["hold_s"] + ph["shrink_s"]
            c, r = nc, nr
        return out

    def zone(self):
        for s0, s1, c, r, nc, nr, dps in self.phases:
            if self.t < s1:
                f = 0.0 if self.t < s0 else (self.t - s0) / (s1 - s0)
                cur = [c[0] + (nc[0] - c[0]) * f, c[1] + (nc[1] - c[1]) * f]
                return {"center": cur, "radius": r + (nr - r) * f, "next_center": nc, "next_radius": nr,
                        "shrink_start": s0, "shrink_end": s1, "dps": dps}
        s0, s1, c, r, nc, nr, dps = self.phases[-1]
        return {"center": nc, "radius": 0.0, "next_center": nc, "next_radius": 0.0,
                "shrink_start": s0, "shrink_end": s1, "dps": dps}

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    # ---------- physics ----------
    def surface(self, x, y, z):
        k = self.w.cell(x, y)
        if k < 0:
            return None
        g, sb, st = self.w.ground[k], self.w.sb[k], self.w.st[k]
        if st > -1e8 and st <= z + self.cfg["mech"]["step_up"] + 0.01:
            return max(g, st)
        if st > -1e8 and sb <= z + 9 and st > z:
            return None  # wall
        if g > z + self.cfg["mech"]["step_up"] and not (st > -1e8 and sb > g + 9):
            return None
        return g

    def step_mech(self, m):
        mc = self.cfg["mech"]
        sp = mc["walk_speed"]
        if m.boost and m.grounded and m.energy > 0:
            sp = mc["boost_speed"]
        L = m.weapons[1]
        if L and L["kind"] == "laser" and L.get("charge", 0) > 0:
            sp *= mc["charge_move_mult"]
        mx, my = m.move
        n = math.hypot(mx, my)
        if n > 1:
            mx, my = mx / n, my / n
        acc = (mc["ground_accel"] if m.grounded else mc["air_accel"]) * DT
        for i, want in ((0, mx * sp), (1, my * sp)):
            dv = want - m.vel[i]
            m.vel[i] += max(-acc, min(acc, dv))
        spend = False
        if m.jump and m.grounded:
            m.vel[2] = mc["jump_speed"]
            m.grounded = False
        if not m.grounded:
            m.vel[2] -= mc["gravity"] * DT
            if m.boost and m.energy > 0:
                m.vel[2] = min(mc["max_rise_speed"], m.vel[2] + mc["jet_accel"] * DT)
                m.energy -= mc["jet_drain"] * DT
                spend = True
        elif m.boost and n > 0.1:
            m.energy -= mc["boost_drain"] * DT
            spend = True
        m.energy = max(0.0, m.energy)
        if spend:
            m.e_idle = 0.0
        else:
            m.e_idle += DT
            if m.e_idle > mc["energy_regen_delay"]:
                m.energy = min(mc["energy"], m.energy + mc["energy_regen"] * DT)
        x, y, z = m.pos
        nx_, ny_ = x + m.vel[0] * DT, y + m.vel[1] * DT
        for tx, ty in ((nx_, ny_), (nx_, y), (x, ny_)):
            s = self.surface(tx, ty, z)
            if s is not None and (not m.grounded or s >= z - 3 or True):
                if (tx, ty) != (nx_, ny_):
                    if tx == x:
                        m.vel[0] = 0
                    else:
                        m.vel[1] = 0
                x, y = tx, ty
                break
        else:
            m.vel[0] = m.vel[1] = 0
        s = self.surface(x, y, z)
        if s is None:
            s = self.w.gz(x, y)
        z += m.vel[2] * DT
        if m.grounded:
            if s < z - 0.5:
                m.grounded = False
            else:
                z = s
        if not m.grounded and z <= s:
            z, m.vel[2], m.grounded = s, 0.0, True
        m.pos = [x, y, z]
        # torso slew
        for i, rate in ((0, mc["turn_rate"]), (1, mc["pitch_rate"])):
            d = wrap(m.cmd_aim[i] - m.aim[i]) if i == 0 else m.cmd_aim[i] - m.aim[i]
            m.aim[i] += max(-rate * DT, min(rate * DT, d))
        m.aim[0] = wrap(m.aim[0])
        m.aim[1] = max(mc["min_pitch"], min(mc["max_pitch"], m.aim[1]))

    def muzzle(self, m):
        return m.pos[0], m.pos[1], m.pos[2] + self.cfg["mech"]["muzzle_height"]

    def dirv(self, aim):
        return (math.cos(aim[1]) * math.cos(aim[0]), math.cos(aim[1]) * math.sin(aim[0]), math.sin(aim[1]))

    def damage(self, src, dst, amt, weapon, origin):
        if not dst.alive:
            return
        b = math.atan2(origin[1] - dst.pos[1], origin[0] - dst.pos[0])
        rear = abs(wrap(b - dst.aim[0])) > math.radians(self.cfg["mech"]["front_arc_deg"] / 2)
        if rear:
            amt *= self.cfg["mech"]["rear_mult"]
        dst.hp -= amt
        if TRACE and 0 in (dst.id, src.id if src else -1):
            print(f"{self.t:6.1f} {src.id if src else 'Z'}->{dst.id} {weapon} {amt:.0f} rear={rear} hp={dst.hp:.0f} d={math.dist(dst.pos, origin):.0f}", file=sys.stderr)
        dst.last_dmg = self.t
        if src is not None:
            src.last_dmg = self.t
        if dst.id == 0:
            self.events.append({"type": "damaged", "amount": round(amt, 1), "weapon": weapon, "bearing": b,
                                "by": src.id if src else None, "rear": rear})
        if src is not None and src.id == 0:
            self.events.append({"type": "hit", "target": dst.id, "amount": round(amt, 1), "weapon": weapon})
        if src is not None and src.id != 0 and dst.id != 0:
            self.react[dst.id] = self.react.get(dst.id, 0)
        if dst.hp <= 0:
            dst.alive, dst.died_t = False, self.t
            dst.hp = 0
            self.sound("death", dst.pos)
            if src is not None:
                src.kills += 1
                if src.id == 0:
                    self.events.append({"type": "kill", "target": dst.id})
            if dst.weapons[1]:
                self.pickups.append({"id": self.next_pick, "kind": dst.weapons[1]["kind"], "pos": list(dst.pos)})
                self.next_pick += 1

    def sound(self, kind, pos, by=None):
        self.sounds.append((kind, list(pos), self.t, by))

    def hit_cyl(self, m, x, y, z, r=0.3):
        return math.hypot(x - m.pos[0], y - m.pos[1]) < 2.5 + r and m.pos[2] - r <= z <= m.pos[2] + 9 + r

    def fire_weapons(self, m):
        for si, w in enumerate(m.weapons):
            if w is None:
                continue
            w["cooldown"] = max(0.0, w["cooldown"] - DT)
            if w["kind"] == "gun":
                if w["reload"] > 0:
                    w["reload"] -= DT
                    if w["reload"] <= 0:
                        w["reload"], w["ammo"] = 0.0, w["stats"]["mag"]
                if m.fire[si] and w["cooldown"] <= 0 and w["ammo"] > 0 and w["reload"] <= 0:
                    st = w["stats"]
                    sd = math.radians(st["spread_deg"])
                    a = [m.aim[0] + self.rng.gauss(0, sd), m.aim[1] + self.rng.gauss(0, sd)]
                    d = self.dirv(a)
                    x, y, z = self.muzzle(m)
                    self.proj.append({"by": m.id, "pos": [x + d[0] * 3, y + d[1] * 3, z + d[2] * 3],
                                      "vel": [d[0] * st["speed"], d[1] * st["speed"], d[2] * st["speed"]],
                                      "left": st["range"], "dmg": st["damage"], "id": self.tick * 20 + m.id})
                    w["cooldown"], w["ammo"] = st["cooldown"], w["ammo"] - 1
                    self.sound("gun", m.pos, m.id)
                    if w["ammo"] == 0:
                        w["reload"] = st["reload"]
            elif w["kind"] == "laser":
                st = w["stats"]
                if m.fire[si] and w["cooldown"] <= 0:
                    if w.get("charge", 0) == 0:
                        self.sound("laser_charge", m.pos, m.id)
                    w["charge"] = w.get("charge", 0) + DT
                    if w["charge"] >= st["charge"] + st["hold_s"]:
                        self.laser(m, w)
                elif w.get("charge", 0) > 0:
                    if w["charge"] >= st["min_charge"]:
                        self.laser(m, w)
                    else:
                        w["charge"] += DT
            elif w["kind"] == "lightning":
                st = w["stats"]
                if m.fire[si] and w["cooldown"] <= 0 and m.energy >= st["energy"]:
                    m.energy -= st["energy"]
                    m.e_idle = 0.0
                    w["cooldown"] = st["cooldown"]
                    self.sound("lightning", m.pos, m.id)
                    x, y, z = self.muzzle(m)
                    best, bd = None, st["range"]
                    for o in self.mechs:
                        if o is m or not o.alive or (m.id != 0 and o.id != 0):
                            continue
                        d = math.dist(o.pos[:2], m.pos[:2])
                        b = math.atan2(o.pos[1] - y, o.pos[0] - x)
                        if d < bd and abs(wrap(b - m.aim[0])) < math.radians(st["cone_deg"]) / 2 and \
                                self.w.los(x, y, z, o.pos[0], o.pos[1], o.pos[2] + 4.5):
                            best, bd = o, d
                    if best:
                        self.damage(m, best, st["damage"], "lightning", m.pos)
                        if best.weapons[1] and best.weapons[1]["kind"] == "laser":
                            best.weapons[1]["charge"] = 0.0
                            best.weapons[1]["cooldown"] = max(best.weapons[1]["cooldown"], st["short_s"])

    def laser(self, m, w):
        st = w["stats"]
        f = min(1.0, w["charge"] / st["charge"])
        dmg = st["damage"] * (st["min_frac"] + (1 - st["min_frac"]) * f)
        w["charge"], w["cooldown"] = 0.0, st["cooldown"]
        self.sound("laser", m.pos, m.id)
        x, y, z = self.muzzle(m)
        d = self.dirv(m.aim)
        s = 3.0
        while s < st["range"]:
            px, py, pz = x + d[0] * s, y + d[1] * s, z + d[2] * s
            if self.w.blocked(px, py, pz):
                break
            for o in self.mechs:
                if o is not m and o.alive and (m.id == 0 or o.id == 0) and self.hit_cyl(o, px, py, pz, 0.0):
                    self.damage(m, o, dmg, "laser", m.pos)
                    self.beams.append({"by": m.id, "kind": "laser", "path": [[x, y, z], [px, py, pz]], "age": 0})
                    return
            s += 1.0

    def step_proj(self):
        keep = []
        for p in self.proj:
            x, y, z = p["pos"]
            vx, vy, vz = p["vel"]
            n = 6
            hit = False
            for k in range(1, n + 1):
                px, py, pz = x + vx * DT * k / n, y + vy * DT * k / n, z + vz * DT * k / n
                if self.w.blocked(px, py, pz):
                    hit = True
                    break
                for o in self.mechs:
                    if o.alive and o.id != p["by"] and (p["by"] == 0 or o.id == 0) and self.hit_cyl(o, px, py, pz):
                        self.damage(self.mechs[p["by"]], o, p["dmg"], "gun", (x, y, z))
                        hit = True
                        break
                if hit:
                    break
            p["left"] -= math.hypot(vx, vy, vz) * DT
            p["pos"] = [x + vx * DT, y + vy * DT, z + vz * DT]
            if not hit and p["left"] > 0:
                keep.append(p)
        self.proj = keep

    # ---------- observation for slot 0 ----------
    def sees(self, a, b):
        eye = (a.pos[0], a.pos[1], a.pos[2] + 8)
        d = math.dist(eye[:2], b.pos[:2])
        v = self.cfg["vision"]
        if d > v["range"]:
            return False
        if d > v["near_range"]:
            bb = math.atan2(b.pos[1] - a.pos[1], b.pos[0] - a.pos[0])
            if abs(wrap(bb - a.aim[0])) > math.radians(v["fov_deg"]) / 2:
                return False
        return self.w.los(*eye, b.pos[0], b.pos[1], b.pos[2] + 4.5, 2.0, 2.0) or \
            self.w.los(*eye, b.pos[0], b.pos[1], b.pos[2] + 8.8, 2.0, 2.0)

    def obs(self):
        me = self.mechs[0]
        mechs = []
        for o in self.mechs[1:]:
            if not o.alive:
                continue
            if self.sees(me, o):
                me.seen[o.id] = self.t
            if self.t - me.seen.get(o.id, -99) <= self.cfg["vision"]["memory_s"]:
                L = o.weapons[1]
                mechs.append({"id": o.id, "kind": "mech", "pos": o.pos, "vel": o.vel, "aim": o.aim, "hp": o.hp,
                              "max_hp": o.max_hp, "cubes": 0, "weapons": [w["kind"] for w in o.weapons if w],
                              "charging": bool(L and L["kind"] == "laser" and L.get("charge", 0) > 0)})
        eye = (me.pos[0], me.pos[1], me.pos[2] + 8)
        picks = [p for p in self.pickups if math.dist(p["pos"][:2], me.pos[:2]) < 200 and
                 (math.dist(p["pos"][:2], me.pos[:2]) < 45 or
                  abs(wrap(math.atan2(p["pos"][1] - me.pos[1], p["pos"][0] - me.pos[0]) - me.aim[0])) < math.pi / 3)
                 and self.w.los(*eye, p["pos"][0], p["pos"][1], p["pos"][2] + 0.5)]
        projs = [{"id": p["id"], "kind": "gun", "pos": p["pos"], "vel": p["vel"]} for p in self.proj
                 if p["by"] != 0 and math.dist(p["pos"], me.pos) < 200]
        sr = self.cfg["sound"]
        snd = []
        for kind, pos, t, by in self.sounds:
            if by == 0:
                continue
            d = math.dist(pos[:2], me.pos[:2])
            if d <= sr["range"].get(kind, 100):
                b = math.atan2(pos[1] - me.pos[1], pos[0] - me.pos[0]) + self.rng.gauss(0, math.radians(sr["bearing_noise_deg"]))
                snd.append({"kind": kind, "bearing": wrap(b), "dist": "near" if d < sr["near"] else "mid" if d < sr["mid"] else "far",
                            "age": round(self.t - t, 2)})
        z = self.zone()
        mc = self.cfg["mech"]
        stats = {k: mc[k] for k in ("walk_speed", "boost_speed", "turn_rate", "eye_height", "muzzle_height")}
        zd = math.dist(me.pos[:2], z["center"])
        return {"type": "obs", "tick": self.tick, "t": round(self.t, 2), "alive": sum(m.alive for m in self.mechs),
                "self": {"id": 0, "pos": me.pos, "vel": me.vel, "aim": me.aim, "hp": me.hp, "max_hp": me.max_hp,
                         "energy": me.energy, "max_energy": mc["energy"], "grounded": me.grounded, "cubes": 0,
                         "upgrades": [], "in_zone": zd <= z["radius"], "weapons": me.weapons, "stats": stats},
                "mechs": mechs, "projectiles": projs, "crates": [], "cubes": [], "pickups": picks, "sounds": snd,
                "beams": [], "events": self.events, "drops": [], "zone": z, "offer": None}

    # ---------- house bots ----------
    def hunters(self):
        me = self.mechs[0]
        killed = sum(1 for m in self.mechs[1:] if not m.alive and m.died_t is not None)
        n = 1 + killed + int(self.t // 60)
        if self.t > 300:
            n = 15
        return set(self.hunt_order[:n])

    def house(self, m, hunting):
        me = self.mechs[0]
        noise, leadf, react, takes_laser = TIER[self.tier[m.id]]
        x, y, z = m.pos
        mx, my, mz = self.muzzle(m)
        tx, ty = me.pos[0], me.pos[1]
        d = math.hypot(tx - x, ty - y)
        los = self.w.los(mx, my, mz, tx, ty, me.pos[2] + 4.5)
        engage = hunting or (d < 120 and los)
        zn = self.zone()
        # loot
        if takes_laser and not m.weapons[1]:
            for p in self.pickups:
                if p["kind"] in ("laser", "lightning") and math.dist(p["pos"][:2], (x, y)) < 4:
                    self.take(m, p)
                    break
        m.fire = [False, False]
        goal = None
        if engage and me.alive:
            if los:
                self.react[m.id] = self.react.get(m.id, 0) + 0.1
            else:
                self.react[m.id] = 0.0
            want = 40 if (m.weapons[1] and m.weapons[1]["kind"] == "lightning") else 80
            ux, uy = (tx - x) / max(d, 1e-6), (ty - y) / max(d, 1e-6)
            if not los or d > want + 15:
                goal = (ux, uy)
            elif d < want - 15:
                goal = (-ux, -uy)
            else:
                s = 1 if (int(self.t / 1.3) + m.id) % 2 else -1
                goal = (-uy * s, ux * s)
            lt = d / 252 * leadf
            ax, ay, az = tx + me.vel[0] * lt, ty + me.vel[1] * lt, me.pos[2] + 4.5
            m.cmd_aim = [math.atan2(ay - my, ax - mx) + self.rng.gauss(0, math.radians(noise)),
                         math.atan2(az - mz, math.hypot(ax - mx, ay - my)) + self.rng.gauss(0, math.radians(noise) * 0.5)]
            if los and self.react[m.id] >= react:
                err = abs(wrap(m.cmd_aim[0] - m.aim[0]))
                if d < 225 and err < 0.05:
                    m.fire[0] = True
                L = m.weapons[1]
                if L and L["kind"] == "laser":
                    if L.get("charge", 0) > 0:
                        m.fire[1] = L["charge"] < L["stats"]["charge"] or err > 0.02
                    elif L["cooldown"] <= 0 and d < 260:
                        m.fire[1] = True
                if L and L["kind"] == "lightning" and d < 42:
                    m.fire[1] = True
        else:
            cx, cy = zn["next_center"]
            dz = math.hypot(cx - x, cy - y)
            if dz > zn["next_radius"] * 0.6:
                goal = ((cx - x) / dz, (cy - y) / dz)
            elif takes_laser and not m.weapons[1]:
                near = [p for p in self.pickups if p["kind"] == "laser" and math.dist(p["pos"][:2], (x, y)) < 120]
                if near:
                    p = min(near, key=lambda p: math.dist(p["pos"][:2], (x, y)))
                    dd = math.dist(p["pos"][:2], (x, y))
                    goal = ((p["pos"][0] - x) / dd, (p["pos"][1] - y) / dd)
            if goal:
                m.cmd_aim = [math.atan2(goal[1], goal[0]), 0.0]
        # zone overrides
        zd = math.dist((x, y), zn["center"])
        if zd > zn["radius"] - 5:
            cx, cy = zn["center"]
            goal = ((cx - x) / max(zd, 1), (cy - y) / max(zd, 1))
        m.move = goal or (0.0, 0.0)
        # wall handling: rotate, else climb
        m.jump, m.boost = False, False
        if goal:
            gx, gy = goal
            if self.w.wall_ahead(x, y, z, gx, gy) is not None:
                for a in (0.6, -0.6, 1.2, -1.2, 1.8, -1.8):
                    ca, sa = math.cos(a), math.sin(a)
                    ex, ey = gx * ca - gy * sa, gx * sa + gy * ca
                    if self.w.wall_ahead(x, y, z, ex, ey) is None:
                        m.move = (ex, ey)
                        break
                else:
                    m.jump = m.grounded
                    m.boost = not m.grounded

    def take(self, m, p):
        st = self.cfg["weapons"][p["kind"]]
        m.weapons[1] = {"kind": p["kind"], "cooldown": max(1.0, st.get("cooldown", 0)), "stats": dict(st)}
        if p["kind"] == "laser":
            m.weapons[1]["charge"] = 0.0
        self.pickups.remove(p)

    # ---------- main loop ----------
    def apply(self, m, a):
        mv = a.get("move", [0, 0])
        m.move = (float(mv[0]), float(mv[1]))
        m.boost, m.jump = bool(a.get("boost")), bool(a.get("jump"))
        if "aim" in a:
            m.cmd_aim = [float(a["aim"][0]), float(a["aim"][1])]
        f = a.get("fire", [False, False])
        m.fire = [bool(f[0]), bool(f[1]) if len(f) > 1 else False]
        g = m.weapons[0]
        if a.get("reload") and g and g["kind"] == "gun" and g["reload"] <= 0 and g["ammo"] < g["stats"]["mag"]:
            g["reload"] = g["stats"]["reload"]
        if "take" in a:
            p = next((p for p in self.pickups if p["id"] == a["take"]), None)
            if p and math.dist(p["pos"][:2], m.pos[:2]) <= 4 and abs(p["pos"][2] - m.pos[2]) <= 4:
                if any(w and w["kind"] == p["kind"] for w in m.weapons):
                    return
                if m.weapons[1] is not None:
                    if a.get("drop") != 1:
                        return
                    old = m.weapons[1]
                    self.pickups.append({"id": self.next_pick, "kind": old["kind"], "pos": list(m.pos)})
                    self.next_pick += 1
                self.take(m, p)
                self.events.append({"type": "took", "id": p["id"], "kind": p["kind"], "slot": 1})

    def run(self):
        me = self.mechs[0]
        dur = self.cfg["match"]["duration_s"]
        while me.alive and self.t < dur and sum(m.alive for m in self.mechs) > 1:
            o = self.obs()
            self.events = []
            t0 = time.perf_counter()
            self.send(o)
            line = self.proc.stdout.readline()
            ms = (time.perf_counter() - t0) * 1000
            self.max_ms = max(self.max_ms, ms)
            if ms > 50:
                self.late += 1
            else:
                a = json.loads(line)
                if a.get("tick") == self.tick:
                    self.apply(me, a)
            hunt = self.hunters()
            for m in self.mechs[1:]:
                if m.alive:
                    self.house(m, m.id in hunt)
            for _ in range(2):
                self.t += DT
                self.tick += 1
                for m in self.mechs:
                    if m.alive:
                        self.step_mech(m)
                        self.fire_weapons(m)
                self.step_proj()
                z = self.zone()
                for m in self.mechs:
                    if not m.alive:
                        continue
                    if math.dist(m.pos[:2], z["center"]) > z["radius"]:
                        self.damage(None, m, z["dps"] * DT, "zone", m.pos)
                        m.last_dmg = self.t
                    elif self.t - m.last_dmg > self.cfg["mech"]["regen_delay"]:
                        m.hp = min(m.max_hp, m.hp + self.cfg["mech"]["regen_per_s"] * DT)
            self.sounds = [s for s in self.sounds if self.t - s[2] < 0.1]
        alive = [m for m in self.mechs if m.alive]
        if me.alive:
            place = 1 + sum(1 for m in alive if m.hp > me.hp)
        else:
            place = 1 + len(alive)
        try:
            self.send({"type": "end", "place": place, "kills": me.kills})
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()
        return {"seed": self.seed, "place": place, "t": round(self.t, 1), "kills": me.kills,
                "late": self.late, "max_ms": round(self.max_ms, 1)}


if __name__ == "__main__":
    k = sys.argv.index("--")
    print(json.dumps(Sim(sys.argv[1], int(sys.argv[2]), sys.argv[k + 1:]).run()), flush=True)
