"""Run many sim matches in parallel: python3 batch.py MAP_DIR SEEDS PROCS BOT.py [BOT2.py ...] -> mean (16-place) per bot."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))


def one(args):
    mp, seed, bot = args
    r = subprocess.run([sys.executable, os.path.join(HERE, "sim.py"), mp, str(seed), "--"] + ([sys.executable, "-u", bot] if bot.endswith(".py") else [bot]),
                       capture_output=True, text=True, timeout=900)
    try:
        return bot, json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return bot, {"seed": seed, "place": 16, "err": r.stderr[-300:]}


def ffa(mp, seed, base, var, k):
    slots = {(seed + j * 16 // k) % 16 for j in range(k)}
    lineup = [var if i in slots else base for i in range(16)]
    r = subprocess.run([sys.executable, os.path.join(HERE, "sim.py"), mp, str(seed), "--ffa"] + lineup,
                       capture_output=True, text=True, timeout=1800)
    try:
        r = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return [], [], r.stderr[-300:]
    pts = [16 - p for p in r["places"]]
    return [pts[i] for i in slots], [pts[i] for i in range(16) if i not in slots], None


if __name__ == "__main__" and sys.argv[4] == "--ffa":
    # python3 batch.py MAP SEEDS PROCS --ffa BASE VARIANT [K]: K variant copies among 16-K base copies, no house bots.
    mp, n, procs, base, var = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[5], sys.argv[6]
    k = int(sys.argv[7]) if len(sys.argv) > 7 else 4
    with ThreadPoolExecutor(procs) as ex:
        rs = list(ex.map(lambda s: ffa(mp, s, base, var, k), range(1, n + 1)))
    v = [x for r in rs for x in r[0]]
    b = [x for r in rs for x in r[1]]
    errs = [r[2] for r in rs if r[2]]
    sd = (sum((x - sum(v) / len(v)) ** 2 for x in v) / max(1, len(v) - 1)) ** 0.5 if v else 0
    print(f"{os.path.basename(var)} x{k} vs {os.path.basename(base)}: var {sum(v) / max(1, len(v)):.2f} (se {sd / max(1, len(v)) ** 0.5:.2f}, n {len(v)})"
          f"  base {sum(b) / max(1, len(b)):.2f}  errs {len(errs)}", flush=True)
    if errs:
        print(errs[0], flush=True)
elif __name__ == "__main__":
    mp, n, procs, bots = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
    jobs = [(mp, s, b) for s in range(1, n + 1) for b in bots]
    res = {b: [] for b in bots}
    with ThreadPoolExecutor(procs) as ex:
        for b, r in ex.map(one, jobs):
            res[b].append(r)
    for b in bots:
        rs = res[b]
        pts = [16 - r["place"] for r in rs]
        errs = [r for r in rs if "err" in r]
        print(f"{b}: mean {sum(pts) / len(pts):.2f} over {len(pts)}  kills {sum(r.get('kills', 0) for r in rs) / len(rs):.2f}"
              f"  late {sum(r.get('late', 0) for r in rs)}  errs {len(errs)}", flush=True)
        if os.environ.get("V"):
            print("  " + " ".join(f"{r['place']}/{r.get('t', 0):.0f}s/{r.get('kills', 0)}k[{r.get('who', '')}]" for r in rs), flush=True)
        if errs:
            print(errs[0], flush=True)
