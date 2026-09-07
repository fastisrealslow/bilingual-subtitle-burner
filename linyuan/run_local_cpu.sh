#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "用法: $0 <本地视频.mp4> <slug> [场合]" >&2
  exit 2
fi
command -v ffmpeg >/dev/null || { echo "缺少 ffmpeg" >&2; exit 2; }
command -v ollama >/dev/null || { echo "缺少 Ollama；请先安装并运行 ollama serve" >&2; exit 2; }
curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null || {
  echo "本机 Ollama 未启动" >&2
  exit 2
}

export TEXT_BACKEND=local
export LOCAL_LLM_URL=http://127.0.0.1:11434/api/chat
export LOCAL_LLM_MODEL=${LOCAL_LLM_MODEL:-qwen3:4b}
export LOCAL_FACE_MODEL_DIR=${LOCAL_FACE_MODEL_DIR:-/tmp/linyuan-face-models}
unset SILICONFLOW_API_KEY ALIYUN_AK ALIYUN_SK FC_REFILL

exec python3 "$(dirname "$0")/produce_cn.py" \
  --source "$1" --slug "$2" --speaker 林园 \
  --occasion "${3:-本地CPU生产}" --prefer-live-video --split-highlights
