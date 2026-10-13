import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from source_admission_guard import check,restriction

URL='https://www.bilibili.com/video/BV1Bo4y1W7Ts'


@pytest.mark.parametrize('fields',[dict(no_reprint=1),dict(direct_dispatch=False),dict(source_role='catalog_only'),dict(source_role='reference')])
def test_known_parent_restriction_also_blocks_exact_page_and_old_retry(fields):
    seeds=[dict(bvid='BV1Bo4y1W7Ts',**fields)]
    assert restriction(URL+'?p=2&from=search',seeds)
    assert check(URL,'old',seeds,{})
    state=dict(dispatched=[dict(slug='old',ts=1,source_url=URL)])
    assert check('https://github.com/org/repo/releases/download/staging/old.mp4','old',seeds,state)


def test_unknown_or_different_source_is_not_given_false_clearance_or_wrong_rejection():
    seeds=[dict(bvid='BV1Bo4y1W7Ts',no_reprint=1)]
    assert restriction('https://www.bilibili.com/video/BV1RJ411Q7Ki',seeds) is None
    assert restriction('https://example.com/?url='+URL,seeds) is None
    assert check('https://example.com/source.mp4','new',seeds,{}) is None


def test_known_source_admission_rejection_is_not_relabelled_as_retryable_service_failure(tmp_path):
    from production_failure_report import report_missing
    target=tmp_path/'batch_report.json'
    assert not report_missing(target,dict(source_admission=dict(outcome='failure')),'old')
    assert not target.exists()
