"""Audience-facing copy: remove known production wrappers, not source claims.

No model rewrite, invented firsthand experience, changed financial judgment,
removed provenance or suppressed synthetic-media/rights disclosure.
"""
import re

VERSION = 2026101202
_LEAD = re.compile(r'^本段讨论\s*[：:]\s*')
_SERIES = re.compile(
    r'^同场访谈分集：第 (\d+)/(\d+) 集'
    r'（各集独立成篇，发布以主页为准）\s*$')


def description(value, speaker='林园'):
    text = str(value or '').strip()
    if _LEAD.match(text):
        text = _LEAD.sub(speaker + '：', text, count=1)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = _SERIES.fullmatch(line)
        if match and 1 <= int(match[1]) <= int(match[2]) <= 20:
            lines[i] = f'第 {match[1]}/{match[2]} 集（已更新的片段见主页）'
            if i + 1 < len(lines) and lines[i + 1] == '分集目录：':
                lines[i + 1] = '这一组还有：'
    for i, line in enumerate(lines):
        nav = re.fullmatch(r'第 (\d+)/(\d+) 集（已更新的片段见主页）', line)
        if not nav or i + 1 >= len(lines) or lines[i + 1] != '这一组还有：':
            continue
        number, total = map(int, nav.groups())
        if not 1 <= number <= total or not 5 <= total <= 20:
            continue
        directory = lines[i + 2:i + 2 + total]
        episodes = []
        for expected, entry in enumerate(directory, 1):
            row = re.fullmatch(r'(\d+)\. (.+)', entry)
            if not row or int(row[1]) != expected or len(row[2]) > 160:
                break
            episodes.append(dict(episode_number=expected, title=row[2]))
        if len(episodes) == total:
            lines[i:i + 2 + total] = series_navigation(number, episodes).splitlines()
            break
    return '\n'.join(lines)


def series_navigation(number, episodes):
    # Long directories bury the answer on a phone. Adjacent approved titles
    # orient the viewer without inventing a hook or claiming future publication.
    if len(episodes) >= 5:
        nearby = []
        if number > 1:
            nearby.append('上一段：' + episodes[number - 2]['title'])
        if number < len(episodes):
            nearby.append('下一段：' + episodes[number]['title'])
        return (f'第 {number}/{len(episodes)} 集（已更新的片段见主页）\n'
                + '\n'.join(nearby))
    directory = '\n'.join(f"{e['episode_number']}. {e['title']}" for e in episodes)
    return (f'第 {number}/{len(episodes)} 集（已更新的片段见主页）\n'
            '这一组还有：\n' + directory)


def collection_copy(first_title, *, episode_titles=None):
    # Use the already verified title; never infer a new theme from keywords.
    title = str(first_title or '').replace('\n', ' ').strip()
    for prefix in ('林园：', '林园:'):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break
    heading = '林园｜' + (title or '访谈片段')
    if len(heading) > 50:
        heading = '林园｜访谈片段'  # Do not truncate a condition or financial claim.
    if episode_titles:
        # Only label a topic when EVERY published member explicitly mentions
        # it. This is navigation, not a new investment judgment or source date.
        topics = [('白酒生意', r'白酒|茅台|五粮液'),
                  ('医药与需求', r'医药|中药|慢病|慢性病|药企'),
                  ('公司与生意', r'公司|企业|生意'),
                  ('投资与持有', r'投资|持有|买入|卖出')]
        if len(heading) > 32:
            heading = '林园｜访谈片段'
        for label, pattern in topics:
            if all(re.search(pattern, t) for t in episode_titles):
                heading = '林园｜' + label
                break
        return heading, (f'这组有 {len(episode_titles)} 段，按分集顺序排列。'
                         '可以接着看，也可以单独看。')
    return heading, '把这几段放在一起，方便接着看。每段也可以单独观看。'
