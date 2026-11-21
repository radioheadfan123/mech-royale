"""Run many sim matches in parallel: python3 batch.py MAP_DIR SEEDS PROCS BOT.py [BOT2.py ...] -> mean (16-place) per bot."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))


def one(args):
    mp, seed, bot = args
    r = subprocess.run([sys.executable, os.path.join(HERE, "sim.py"), mp, str(seed), "--", sys.executable, "-u", bot],
                       capture_output=True, text=True, timeout=900)
    try:
        return bot, json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return bot, {"seed": seed, "place": 16, "err": r.stderr[-300:]}


if __name__ == "__main__":
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
            print("  " + " ".join(f"{r['place']}/{r.get('t', 0):.0f}s/{r.get('kills', 0)}k" for r in rs), flush=True)
        if errs:
            print(errs[0], flush=True)
