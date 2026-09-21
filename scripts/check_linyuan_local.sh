#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$project_root"
python_bin=${PYTHON_BIN:-"$project_root/.venv311/bin/python"}
if [[ ! -x "$python_bin" ]]; then
  echo "先按 docs/LOCAL_DEVELOPMENT.md 创建 .venv311，或设置 PYTHON_BIN。" >&2
  exit 2
fi
export OPENCV_VIDEOIO_PRIORITY_LIST=FFMPEG
"$python_bin" -m pytest -q \
  tests/test_linyuan_editorial_iteration.py \
  tests/test_linyuan_cover_conditions.py \
  tests/test_linyuan_live_card_theme.py \
  tests/test_linyuan_portrait_window.py \
  tests/test_linyuan_title_claims.py \
  tests/test_linyuan_title_batch_regressions.py \
  tests/test_linyuan_headline_policy.py \
  tests/test_linyuan_editorial_policy.py \
  tests/test_linyuan_production_history.py \
  tests/test_linyuan_source_selection.py \
  tests/test_linyuan_presentation.py \
  tests/test_linyuan_source_priority.py \
  tests/test_linyuan_runner_dispatch.py \
  tests/test_linyuan_title_model_lab.py \
  tests/test_linyuan_source100_ab.py \
  tests/test_linyuan_dispatch_retry.py \
  tests/test_linyuan_batch_isolation.py \
  tests/test_linyuan_visual_before_copy.py "$@"
