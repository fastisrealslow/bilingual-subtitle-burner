from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from fc import index as fc


@pytest.mark.parametrize('container,field',[
    ('title_rewrite','style_profile'),('copy_identity','title_style_profile')])
def test_inflight_experimental_title_cannot_be_admitted(container,field):
    meta={container:{field:'yuanyuan-v6-concrete-reader-value-20261010'}}
    assert '实验标题' in fc.artifact_quality_error(meta)


@pytest.mark.parametrize('profile',[None,'yuanyuan-v5-complete-spoken-copy-20260924'])
def test_stable_and_historical_titles_still_go_through_normal_checks(profile):
    meta={'copy_identity':{'title_style_profile':profile}}
    result=fc.artifact_quality_error(meta)
    assert result and '实验标题' not in result
    assert '质量闸门' in result


@pytest.mark.parametrize('container',['title_rewrite','copy_identity'])
@pytest.mark.parametrize('malformed',['broken',['broken'],42])
def test_malformed_profile_container_does_not_crash_before_normal_validation(container,malformed):
    result=fc.artifact_quality_error({container:malformed})
    assert result and '质量闸门' in result
