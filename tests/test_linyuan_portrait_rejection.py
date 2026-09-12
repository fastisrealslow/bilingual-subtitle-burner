import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from live_motion import residual_motion
import headline_policy as H

@pytest.mark.parametrize('text', ['人少了没办法，它只消费少','它只消费少','我们做做买卖都是要赚钱','老字号啊，就最顶尖的老字号都是国有'])
def test_orphan_replies_and_disfluencies_are_not_titles(text):
    assert not H.complete(text)

@pytest.mark.parametrize('text', ['如果价格始终没有跌到我们能承受的水平，我们不会买入','股息率不到8%我不会买','我们长期持有优秀企业'])
def test_qualified_claims_remain_eligible(text):
    assert H.complete(text)


def test_camera_motion_over_a_photo_is_not_local_motion():
    import cv2
    import numpy as np
    rng=np.random.default_rng(20)
    first=cv2.GaussianBlur(rng.integers(0,256,(180,240),dtype=np.uint8),(7,7),0)
    transform=cv2.getRotationMatrix2D((120,90),1.,1.015)
    transform[:,2]+=[2,-1]
    second=cv2.warpAffine(first,transform,(240,180),borderMode=cv2.BORDER_REFLECT)
    assert not residual_motion(first,second)['moving']


def test_local_motion_survives_camera_compensation():
    import cv2
    import numpy as np
    rng=np.random.default_rng(20)
    first=cv2.GaussianBlur(rng.integers(0,256,(180,240),dtype=np.uint8),(3,3),0)
    second=first.copy()
    cv2.ellipse(second,(120,110),(20,8),0,0,360,80,-1)
    assert residual_motion(first,second)['moving']


def test_landscape_sampling_excludes_overlaid_subtitles():
    from live_motion import window_for_meta
    rect=window_for_meta(dict(layout_proof=dict(
        live_region=dict(x=156,y=0,width=968,height=720),
        subtitle_region=dict(x=200,y=570,width=880,height=138))))
    assert rect==dict(x=156,y=0,width=968,height=570)
    assert window_for_meta({})==dict(x=44,y=360,width=632,height=470)
