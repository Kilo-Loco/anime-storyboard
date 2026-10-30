"""Public demo mode: lets an audience use the storyboard page from their phones while the rest of
ComfyUI stays closed, and spreads boards across several pods.

Enabled by /workspace/storyboard_public.json:
    {"host_key": "<secret>", "backends": ["https://<pod>-8188.proxy.runpod.net", ...]}
Without that file the pod behaves like a normal open ComfyUI (handy for development).
"""
import asyncio
import json
import re
import secrets
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from comfy.cli_args import args
from server import PromptServer

from .planner import ANIMA_NEGATIVE, LENGTH_CHOICES, LLM_CHOICES, PACING_CHOICES, plan_storyboard, shot_prompt

CONFIG = Path("/workspace/storyboard_public.json")
WEB = Path(__file__).parent / "web"
UA = {"User-Agent": "storyboard-gateway/1.0"}  # the Runpod proxy rejects default client user agents

# Rough cost model for picking the least busy pod: an 8-cut plan holds the GPU ~17s, a panel ~1.4s.
PLAN_SECONDS, PANEL_SECONDS = 17, 1.4
AUDIENCE_CUTS = 8  # 30 people through 3 pods in ~5 minutes

BLOCKLIST = re.compile(r"\b(nude|nudity|naked|nsfw|porn\w*|sex\w*|hentai|loli\w*|gore|gory|decapitat\w*|dismember\w*|torture\w*)\b", re.I)

_cfg = {"mtime": None, "data": {}}
plan_lock = asyncio.Lock()  # Ollama runs this model one request at a time; the lock makes the line explicit
plan_jobs = {}          # job_id -> {"status": queued|planning|done|error, ...}
waiting = []            # job ids queued behind the lock, in order
boards = {}             # board_id -> board; boards planned on this pod
hidden = set()          # board ids the host hid from the wall (kept on the entry pod)
assigned = {}           # backend -> recent assignment times, so a burst of scans doesn't all land on one pod

routes = PromptServer.instance.routes


def config():
    try:
        mtime = CONFIG.stat().st_mtime
    except FileNotFoundError:
        return {}
    if mtime != _cfg["mtime"]:
        _cfg.update(mtime=mtime, data=json.loads(CONFIG.read_text()))
    return _cfg["data"]


def is_host(request):
    key = config().get("host_key")
    if not key:
        return True
    # Calls from inside the pod (e.g. /storyboard/render queueing its own prompt) come over loopback
    # without the proxy's forwarding headers.
    if request.remote in ("127.0.0.1", "::1") and "X-Forwarded-For" not in request.headers:
        return True
    supplied = request.cookies.get("sb_host") or request.headers.get("X-Storyboard-Key") or ""
    return secrets.compare_digest(supplied, key)


PUBLIC = {
    ("GET", "/storyboard"), ("GET", "/storyboard/wall"), ("GET", "/storyboard/host"), ("GET", "/storyboard/qr.svg"),
    ("GET", "/storyboard/config"), ("GET", "/storyboard/status"), ("GET", "/storyboard/assign"),
    ("GET", "/storyboard/feed"), ("GET", "/storyboard/hidden"),
    ("POST", "/storyboard/plan"), ("POST", "/storyboard/render"), ("GET", "/ws"),
}
PUBLIC_PATTERNS = [("GET", re.compile(r"/history/[0-9a-f-]{36}")), ("GET", re.compile(r"/storyboard/plan/[0-9a-f]{12}"))]


def public_ok(request):
    path = request.path[4:] if request.path.startswith("/api/") else request.path
    if (request.method, path) in PUBLIC:
        return True
    if request.method == "GET" and path == "/view":
        q = request.query
        return q.get("type") == "output" and q.get("subfolder", "").startswith("storyboard") and ".." not in q.get("filename", "")
    return any(request.method == m and p.fullmatch(path) for m, p in PUBLIC_PATTERNS)


@web.middleware
async def lockdown(request, handler):
    if public_ok(request) or is_host(request):
        return await handler(request)
    return web.Response(status=403, text="This pod only serves the storyboard demo.")


# Custom nodes load before the server starts, so the app's middleware list is still open here.
PromptServer.instance.app.middlewares.append(lockdown)


def status():
    queue = PromptServer.instance.prompt_queue.get_tasks_remaining()
    plans = len(waiting) + (1 if plan_lock.locked() else 0)
    return {"planning": plans, "queue": queue, "est_wait": round(plans * PLAN_SECONDS + queue * PANEL_SECONDS)}


def panel_graph(text, seed):
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "anima-turbo-v1.1.safetensors", "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": ANIMA_NEGATIVE, "clip": ["clip", 0]}},
        "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 1344, "height": 768, "batch_size": 1}},
        "sampler": {"class_type": "KSampler", "inputs": {
            "model": ["unet", 0], "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0],
            "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "storyboard/live"}},
    }


# ---------------- pages ----------------

def _page(name):
    return web.FileResponse(WEB / name, headers={"Cache-Control": "no-store"})


@routes.get("/storyboard")
async def storyboard_page(request):
    return _page("storyboard.html")


@routes.get("/storyboard/wall")
async def wall_page(request):
    return _page("wall.html")


@routes.get("/storyboard/host")
async def host_login(request):
    key = config().get("host_key", "")
    if not key or not secrets.compare_digest(request.query.get("key", ""), key):
        return web.Response(status=403, text="Wrong host key.")
    resp = web.HTTPFound(request.query.get("next", "/storyboard/wall"))
    resp.set_cookie("sb_host", key, max_age=7 * 24 * 3600, httponly=True, secure=True, samesite="Lax")
    raise resp


@routes.get("/storyboard/qr.svg")
async def qr_code(request):
    import qrcode
    import qrcode.image.svg

    url = f"https://{request.host}/storyboard"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return web.Response(body=img.to_string(), content_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ---------------- api ----------------

@routes.get("/storyboard/config")
async def demo_config(request):
    return web.json_response({
        "host": is_host(request), "public": bool(config().get("host_key")),
        "pacing": PACING_CHOICES, "llm": LLM_CHOICES, "length": LENGTH_CHOICES,
        "backends": config().get("backends") or [""],
    })


@routes.get("/storyboard/status")
async def pod_status(request):
    return web.json_response(status())


@routes.get("/storyboard/assign")
async def assign(request):
    """Pick the pod with the shortest estimated wait for a new board."""
    backends = config().get("backends") or [""]
    if len(backends) == 1:
        return web.json_response({"base": backends[0], "est_wait": status()["est_wait"]})

    async def probe(session, base):
        try:
            async with session.get(f"{base}/storyboard/status", timeout=aiohttp.ClientTimeout(total=5)) as r:
                return (await r.json())["est_wait"]
        except Exception:
            return None  # unreachable pods are skipped

    async with aiohttp.ClientSession(headers=UA) as session:
        waits = await asyncio.gather(*(probe(session, b) for b in backends))
    now = time.time()
    best = None
    for base, wait in zip(backends, waits):
        if wait is None:
            continue
        recent = [t for t in assigned.get(base, []) if now - t < 25]
        assigned[base] = recent
        score = wait + len(recent) * PLAN_SECONDS  # boards sent there that haven't started planning yet
        if best is None or score < best[1]:
            best = (base, score)
    if best is None:
        return web.json_response({"error": "No storyboard servers are reachable right now."}, status=503)
    assigned.setdefault(best[0], []).append(now)
    return web.json_response({"base": best[0], "est_wait": round(best[1])})


@routes.post("/storyboard/plan")
async def plan(request):
    """Start planning a board. Returns a job id at once; the page polls /storyboard/plan/<job_id>.
    (A plan can wait in line for minutes, longer than the pod proxy keeps a request open.)"""
    body = await request.json()
    premise = str(body.get("premise", "")).strip()[:200]
    if not premise:
        return web.json_response({"error": "Type a premise first."}, status=400)
    if BLOCKLIST.search(premise):
        return web.json_response({"error": "Let's keep it suitable for everyone. Try another premise!"}, status=400)
    host = is_host(request)
    args_ = dict(
        premise=premise,
        pacing=body.get("pacing") if body.get("pacing") in PACING_CHOICES else "auto",
        llm=body.get("llm") if body.get("llm") in LLM_CHOICES else LLM_CHOICES[0],
        seed=int(body.get("reroll", 0)),
        # The audience gets fixed-size boards so 30 people fit through the pods in a few minutes.
        shots=0 if host else AUDIENCE_CUTS,
        length=body.get("length", "standard"),
    )
    job_id = uuid.uuid4().hex[:12]
    plan_jobs[job_id] = {"status": "queued", "created": time.time()}
    waiting.append(job_id)
    asyncio.create_task(_run_plan(job_id, args_))
    for old in sorted(plan_jobs, key=lambda j: plan_jobs[j]["created"])[:-500]:
        del plan_jobs[old]
    return web.json_response({"job_id": job_id, "position": len(waiting)})


async def _run_plan(job_id, a):
    job = plan_jobs[job_id]
    try:
        async with plan_lock:
            waiting.remove(job_id)
            job["status"] = "planning"
            result = await asyncio.get_running_loop().run_in_executor(
                None, plan_storyboard, a["premise"], a["pacing"], a["shots"], a["llm"], a["seed"], a["length"])
    except Exception as e:
        if job_id in waiting:
            waiting.remove(job_id)
        job.update(status="error", error=f"Planning failed ({type(e).__name__}). Try again!")
        return
    board_id = uuid.uuid4().hex[:12]
    boards[board_id] = {
        "id": board_id, "created": time.time(), "premise": a["premise"], "title": result.title, "pacing": result.pacing,
        "prompts": [shot_prompt(result, s) for s in result.shots], "cuts": [None] * len(result.shots),
    }
    for old in sorted(boards, key=lambda b: boards[b]["created"])[:-200]:
        del boards[old]
    job.update(status="done", board_id=board_id, plan=result.model_dump())


@routes.get("/storyboard/plan/{job_id}")
async def plan_job(request):
    job = plan_jobs.get(request.match_info["job_id"])
    if job is None:
        return web.json_response({"error": "unknown plan"}, status=404)
    out = {k: v for k, v in job.items() if k != "created"}
    if job["status"] == "queued":
        out["position"] = waiting.index(request.match_info["job_id"]) + 1
    return web.json_response(out)


@routes.post("/storyboard/render")
async def render(request):
    """Queue one cut of a planned board. Prompts never come from the browser, only board id + cut."""
    body = await request.json()
    board = boards.get(str(body.get("board_id")))
    cut = body.get("cut")
    if board is None or not isinstance(cut, int) or not 0 <= cut < len(board["prompts"]):
        return web.json_response({"error": "unknown board or cut"}, status=400)
    payload = {"prompt": panel_graph(board["prompts"][cut], int(body.get("seed", 0)) % 2**32),
               "client_id": str(body.get("client_id", ""))[:64]}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{args.port}/prompt", json=payload) as r:
            out = await r.json()
    if "prompt_id" not in out:
        return web.json_response({"error": "render rejected"}, status=500)
    board["cuts"][cut] = out["prompt_id"]
    return web.json_response({"prompt_id": out["prompt_id"]})


def _image(prompt_id):
    if not prompt_id:
        return None
    entry = PromptServer.instance.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)
    try:
        img = entry["outputs"]["save"]["images"][0]
    except (TypeError, KeyError, IndexError):
        return None
    return f"/view?filename={img['filename']}&subfolder={img['subfolder']}&type=output&preview=webp;70"


@routes.get("/storyboard/feed")
async def feed(request):
    """Recent boards on this pod with whichever panels have finished, for the projector wall."""
    cutoff = time.time() - 3 * 3600
    recent = sorted((b for b in boards.values() if b["created"] > cutoff), key=lambda b: -b["created"])[:40]
    return web.json_response([{
        "id": b["id"], "created": b["created"], "premise": b["premise"], "title": b["title"], "pacing": b["pacing"],
        "panels": [_image(pid) for pid in b["cuts"]],
    } for b in recent])


@routes.get("/storyboard/hidden")
async def hidden_list(request):
    return web.json_response(sorted(hidden))


@routes.post("/storyboard/hide")
async def hide(request):
    # Not in PUBLIC, so the lockdown middleware only lets the host through.
    body = await request.json()
    (hidden.add if body.get("hidden", True) else hidden.discard)(str(body.get("board_id")))
    return web.json_response(sorted(hidden))
