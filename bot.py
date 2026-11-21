"""Mech Royale bot: stay in the zone, loot a laser, fight with leading aim, face threats, retreat to cover."""
import math
import random

import mechfight as mf
from world import Nav, World

TAU = 2 * math.pi
WANT = {"laser": 3, "lightning": 2}  # slot-1 weapons worth taking; mortar never (cook-off bait)
PICKS = ["armor", "plating", "nanites", "gun_rate", "gun_damage", "laser_charge", "laser_damage",
         "lightning_efficiency", "gyros", "gun_mag", "servos", "gun_velocity", "capacitor", "reactor",
         "jets", "sensors", "lightning_range", "lightning_chain", "laser_pierce"]
LOUD = {"gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet", "reload"}


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def norm(x, y):
    d = math.hypot(x, y)
    return (x / d, y / d) if d > 1e-9 else (0.0, 0.0)


class Bot:
    name = "ponytail"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.w = World(hello["map"]["dir"])
        self.nav = Nav(self.w)
        self.rng = random.Random(hello["seed"])
        self.strafe, self.flip_t = 1, 0.0
        self.threat = None          # (bearing, t, by)
        self.stuck_t, self.climb_until = 0.0, -1.0
        self.side = 1               # preferred detour side
        self.last_t = 0.0
        self.charging_since = None
        self.heal = False

    # ---------- helpers ----------
    def center_z(self, m):
        if m["kind"] == "npc":
            return m["pos"][2] + self.cfg["npc"]["height"] / 2
        return m["pos"][2] + 4.5 * (1 + 0.05 * m.get("cubes", 0))

    def lead(self, mx, my, mz, m, speed, delay=0.05):
        px, py, pz = m["pos"]
        vx, vy, _ = m["vel"]
        cz = self.center_z(m)
        tt = delay
        for _ in range(3):
            tx, ty = px + vx * tt, py + vy * tt
            tt = delay + math.dist((tx, ty, cz), (mx, my, mz)) / speed
        return px + vx * tt, py + vy * tt, cz

    def safe_goal(self, obs, x, y):
        z = obs["zone"]
        cx, cy = z["next_center"]
        r = z["next_radius"]
        margin = min(25.0, 0.35 * r)
        d = math.hypot(x - cx, y - cy)
        if d <= r - margin:
            return None, d, r
        ux, uy = norm(cx - x, cy - y)
        k = d - (r - margin) * 0.6
        return (x + ux * k, y + uy * k), d, r

    def steer(self, x, y, z, dx, dy):
        """Rotate the wanted direction around walls. Returns (dx, dy, blocked)."""
        if self.w.wall_ahead(x, y, z, dx, dy) is None:
            return dx, dy, False
        for a in (0.45, 0.9, 1.35, 1.8, 2.3):
            for s in (self.side, -self.side):
                ca, sa = math.cos(a * s), math.sin(a * s)
                ex, ey = dx * ca - dy * sa, dx * sa + dy * ca
                if self.w.wall_ahead(x, y, z, ex, ey) is None:
                    self.side = s
                    return ex, ey, False
        return dx, dy, True

    def choose(self, offer):
        ids = [c["id"] for c in offer["choices"]]
        rank = {u: i for i, u in enumerate(PICKS)}
        return min(range(len(ids)), key=lambda i: rank.get(ids[i], 99))

    # ---------- main ----------
    def act(self, obs):
        t = obs["t"]
        dt = max(0.05, t - self.last_t)
        self.last_t = t
        me = obs["self"]
        x, y, z = me["pos"]
        st = me["stats"]
        eye = z + st.get("eye_height", 8)
        mz = z + st.get("muzzle_height", 7)
        hpf = me["hp"] / me["max_hp"]
        energy = me["energy"]
        act = {}
        if obs.get("offer"):
            act["pick"] = self.choose(obs["offer"])

        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None and e.get("bearing") is not None:
                self.threat = (e["bearing"], t, e["by"])

        weapons = me["weapons"]
        slot = {w["kind"]: i for i, w in enumerate(weapons) if w}

        # ----- loot within reach -----
        for p in obs["pickups"]:
            if math.dist(p["pos"][:2], (x, y)) < self.cfg["loot"]["pickup_radius"] - 0.3 and abs(p["pos"][2] - z) < 3.8:
                k = p["kind"]
                if k in slot or k not in WANT:
                    continue
                if weapons[1] is None:
                    act["take"] = p["id"]
                elif WANT.get(weapons[1]["kind"], 0) < WANT[k] and not weapons[1].get("charge"):
                    act["take"], act["drop"] = p["id"], 1
                break

        # ----- pick a target -----
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        threat_id = self.threat[2] if self.threat and t - self.threat[1] < 4 else None
        best, best_s = None, 1e9
        for m in enemies:
            d = math.dist(m["pos"], me["pos"])
            vis = self.w.los(x, y, mz, m["pos"][0], m["pos"][1], self.center_z(m))
            s = d + m.get("hp", 1400) / 20 - (60 if m["id"] == threat_id else 0) + (0 if vis else 150)
            if s < best_s:
                best, best_s, best_vis, best_d = m, s, vis, d
        farm = None
        if (best is None or best_d > 120) and hpf > 0.35 and not self.heal:
            cands = [m for m in obs["mechs"] if m["kind"] == "npc"] + [dict(c, kind="crate") for c in obs["crates"]]
            fs = 1e9
            for c in cands:
                d = math.dist(c["pos"], me["pos"])
                if d > 220:
                    continue
                cz = c["pos"][2] + (1.2 if c["kind"] == "crate" else 1.75)
                if self.w.los(x, y, mz, c["pos"][0], c["pos"][1], cz) and d < fs:
                    farm, fs = c, d

        # ----- aim -----
        fire = [False, False]
        aim = None
        tgt = farm if farm is not None else best
        if tgt is not None:
            gun = weapons[slot["gun"]]["stats"] if "gun" in slot else None
            if tgt.get("kind") == "crate":
                tx, ty, tz = tgt["pos"][0], tgt["pos"][1], tgt["pos"][2] + 1.2
                hx, hy, hz = tx, ty, tz
                trad = 1.4
            else:
                tx, ty, tz = self.lead(x, y, mz, tgt, gun["speed"] if gun else 252)
                hx, hy, hz = self.lead(x, y, mz, tgt, 1e9, delay=0.1)  # hitscan point
                trad = 2.5 * (1 + 0.05 * tgt.get("cubes", 0)) if tgt["kind"] == "mech" else 1.8
            d = math.dist((tx, ty, tz), (x, y, mz))
            gun_aim = [math.atan2(ty - y, tx - x), math.atan2(tz - mz, math.hypot(tx - x, ty - y))]
            hit_aim = [math.atan2(hy - y, hx - x), math.atan2(hz - mz, math.hypot(hx - x, hy - y))]
            ay, ap = me["aim"]
            slew = st.get("turn_rate", 4) * 0.05
            tol = math.atan2(trad * 0.7, max(d, 1.0))

            def err(a):
                return max(0.0, math.hypot(wrap(a[0] - ay), a[1] - ap) - slew)

            vis = self.w.los(x, y, mz, tx, ty, tz)
            aim = gun_aim
            # laser: charge while in sight, release on target when full
            if "laser" in slot:
                L = weapons[slot["laser"]]
                ls = L["stats"]
                if L.get("charge", 0) > 0:
                    aim = hit_aim
                    full = L["charge"] >= ls.get("charge", 0.75) - 0.05
                    on = err(hit_aim) < tol and self.w.los(x, y, mz, hx, hy, hz)
                    fire[slot["laser"]] = not (full and on)
                elif L["cooldown"] <= 0 and d < ls.get("range", 280) * 0.95 and vis:
                    fire[slot["laser"]] = True
            if "lightning" in slot:
                ls = weapons[slot["lightning"]]["stats"]
                if d < ls.get("range", 45) * 0.95 and vis and energy > 12 and \
                        abs(wrap(gun_aim[0] - ay)) < math.radians(ls.get("cone_deg", 30)) * 0.4:
                    fire[slot["lightning"]] = True
            if gun and vis and d < gun["range"] * 0.97 and err(aim) < tol:
                g = weapons[slot["gun"]]
                if g.get("ammo", 1) > 0 and g.get("reload", 0) <= 0:
                    fire[slot["gun"]] = True
        if aim is None:
            if self.threat and t - self.threat[1] < 3:
                aim = [self.threat[0], 0.0]
            else:
                snd = [s for s in obs["sounds"] if s["kind"] in LOUD and s["dist"] != "far"]
                if snd:
                    aim = [snd[0]["bearing"], 0.0]
        g = weapons[slot["gun"]] if "gun" in slot else None
        if g and best is None and g.get("reload", 0) <= 0 and g.get("ammo", 99) < g["stats"]["mag"] * 0.6:
            act["reload"] = True

        # ----- movement -----
        boost = False
        goal, zd, zr = self.safe_goal(obs, x, y)
        zn = obs["zone"]
        self.nav.target(zn["next_center"][0], zn["next_center"][1], zn["next_radius"] * 0.6)
        self.nav.work(0.004)
        mv = (0.0, 0.0)
        if t >= self.flip_t:
            self.strafe = -self.strafe
            self.flip_t = t + self.rng.uniform(0.6, 1.6)
        dodge = any(m.get("charging") and abs(wrap(m["aim"][0] - math.atan2(y - m["pos"][1], x - m["pos"][0]))) < 0.15
                    for m in enemies)
        if best is not None and farm is None:
            ex, ey = best["pos"][0], best["pos"][1]
            ux, uy = norm(ex - x, ey - y)
            d = math.hypot(ex - x, ey - y)
            ehp = best.get("hp", 1400) / best.get("max_hp", 1400)
            if hpf < 0.45 and ehp > 0.3:
                self.heal = True
            if self.heal and (hpf > 0.9 or ehp < 0.2):
                self.heal = False
            if self.heal:
                mv = self.cover(x, y, z, best, goal)
                boost = True
            else:
                want = 30 if "lightning" in slot else 70
                radial = max(-1.0, min(1.0, (d - want) / 25))
                px, py = -uy * self.strafe, ux * self.strafe
                mv = norm(px + ux * radial * 0.7, py + uy * radial * 0.7)
                boost = energy > 35 or dodge
        elif farm is not None:
            fx, fy = farm["pos"][0], farm["pos"][1]
            d = math.hypot(fx - x, fy - y)
            if d > 35:
                mv = norm(fx - x, fy - y)
        else:
            tgo = self.loot_goal(obs, x, y, z, slot, weapons)
            if tgo:
                mv = norm(tgo[0] - x, tgo[1] - y)
        if self.heal and hpf > 0.9:
            self.heal = False
        if goal is not None:
            gx, gy = self.nav.step(x, y) or norm(goal[0] - x, goal[1] - y)
            outside = zd > zr
            w = 1.0 if (outside or best is None) else 0.6
            mv = norm(mv[0] * (1 - w) + gx * w, mv[1] * (1 - w) + gy * w)
            if not me["in_zone"]:
                boost = True
        if best is None and farm is None and goal is None and mv == (0.0, 0.0):
            mv = (0.0, 0.0)  # inside the safe circle, nothing to do: stand still (silent)

        # ----- grab visible cubes (crate/drone/kill drops) -----
        cbs = [c for c in obs["cubes"] if math.dist(c["pos"][:2], (x, y)) < 300]
        if cbs and me["in_zone"] and not self.heal and (best is None or best_d > 30):
            cb = min(cbs, key=lambda c: math.dist(c["pos"][:2], (x, y)))
            mv = norm(cb["pos"][0] - x, cb["pos"][1] - y)
        # ----- supply drop: full HP + 3 cubes for holding the ring alone 4 s -----
        dps = [d for d in obs.get("drops", []) if d.get("holder") in (None, me["id"])
               and math.dist(d["pos"][:2], (x, y)) < 260]
        if dps:
            dp = min(dps, key=lambda d: math.dist(d["pos"][:2], (x, y)))
            ddx, ddy = dp["pos"][0] - x, dp["pos"][1] - y
            dd = math.hypot(ddx, ddy)
            if dd > dp.get("radius", 8) - 3:
                mv = norm(ddx, ddy)
                boost = boost or (energy > 25 and dd > 20)
            elif best is None:
                mv = (0.0, 0.0)
        jump = False
        if mv != (0.0, 0.0):
            dx, dy, blocked = self.steer(x, y, z, mv[0], mv[1])
            hs = math.hypot(me["vel"][0], me["vel"][1])
            self.stuck_t = self.stuck_t + dt if hs < 2.0 else 0.0
            if blocked or self.stuck_t > 0.6:
                self.climb_until = t + 2.5
                dx, dy = mv
            if t < self.climb_until and energy > 4:
                jump = me["grounded"]
                boost = not me["grounded"]
            mv = (dx, dy)
        if aim is None:
            aim = [math.atan2(mv[1], mv[0]) if mv != (0.0, 0.0) else me["aim"][0], 0.0]
        act.update(move=[mv[0], mv[1]], aim=aim, fire=fire, boost=boost, jump=jump)
        return act

    def cover(self, x, y, z, foe, goal):
        """Pick a nearby spot the foe can't see, preferring away from it and inside the zone."""
        fx, fy, fz = foe["pos"]
        fe = fz + 8
        best, bs = norm(x - fx, y - fy), -1e9
        for k in range(12):
            a = k * TAU / 12
            cx, cy = x + 22 * math.cos(a), y + 22 * math.sin(a)
            if self.w.blocked(cx, cy, z + 2):
                continue
            s = math.hypot(cx - fx, cy - fy) * 0.5
            if not self.w.los(fx, fy, fe, cx, cy, self.w.gz(cx, cy) + 6):
                s += 100
            if goal is not None:
                s -= math.hypot(cx - goal[0], cy - goal[1]) * 0.3
            if s > bs:
                bs, best = s, norm(cx - x, cy - y)
        return best

    def loot_goal(self, obs, x, y, z, slot, weapons):
        cur = WANT.get(weapons[1]["kind"], 0) if weapons[1] else -1
        best, bd = None, 1e9
        for p in obs["pickups"]:
            if p["kind"] in WANT and p["kind"] not in slot and WANT[p["kind"]] > cur:
                d = math.dist(p["pos"][:2], (x, y))
                if d < 120 and d < bd:
                    best, bd = p["pos"], d
        for c in obs["cubes"]:
            d = math.dist(c["pos"][:2], (x, y))
            if d < 60 and d * 1.5 < bd:
                best, bd = c["pos"], d * 1.5
        return best


if __name__ == "__main__":
    mf.run(Bot)
