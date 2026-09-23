"""Check the context of explicit population counts and annual percentages.

These checks bind to matching source quantities, not a date elsewhere in a
talk. They supplement semantic review and do not verify the speaker's facts.
"""
import re

NUMBER = r'[零〇一二两三四五六七八九十百千0-9]+'
ANNUAL = r'每年|(?<![零〇一二两三四五六七八九十百千0-9])年(?:复合|符合)?增长|年均|年化'


def number(raw):
    digits = {c: str(i) for i, c in enumerate('零一二三四五六七八九')}
    raw = raw.replace('〇', '零').replace('两', '二')
    if raw.isdigit():
        return int(raw)
    if all(c in digits for c in raw):
        return int(''.join(digits[c] for c in raw))
    total = current = 0
    for c in raw:
        if c in digits:
            current = int(digits[c])
        elif c in '十百千':
            total += (current or 1) * {'十': 10, '百': 100, '千': 1000}[c]
            current = 0
        else:
            return None
    return total + current


def clean(text):
    return re.sub(r'[^\u4e00-\u9fff0-9%％.]', '', str(text))


def population_contexts(source):
    text = clean(source)
    result = []
    for match in re.finditer(rf'({NUMBER})亿', text):
        before = text[max(0, match.start()-80):match.start()]
        after = text[match.end():match.end()+20]
        ages = list(re.finditer(rf'({NUMBER})岁(?:以上|及以上)', before))
        years = list(re.finditer(rf'({NUMBER})年', before))
        if not ages or not re.search(r'老人|老年|人口', before[-50:]+after):
            continue
        age = number(ages[-1][1])
        year = number(years[-1][1]) if years else None
        # Require a calendar-year prediction close to this age-group count.
        if not year or not 1900 <= year <= 2200 or not 40 <= age <= 100:
            continue
        result.append((number(match[1]), year, age,
                       bool(re.search(r'接近|近|大概|约', before[-8:]))))
    return result


def error(title, cover, source):
    contexts = population_contexts(source)
    normalized = clean(source)
    for copy in (title, cover):
        text = clean(copy)
        counts = {number(m[1]) for m in re.finditer(rf'({NUMBER})亿', text)}
        for count in counts:
            rows = {row for row in contexts if row[0] == count}
            if len(rows) != 1:
                continue  # Ambiguous statistics still require semantic review.
            _, year, age, approximate = rows.pop()
            years = {number(m[1]) for m in re.finditer(rf'({NUMBER})年', text)}
            ages = {number(m[1]) for m in re.finditer(rf'({NUMBER})岁(?:以上|及以上)', text)}
            if (year not in years or age not in ages
                    or approximate and not re.search(r'接近|近|大概|约', text)):
                return '人口数量须保留原文年份、年龄范围与约数；字数不够可改选原话中的其他观点，不能把预测写成现状'
        # Match the same percent, requiring explicit 年 in its source context.
        for m in re.finditer(rf'(?:百分之({NUMBER})|([0-9]+)[%％])', text):
            value = number(m[1] or m[2])
            for original in re.finditer(rf'(?:百分之({NUMBER})|([0-9]+)[%％])', normalized):
                if number(original[1] or original[2]) != value:
                    continue
                prefix = normalized[max(0, original.start()-30):original.start()]
                if (re.search(ANNUAL, prefix)
                        and re.search(r'增长|增速', text)
                        and not re.search(ANNUAL, text)):
                    return '原话是年度增长率，标题与封面不能丢掉每年或年复合的单位，变成多年累计增长'
    return None
