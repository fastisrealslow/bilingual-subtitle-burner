"""Audience-facing copy: remove known production wrappers, not source claims.

No model rewrite, invented firsthand experience, changed financial judgment,
removed provenance or suppressed synthetic-media/rights disclosure.
"""
import re

VERSION = 2026101201
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
    return '\n'.join(lines)


def series_navigation(number, episodes):
    directory = '\n'.join(f"{e['episode_number']}. {e['title']}" for e in episodes)
    return (f'第 {number}/{len(episodes)} 集（已更新的片段见主页）\n'
            '这一组还有：\n' + directory)


def collection_copy(first_title):
    # Use the already verified title; never infer a new theme from keywords.
    title = str(first_title or '').replace('\n', ' ').strip()
    for prefix in ('林园：', '林园:'):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break
    heading = '林园｜' + (title or '访谈片段')
    if len(heading) > 50:
        heading = '林园｜访谈片段'  # Do not truncate a condition or financial claim.
    return heading, '把这几段放在一起，方便接着看。每段也可以单独观看。'
