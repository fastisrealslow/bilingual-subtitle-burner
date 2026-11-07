"""The index must equal the old exhaustive matcher, including threshold edges."""
import random
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from fingerprint_match import match_ratio


def exhaustive(current,previous,limit):
    if not current or not previous:return 0.0
    def distance(a,b):
        try:return (int(a,16)^int(b,16)).bit_count()
        except (TypeError,ValueError):return 10**9
    return sum(any(distance(a,b)<=limit for b in previous) for a in current)/len(current)


@pytest.mark.parametrize('bits',[32,64,96])
@pytest.mark.parametrize('limit',[0,3,8,10])
@pytest.mark.parametrize('count',[3,96])
def test_seeded_actual_sized_pairs_equal_exhaustive_matching(bits,limit,count):
    rng=random.Random(bits*10000+limit*100+count)
    previous=[format(rng.getrandbits(bits),'x') for _ in range(count)]
    current=[format(rng.getrandbits(bits),'x') for _ in range(12)]
    # Include both sides of the exact threshold, distributed across all blocks.
    for changed in [0,limit,max(1,limit+1)]:
        original=int(previous[0],16)
        mask=sum(1<<i for i in rng.sample(range(bits),changed))
        current.append(format(original^mask,'x'))
    assert match_ratio(current,previous,limit)==exhaustive(current,previous,limit)


@pytest.mark.parametrize('limit',[-1,0,3,8,32,64,10**9])
def test_empty_duplicate_malformed_signed_and_variable_width_values_keep_old_semantics(limit):
    for current,previous in [([],['0']),(['0'],[]),
        (['','garbage',None,[],{},1,'-1','0xABC',b'ff',bytearray(b'ff')],['0','ff','-2',None]),
        (['1','1','1','ffffffff'],['0']*50),
        (['0','f','100000000'],[format(i,'x') for i in range(100)])]:
        assert match_ratio(current,previous,limit)==exhaustive(current,previous,limit)


def test_changed_history_content_gets_a_different_index_without_stale_acceptance():
    old=[format(i,'x') for i in range(100)]
    assert match_ratio(['ffffffff'],old,3)==0
    old.append('ffffffff')
    assert match_ratio(['ffffffff'],old,3)==1
    assert match_ratio(['ffffffff','0','ffffffff'],old,3)==1
