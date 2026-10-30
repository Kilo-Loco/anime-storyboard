# 絵コンテ Anime Storyboard

Type one line, get a timed anime storyboard. A local LLM (Qwen 3.6 via Ollama) plans the cuts and pacing; [Anima Turbo](https://huggingface.co/circlestone-labs/Anima) draws every panel in ComfyUI. Everything open-weight, one GPU.

<img src="assets/repo-qr.svg" width="200" alt="QR code linking to github.com/Kilo-Loco/anime-storyboard">

Scan to open this repo.

## Try it

You need a [Runpod](https://runpod.io) account with an SSH key added (Settings → SSH Public Keys).

1. **Create a pod** from the **"ComfyUI - CUDA 13.0"** template on a 48GB GPU (L40S, RTX 6000 Ada or A40), with a 60GB volume.
2. **Install models** (~28GB; takes a few minutes, done when `/workspace/bootstrap.log` ends in `BOOTSTRAP_DONE`):
   ```bash
   scripts/bootstrap_pod.sh <pod-ssh-user>@ssh.runpod.io ~/.ssh/id_ed25519
   ```
3. **Deploy the node pack:**
   ```bash
   scripts/deploy_pod.sh https://<pod-id>-8188.proxy.runpod.net <pod-ssh-user>@ssh.runpod.io ~/.ssh/id_ed25519
   ```
4. **Open** `https://<pod-id>-8188.proxy.runpod.net/storyboard`, type a premise, press Enter.

The SSH user is in the pod's **Connect** tab (`ssh <pod-ssh-user>@ssh.runpod.io`).

## Audience mode (QR code)

To let a room join from their phones, put this in `/workspace/storyboard_public.json` on each pod (list every pod you run):

```json
{"host_key": "<make-up-a-secret>", "backends": ["https://<pod-id>-8188.proxy.runpod.net"]}
```

Show `/storyboard/wall` on the projector. Phones only get the storyboard page; the rest of ComfyUI is locked unless you open `/storyboard/host?key=<your-secret>` first. One pod handles ~2 boards/min; three handle a room of 30.

## In ComfyUI

The pack adds **Storyboard Planner (Ollama)** and **E-Konte Storyboard Sheet** nodes; load `workflows/storyboard_api.json` for the full graph.
