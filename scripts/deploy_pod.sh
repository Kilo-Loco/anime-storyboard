#!/usr/bin/env bash
# Push this node pack to a Runpod ComfyUI pod and restart ComfyUI.
# Usage: scripts/deploy_pod.sh <comfy_url> <podid-xxxx@ssh.runpod.io> <ssh_key>
# Set STORYBOARD_KEY to the host key once the pod is in public (locked-down) mode.
# The SSH proxy is PTY-only and drops large stdin, so the tarball goes up through ComfyUI's
# /upload/image endpoint (into temp/) and SSH only runs short commands.
set -euo pipefail
URL=${1%/}
TARGET=$2
KEY=$3
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

COPYFILE_DISABLE=1 tar czf "$TMP/storyboard_pack.tgz" -C "$(dirname "$ROOT")" --exclude out --exclude __pycache__ --exclude fonts "$(basename "$ROOT")"
curl -sf -A deploy -H "X-Storyboard-Key: ${STORYBOARD_KEY:-}" -F "image=@$TMP/storyboard_pack.tgz" -F type=temp -F overwrite=true "$URL/upload/image" > /dev/null

ssh -tt -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TARGET" > "$TMP/ssh.log" 2>&1 <<'EOF'
C=/workspace/runpod-slim/ComfyUI; CN=$C/custom_nodes
tar xzf $C/temp/storyboard_pack.tgz -C $CN 2>/dev/null; echo "EXTRACT_RC=$?"
mkdir -p $CN/anime-storyboard/fonts; [ -s $CN/anime-storyboard/fonts/NotoSansJP-Bold.otf ] || wget -q -O $CN/anime-storyboard/fonts/NotoSansJP-Bold.otf https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/JP/NotoSansJP-Bold.otf
cd $C && source .venv-cu128/bin/activate && pip install -q -r $CN/anime-storyboard/requirements.txt
[ -f /workspace/start_ollama.sh ] && bash /workspace/start_ollama.sh
# Latent previews drive the sketch-to-final effect in the demo page; the args file makes start.sh keep it after a restart.
ARGS=/workspace/runpod-slim/comfyui_args.txt; grep -q preview-method $ARGS 2>/dev/null || echo "--preview-method auto" >> $ARGS
pkill -f "python main.py"; sleep 3
setsid nohup python main.py --listen 0.0.0.0 --port 8188 --enable-cors-header --preview-method auto > /workspace/comfy.log 2>&1 < /dev/null &
sleep 2; exit
EOF
grep -q "EXTRACT_RC=0" "$TMP/ssh.log" || { echo "extract failed:"; tail -20 "$TMP/ssh.log"; exit 1; }

for i in $(seq 1 60); do
  sleep 3
  if curl -s -A deploy "$URL/storyboard/config" | grep -q "\"pacing\""; then
    echo "deployed; nodes loaded after $((i * 3))s"; exit 0
  fi
done
echo "ComfyUI did not come back; check /workspace/comfy.log on the pod"; exit 1
