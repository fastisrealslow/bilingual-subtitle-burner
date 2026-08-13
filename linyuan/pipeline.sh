#!/usr/bin/env bash
# 全自动流水线：监控 → 下载 → 出片 → 投稿
#
# 设计要点
#   1. 抓取和下载必须连着跑 —— 微博直链只有 1 小时有效期
#   2. 每一步失败都不静默：健康巡检失败会告警，投稿前强制校验登录态
#   3. 投稿默认定时发布（+4h），留出人工检查和撤回的时间窗口
#
# 一次性准备：
#   python3 bili_login.py        # 扫码登录，凭据有效期数月
#
# 日常：
#   ./pipeline.sh                # 全流程
#   ./pipeline.sh --no-upload    # 只到出片，不投稿
set -uo pipefail
cd "$(dirname "$0")"

UPLOAD=1
[ "${1:-}" = "--no-upload" ] && UPLOAD=0

echo "═══ 1/6 接口健康巡检 ═══"
python3 healthcheck.py || echo "⚠️  接口异常，继续但结果可能不全（详见 RISKS.md）"

echo
echo "═══ 2/6 抓取全部源 ═══"
rm -f monitor_v2_state.json
python3 -u monitor_v2.py 2>&1 | grep -aE "模式|抓取成功|过滤|复用|新增:|导出|总计"

echo
echo "═══ 3/6 立即下载（趁直链未过期）═══"
python3 fetch_videos.py --all 2>&1 | tail -5

echo
echo "═══ 4/6 归档分类 ═══"
python3 organize_videos.py --apply 2>&1 | tail -2

echo
echo "═══ 5/6 选片 ═══"
python3 bridge_produce.py --limit 5 2>&1 | tail -20

if [ "$UPLOAD" = "0" ]; then
  echo
  echo "═══ 跳过投稿（--no-upload）═══"
  exit 0
fi

echo
echo "═══ 6/6 投稿 ═══"
if ! python3 bili_login.py --check; then
  echo "⚠️  未登录，跳过投稿。执行 python3 bili_login.py 扫码后重试"
  exit 0
fi

# deliver/ 下每个还没投过的成片各投一次
for d in deliver/*/; do
  slug=$(basename "$d")
  [ -f "$d/final.mp4" ] || continue
  python3 bili_upload.py --slug "$slug" || echo "  ✗ $slug 投稿失败"
done
