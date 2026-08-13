#!/usr/bin/env bash
# 一键：抓取 → 立即下载 → 归档
#
# 为什么要串起来：微博直链只有 1 小时有效期，抓完必须立刻下载，
# 中间隔太久就全部 403。这个脚本保证「抓完即下」。
set -u
cd "$(dirname "$0")"

echo "═══ 1/5 接口健康巡检 ═══"
# 先探接口——静默失败（返回成功但零数据）不检查发现不了
if ! python3 healthcheck.py; then
  echo "⚠️  接口异常，仍继续抓取，但结果可能不完整。详见 RISKS.md"
fi

echo
echo "═══ 2/5 抓取全部源 ═══"
rm -f monitor_v2_state.json
python3 -u monitor_v2.py 2>&1 | grep -aE "模式|抓取成功|过滤|复用|新增:|导出|总计"

echo
echo "═══ 3/5 立即下载（趁直链未过期）═══"
python3 fetch_videos.py --all 2>&1 | tail -5

echo
echo "═══ 4/5 归档分类 ═══"
python3 organize_videos.py --apply 2>&1 | tail -2

echo
echo "═══ 5/5 视频库统计 ═══"
python3 - <<'PY'
from pathlib import Path
tot = n = 0
for d in sorted(Path('videos').iterdir()):
    if d.is_dir():
        fs = list(d.glob('*.mp4'))
        s = sum(f.stat().st_size for f in fs)
        tot += s; n += len(fs)
        print(f"  {d.name:20} {len(fs):4} 个  {s/1073741824:6.2f}GB")
print(f"  {'合计':20} {n:4} 个  {tot/1073741824:6.2f}GB")
PY
