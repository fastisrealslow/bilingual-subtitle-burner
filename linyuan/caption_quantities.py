"""Observable number/unit spans shared by caption planning and verification."""
import re

VERSION = 2026102401
_CHINESE = '〇零一二三四五六七八九十百千万亿两'
_NUMBER = rf'(?:\d+(?:\.\d+)?|[{_CHINESE}]+(?:点[{_CHINESE}]+)?)'
_UNIT = r'(?:亿元|万元|个百分点|小时|分钟|年|月|日|倍|亿|万|元|%|％|秒)'
QUANTITY = re.compile(rf'(?:{_NUMBER}{_UNIT})+')


def quantity_spans(text):
    return [match.span() for match in QUANTITY.finditer(text)]
