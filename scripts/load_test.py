"""Simulate an audience: N phones each make one short storyboard through the public endpoints only.
Also a good way to warm every pod before the demo.

python3 scripts/load_test.py https://<entry-pod>-8188.proxy.runpod.net [--phones 9]
"""
import argparse
import json
import threading
import time
import urllib.request
from collections import Counter

PREMISES = [
    "a robot learns to bake bread", "two rivals meet again at a train station in the rain",
    "a girl finds a stray cat on the school roof", "the vending machine at school is haunted",
    "final serve of the volleyball championship", "a letter arrives ten years too late",
    "a delivery girl races a dragon across the rooftops", "a ghost tries to pass a math exam",
    "the last day before summer break", "a samurai guards a convenience store at midnight",
]
HEADERS = {"User-Agent": "storyboard-loadtest/1.0", "Content-Type": "application/json"}


def call(url, body=None, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def phone(entry, i, results):
    t0 = time.time()
    try:
        a = call(f"{entry}/storyboard/assign")
        base = a["base"]
        job = call(f"{base}/storyboard/plan", {"premise": PREMISES[i % len(PREMISES)], "pacing": "auto"})
        while (plan := call(f"{base}/storyboard/plan/{job['job_id']}"))["status"] not in ("done", "error"):
            time.sleep(1)
        if plan["status"] == "error":
            raise RuntimeError(plan["error"])
        t_plan = time.time() - t0
        cuts = plan["plan"]["shots"]
        pids = [call(f"{base}/storyboard/render", {"board_id": plan["board_id"], "cut": c, "seed": 424242, "client_id": f"lt{i}"})["prompt_id"]
                for c in range(len(cuts))]
        for pid in pids:  # wait for every cut, polling history like the page does on completion
            while not call(f"{base}/history/{pid}").get(pid, {}).get("outputs"):
                time.sleep(1)
        results[i] = dict(pod=base.split("//")[1][:6], cuts=len(cuts), plan=t_plan, total=time.time() - t0, wait=a["est_wait"])
    except Exception as e:
        results[i] = dict(error=f"{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entry")
    ap.add_argument("--phones", type=int, default=9)
    a = ap.parse_args()
    entry = a.entry.rstrip("/")
    results = {}
    threads = [threading.Thread(target=phone, args=(entry, i, results)) for i in range(a.phones)]
    t0 = time.time()
    for t in threads:
        t.start()
        time.sleep(0.3)  # people don't all tap at the exact same millisecond
    for t in threads:
        t.join()
    for i in sorted(results):
        r = results[i]
        print(f"phone {i:2d}: " + (r["error"] if "error" in r else
              f"pod {r['pod']}  {r['cuts']:2d} cuts  plan {r['plan']:5.1f}s  done {r['total']:5.1f}s  (est wait {r['wait']}s)"))
    ok = [r for r in results.values() if "error" not in r]
    print(f"\n{len(ok)}/{a.phones} boards in {time.time() - t0:.1f}s; per pod: {dict(Counter(r['pod'] for r in ok))}")


if __name__ == "__main__":
    main()
