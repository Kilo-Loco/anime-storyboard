"""Queue the storyboard workflow on a remote ComfyUI and download the e-konte pages.

python3 scripts/run_storyboard.py https://<pod>-8188.proxy.runpod.net "premise" [--pacing action] [--plan examples/x.json]
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# STORYBOARD_KEY is the host key; pods in public mode reject /prompt and /history without it.
UA = {"User-Agent": "anime-storyboard/0.1", "Content-Type": "application/json",
      "X-Storyboard-Key": os.environ.get("STORYBOARD_KEY", "")}


def call(base, path, data=None, timeout=60):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("premise")
    ap.add_argument("--pacing", default="auto")
    ap.add_argument("--shots", type=int, default=0)
    ap.add_argument("--length", default="standard", choices=["short", "standard", "long"])
    ap.add_argument("--plan", help="plan_json file; skips the LLM")
    ap.add_argument("--llm", default="qwen3.6:35b")
    ap.add_argument("--model", default="anima-turbo-v1.1.safetensors")
    ap.add_argument("--out", default=str(ROOT / "out"))
    a = ap.parse_args()
    base = a.base.rstrip("/")

    wf = json.loads((ROOT / "workflows" / "storyboard_api.json").read_text())
    p = wf["planner"]["inputs"]
    p.update(premise=a.premise, pacing=a.pacing, length=a.length, shots=a.shots, llm=a.llm)
    if a.plan:
        p["plan_override"] = Path(a.plan).read_text()
    wf["unet"]["inputs"]["unet_name"] = a.model
    if "turbo" not in a.model:
        wf["sampler"]["inputs"].update(steps=30, cfg=4.0)

    t0 = time.time()
    resp = json.loads(call(base, "/prompt", {"prompt": wf}))
    if resp.get("node_errors"):
        raise SystemExit(json.dumps(resp["node_errors"], indent=1))
    pid = resp["prompt_id"]
    while True:
        h = json.loads(call(base, f"/history/{pid}")).get(pid)
        if h and h.get("status", {}).get("completed") or (h and h["status"].get("status_str") == "error"):
            break
        time.sleep(0.5)
    if h["status"].get("status_str") == "error":
        msgs = [m for m in h["status"].get("messages", []) if m[0] == "execution_error"]
        raise SystemExit(json.dumps(msgs, indent=1)[:4000])
    print(f"done in {time.time() - t0:.1f}s")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for node in ("save_sheet", "save_panels"):
        for img in h["outputs"].get(node, {}).get("images", []):
            q = urllib.parse.urlencode({k: img[k] for k in ("filename", "subfolder", "type")})
            data = urllib.request.urlopen(urllib.request.Request(f"{base}/view?{q}", headers=UA), timeout=120).read()
            (out / img["filename"]).write_bytes(data)
            print("saved", out / img["filename"])


if __name__ == "__main__":
    main()
