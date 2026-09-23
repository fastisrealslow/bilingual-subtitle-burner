"""Read-only production diagnostics. Mother tasks and accepted clips stay separate."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
from pathlib import Path


def failure_category(reason):
    reason = str(reason or '')
    # The tracker includes the minimum face size in its diagnostic even when
    # zero frames failed that size check. Do not recommend higher resolution
    # for host cutaways / identity mismatches (actual library302).
    if '剩余帧即使全部匹配也达不到80%' in reason:
        small=re.search(r'人脸短边不足\d+px的帧数(\d+)',reason)
        return 'resolution' if small and int(small.group(1)) > 0 else 'identity'
    if '与已经发布的母片时间段重叠' in reason:
        return 'duplicate'
    # Operational failures must not be presented as evidence against footage.
    groups = (
        ('cloud_account_billing', ('Current user is in debt', '账户欠费', 'Account in debt')),
        ('service_or_timeout', ('不可用', '时间预算', '生产预算', 'timeout', 'Timeout',
                                'HTTP Error', '工作流步骤失败', 'LLM 调用')),
        ('identity', ('人物不一致', '人物身份', '未找到与林园参考照匹配', '发言归属', '其他嘉宾')),
        ('resolution', ('短边', '清晰度')),
        ('framing', ('取景', '角标', '原画', '人脸', '水印', '黑边', '黑色填充边')),
        # Missing legacy ranges are a ledger gap, not evidence of duplicate
        # content; title retries often also contain the words 完整观点.
        ('publication_history', ('同源历史缺少可核对的起止段', '母片内容哈希已改变')),
        # A pre-title motion rejection mentions 标题/字幕 only to explain work
        # avoided. It is still a visual failure, not a copy-generation error.
        ('motion', ('缺少持续局部动作', '动态证据不足', '疑似照片/背景板')),
        ('title', ('标题', '封面文案', '文案')),
        ('captions', ('字幕', '分屏', '断词')),
        ('selection', ('连续候选', '120秒', '完整观点', '选段')),
        ('duplicate', ('重复', '重叠', '冷却')),
    )
    return next((name for name, words in groups if any(w in reason for w in words)), 'unclassified')


def task_time(task):
    value = task.get('run_started_at')
    if value:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    return float((task.get('signature') or {}).get('ts') or 0)


def summarize(snapshot, since=None):
    cutoff = datetime.fromisoformat(since.replace('Z', '+00:00')) if since else None
    if cutoff and cutoff.tzinfo is None:
        raise ValueError('--since 必须包含时区，例如 2026-09-18T00:00:00+08:00')
    # Retain only the newest projection for each mother job.
    latest = {}
    for task in snapshot.get('tasks', []):
        slug = task.get('slug')
        if slug and (slug not in latest or task_time(task) >= task_time(latest[slug])):
            latest[slug] = task
    tasks = [t for t in latest.values() if not cutoff or task_time(t) >= cutoff.timestamp()]
    failed = [t for t in tasks if t.get('status') in ('failed', 'validation_failed')]
    categories = Counter(failure_category(t.get('detail')) for t in failed)
    return dict(
        snapshot_updated_at=snapshot.get('updated_at'), since=since,
        unit='mother_task_latest_state', tasks=len(tasks),
        statuses=dict(Counter(t.get('status', 'unknown') for t in tasks)),
        failures=dict(categories),
        examples=[dict(slug=t['slug'], category=failure_category(t.get('detail')),
                       reason=t.get('detail'), run_url=t.get('run_url')) for t in failed],
        inventory_snapshot=snapshot.get('inventory', {}),
        interpretation='任务状态不是成片率；complete可能表示已处理完毕。库存是全局快照，非所选时间段产量；publishable_now受时段和配额约束。',
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=Path(__file__).resolve().parents[1]/'site/production_status.json')
    parser.add_argument('--since')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = summarize(json.loads(args.snapshot.read_text()), args.since)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'examples'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
