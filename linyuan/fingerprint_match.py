"""Exact Hamming matching with reusable disjoint-bit indexes.

If two words differ in at most k bits, at least one of k+1 disjoint blocks
must be identical. Buckets only prune impossible pairs; every candidate still
passes the original XOR/bit-count predicate. No thresholds or match weights
change, and no publication decision is cached.
"""
from functools import lru_cache


@lru_cache(maxsize=32768)
def _parse_string(value):
    try:
        return int(value,16)
    except (TypeError,ValueError):
        return None


def _parse(value):
    if isinstance(value,bytearray):value=bytes(value)
    return _parse_string(value) if isinstance(value,(str,bytes)) else None


@lru_cache(maxsize=256)
def _blocks(previous,width,distance):
    count=distance+1
    size,extra=divmod(width,count)
    result=[];offset=0
    for number in range(count):
        length=size+int(number<extra);mask=(1<<length)-1
        buckets={}
        for value in previous:
            buckets.setdefault((value>>offset)&mask,[]).append(value)
        result.append((offset,mask,buckets))
        offset+=length
    return result


def match_ratio(current,previous,max_distance):
    if not current or not previous:return 0.0
    left=[_parse(value) for value in current]
    right=tuple(dict.fromkeys(value for value in map(_parse,previous) if value is not None))
    # Keep legacy semantics for unusual signed/oversized limits, including
    # malformed values' original non-match distance of 10**9.
    if (type(max_distance) is not int or max_distance<0 or max_distance>=10**9
            or any(value is not None and value<0 for value in left)
            or any(value<0 for value in right)):
        def distance(a,b):
            if a is None or b is None:return 10**9
            return (a^b).bit_count()
        raw_right=[_parse(value) for value in previous]
        return sum(any(distance(a,b)<=max_distance for b in raw_right) for a in left)/len(left)
    if not right:return 0.0
    width=max((value.bit_length() for value in [*right,*(v for v in left if v is not None)]),default=1)
    width=32 if width<=32 else 64 if width<=64 else width
    if max_distance>=width:return sum(v is not None for v in left)/len(left)
    # Small lists are cheaper to compare directly, already parsed once.
    if len(right)<24:
        return sum(a is not None and any((a^b).bit_count()<=max_distance for b in right)
                   for a in left)/len(left)
    blocks=_blocks(right,width,max_distance)
    def matched(value):
        if value is None:return False
        return any((value^old).bit_count()<=max_distance
                   for shift,mask,buckets in blocks
                   for old in buckets.get((value>>shift)&mask,()))
    return sum(map(matched,left))/len(left)
