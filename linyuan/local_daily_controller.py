#!/usr/bin/env python3
"""Build a cost-free six-video publishing plan from locally verified MP4s."""
import argparse
import json
from pathlib import Path

DAILY_TARGET = 6
DAILY_LIVE_MINIMUM = 5
DAILY_AUDIO_MAXIMUM = 1
RESERVE_DAYS = 2


def build_plan(audit, capacity=None, published_today=0):
    qualified = [row for row in audit.get('videos', []) if row.get('qualified')]
    live = [row for row in qualified if row.get('render_mode') == 'live_video_card']
    audio = [row for row in qualified if row.get('render_mode') == 'audio_card']
    other = [row for row in qualified if row.get('render_mode') not in {
        'live_video_card', 'audio_card'}]

    reserve_target = DAILY_TARGET * RESERVE_DAYS
    reserve_live_target = DAILY_LIVE_MINIMUM * RESERVE_DAYS
    selected = live[:DAILY_TARGET]
    if len(selected) < DAILY_TARGET:
        selected += audio[:min(DAILY_AUDIO_MAXIMUM, DAILY_TARGET - len(selected))]
    batch_ready = (
        len(selected) == DAILY_TARGET
        and sum(x.get('render_mode') == 'live_video_card' for x in selected)
        >= DAILY_LIVE_MINIMUM
    )
    preliminary = int((capacity or {}).get('candidate_count') or 0)
    remaining_today = max(0, DAILY_TARGET - int(published_today))

    blockers = []
    if len(live) < DAILY_LIVE_MINIMUM:
        blockers.append(f'本地合格真人动态片还差{DAILY_LIVE_MINIMUM - len(live)}条才能组成一日批次')
    if len(qualified) < DAILY_TARGET:
        blockers.append(f'本地合格成片还差{DAILY_TARGET - len(qualified)}条才能组成一日批次')
    if len(qualified) < reserve_target or len(live) < reserve_live_target:
        blockers.append('两日安全库存尚未达到12条，其中至少10条真人动态')

    return {
        'mode': 'local-offline-daily-plan',
        'cloud_calls': 0,
        'policy': {
            'daily_target': DAILY_TARGET,
            'daily_live_minimum': DAILY_LIVE_MINIMUM,
            'daily_audio_maximum': DAILY_AUDIO_MAXIMUM,
            'minimum_duration_sec': 120,
            'reserve_days': RESERVE_DAYS,
            'reserve_target': reserve_target,
            'reserve_live_target': reserve_live_target,
        },
        'today': {
            'published_qualified': int(published_today),
            'remaining': remaining_today,
        },
        'local_inventory': {
            'qualified': len(qualified),
            'live_video': len(live),
            'audio_card': len(audio),
            'unknown_mode': len(other),
            'preliminary_source_windows': preliminary,
            'reserve_deficit': max(0, reserve_target - len(qualified)),
            'reserve_live_deficit': max(0, reserve_live_target - len(live)),
        },
        'next_batch_ready': batch_ready,
        'publish_enabled': batch_ready,
        'selected_files': [x.get('file') for x in selected] if batch_ready else [],
        'blockers': blockers,
        'note': '初筛窗口不是合格成片；本控制器只允许已通过本地成片审核的文件进入发布批次。',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audit', type=Path)
    parser.add_argument('--capacity', type=Path)
    parser.add_argument('--published-today', type=int, default=0)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    capacity = json.loads(args.capacity.read_text()) if args.capacity else None
    result = build_plan(audit, capacity, args.published_today)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
