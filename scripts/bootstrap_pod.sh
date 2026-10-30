#!/usr/bin/env bash
# Prepare a fresh Runpod "ComfyUI - CUDA 13.0" pod for the storyboard demo: Anima Turbo weights,
# Ollama + the planner LLM, and the start-up scripts. Runs in the background on the pod;
# progress goes to /workspace/bootstrap.log (ends with BOOTSTRAP_DONE).
# Usage: scripts/bootstrap_pod.sh <podid-xxxx@ssh.runpod.io> <ssh_key>
set -euo pipefail
TARGET=$1
KEY=$2

ssh -tt -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TARGET" > /dev/null 2>&1 <<'EOF'
cat > /workspace/bootstrap.sh <<'X'
set -e
M=/workspace/runpod-slim/ComfyUI/models
mkdir -p $M/diffusion_models $M/text_encoders $M/vae /workspace/hf /workspace/ollama
cd /workspace
hf download circlestone-labs/Anima split_files/diffusion_models/anima-turbo-v1.1.safetensors split_files/text_encoders/qwen_3_06b_base.safetensors split_files/vae/qwen_image_vae.safetensors --local-dir hf/anima
ln -sf /workspace/hf/anima/split_files/diffusion_models/anima-turbo-v1.1.safetensors $M/diffusion_models/
ln -sf /workspace/hf/anima/split_files/text_encoders/qwen_3_06b_base.safetensors $M/text_encoders/
ln -sf /workspace/hf/anima/split_files/vae/qwen_image_vae.safetensors $M/vae/
echo ANIMA_DONE

command -v zstd >/dev/null || (apt-get update -qq && apt-get install -y -qq zstd >/dev/null)
URL=$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest | grep -o '"browser_download_url": *"[^"]*ollama-linux-amd64\.tar\.zst"' | head -1 | cut -d'"' -f4)
curl -sL "$URL" | zstd -d | tar x -C /workspace/ollama
cat > /workspace/start_ollama.sh <<'Y'
#!/bin/bash
# Ollama lives on the persistent volume so it survives container restarts.
export OLLAMA_MODELS=/workspace/ollama/models OLLAMA_HOST=127.0.0.1:11434 OLLAMA_KEEP_ALIVE=-1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_NUM_PARALLEL=2
pgrep -f "ollama serve" >/dev/null || setsid nohup /workspace/ollama/bin/ollama serve > /workspace/ollama.log 2>&1 < /dev/null &
Y
bash /workspace/start_ollama.sh; sleep 4
OLLAMA_HOST=127.0.0.1:11434 /workspace/ollama/bin/ollama pull qwen3.6:35b > /workspace/pull_qwen.log 2>&1
echo BOOTSTRAP_DONE
X
setsid nohup bash /workspace/bootstrap.sh > /workspace/bootstrap.log 2>&1 < /dev/null &
sleep 2; exit
EOF
echo "bootstrap started on $TARGET; watch /workspace/bootstrap.log"
