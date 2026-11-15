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
    issue = market_time_share_error(title, cover, source)
    if issue:
        return issue
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


def market_time_share_error(title, cover, source):
    """Keep a market time share distinct from a price change percentage.

    Bind the number to the same market subject and movement. An unrelated
    percentage elsewhere in the talk must not impose a time denominator.
    """
    percent = rf'(?:百分之({NUMBER})|([0-9]+)[%％])'
    movement = r'(股市|股票|市场|指数)(上涨|下跌|盘整|横盘)'
    modifiers = r'(?:的|只|仅|有|占|是|为|大约|约|大概|不到|超过|比例)*'
    time_unit = r'(?:时间|交易日|天数)'
    source_text = re.sub(r'\s+', '', str(source))
    contexts = []
    for match in re.finditer(movement + rf'的?{time_unit}{modifiers}' + percent, source_text):
        contexts.append((match[1], match[2], number(match[3] or match[4])))
    for subject, action, value in contexts:
        # If the speaker independently states a price move with this same
        # number, leave that ambiguity to semantic review. Negated/questions
        # are not independent affirmative evidence.
        independent = False
        for clause in re.findall(r'[^。；;！？!?]+[。；;！？!?]?', source_text):
            if re.search(r'不|没|并非|不是|是否|吗|么|[？?]', clause):
                continue
            for match in re.finditer(re.escape(subject + action) + modifiers + percent, clause):
                if number(match[1] or match[2]) == value:
                    independent = True
        if independent:
            continue
        for copy in (title, cover):
            for match in re.finditer(re.escape(subject + action) + rf'[^，,。；;！？!?]{{0,12}}?' + percent, str(copy)):
                if number(match[1] or match[2]) != value:
                    continue
                if re.search(time_unit, match[0]):
                    continue
                # “上涨只有5%，其余时间...” supplies the denominator in
                # the immediately following contrast, unlike an unrelated
                # mention of time elsewhere in the title.
                following = str(copy)[match.end():match.end()+14]
                if re.match(r'[，,](?:其余|其他|剩下的?)时间', following):
                    continue
                return '原话比例统计涨跌时间，标题与封面须各自保留时间分母，不能变成涨跌幅或无对象的百分比'
    return None
