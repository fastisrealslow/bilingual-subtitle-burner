#!/usr/bin/env bash
# Run only from linyuan/; preserve concurrent state writes via ordinary rebase.
set -euo pipefail
test -f monitor_v2.py
test -n "${GITHUB_REF_NAME:-}"
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add -A -- dashboard/data.json monitor_v2.db up_videos.json *_seeds.json
for report in .automation/source_gap_audit.json .automation/source_research_state.json .automation/source_research_report.json; do
  if [ -f "$report" ]; then git add -f "$report"; fi
done
if git diff --staged --quiet; then
  echo '无变更'
  exit 0
fi
git commit -m "${1:-chore(monitor): checkpoint source metadata}"
for attempt in 1 2 3 4 5; do
  if git pull --rebase --autostash origin "$GITHUB_REF_NAME" && git push origin "HEAD:$GITHUB_REF_NAME"; then
    exit 0
  fi
  echo "推送冲突，第 $attempt 次重试"
  sleep "$((attempt * 5))"
done
echo '::error::元数据提交失败，保留日志与补库证据。'
exit 1
