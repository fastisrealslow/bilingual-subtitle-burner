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


PERCENT = re.compile(rf'百分之({NUMBER}(?:点{NUMBER})?)|([0-9]+(?:\.[0-9]+)?)[%％]')


def percentage_mentions(text):
    """Retain clause positions while comparing spoken and written numbers."""
    from decimal import Decimal
    text = str(text or '')
    rows = []
    for match in PERCENT.finditer(text):
        raw = match[1] or match[2]
        if '点' in raw:
            whole, fraction = raw.split('点')
            digits = dict(zip('零〇一二两三四五六七八九', '001223456789'))
            parsed = number(whole)
            if parsed is None or not all(c in digits or c.isdecimal() for c in fraction):
                continue
            raw = str(parsed) + '.' + ''.join(digits.get(c, c) for c in fraction)
        elif not match[2]:
            parsed = number(raw)
            if parsed is None:
                continue
            raw = str(parsed)
        rows.append((Decimal(raw), match))
    return rows


def tentative_percentage(text, match):
    # A hedge from another sentence, comma clause, contrasting judgment or
    # quoted opinion cannot qualify this percentage. A question mark alone
    # is not an estimate. Never rewrite the number or infer missing context.
    prefix = re.split(r'[。！？!?；;，,：:“”「」]|但是|不过|然而|但',
                      text[:match.start()])[-1][-40:]
    return any(not re.search(r'(?:不|没|没有|并非|不是|不能|不再|别|不要)(?:会|能|去)?$',
                             prefix[:hedge.start()])
               for hedge in re.finditer(r'可能|也许|或许|估计|预计|大概', prefix))


def percentage_hedge_error(title, cover, source):
    """Do not promote an explicitly tentative percentage into a fact.

    Only percentages whose every source occurrence is qualified activate the
    check. Repeated values with a definite/ambiguous context still need normal
    semantic review; borrowing one occurrence would assign false certainty.
    """
    source = str(source or '')
    contexts = {}
    for value, match in percentage_mentions(source):
        contexts.setdefault(value, []).append(tentative_percentage(source, match))
    for copy in (str(title or ''), str(cover or '')):
        for value, match in percentage_mentions(copy):
            rows = contexts.get(value, [])
            if rows and all(rows) and not tentative_percentage(copy, match):
                return '百分比是原话的可能或估计；标题与封面各自保留该比例的判断语气，不能改成确定事实'
    return None


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
