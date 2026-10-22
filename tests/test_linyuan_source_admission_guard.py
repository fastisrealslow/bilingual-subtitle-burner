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
    assert restriction('https://www.bilibili.com/video/BV1TEST00001',seeds) is None
    assert restriction('https://example.com/?url='+URL,seeds) is None
    assert check('https://example.com/source.mp4','new',seeds,{}) is None


def test_freshly_banned_historical_mother_is_not_given_false_clearance():
    assert restriction('https://www.bilibili.com/video/BV1RJ411Q7Ki',[])
    assert restriction('https://www.bilibili.com/video/BV1Yu2uBcEDh',[])


def test_known_source_admission_rejection_is_not_relabelled_as_retryable_service_failure(tmp_path):
    from production_failure_report import report_missing
    target=tmp_path/'batch_report.json'
    assert not report_missing(target,dict(source_admission=dict(outcome='failure')),'old')
    assert not target.exists()


@pytest.mark.parametrize('url',[
    'https://www.yicai.com/video/102581577.html',
    'https://yicai.com/video/102581577.html?from=share',
])
def test_verified_official_page_restriction_blocks_normal_and_staged_retry(url):
    assert '书面授权' in restriction(url,[])
    state=dict(dispatched=[dict(slug='old',ts=2,source_url=url)])
    assert check('https://github.com/org/repo/releases/download/staging/old.mp4',
                 'old',[],state)


@pytest.mark.parametrize('url',[
    'https://example.com/?url=https://www.yicai.com/video/102581577.html',
    'https://www.yicai.com.evil.test/video/102581577.html',
    'https://www.yicai.com/video/103329354.html',
])
def test_exact_page_not_platform_wide_policy(url):
    assert restriction(url,[]) is None


def test_explicit_policy_fixture_does_not_grant_other_sources_rights():
    assert restriction('https://www.yicai.com/video/102581577.html',[],[]) is None
    assert check('https://example.com/media.mp4','other',[],dict(dispatched=[
        dict(slug='old',ts=3,source_url='https://www.yicai.com/video/102581577.html')])) is None


@pytest.mark.parametrize('bvid',['BV131421U7XK','BV15ByfBqEoQ','BV1s6yABZEYV',
    'BV1EUycByEdT','BV1cYyKBcEL6','BV1REFpzBE8r','BV1nKxjeNE3u',
    'BV11WL3z8EqR','BV1K9VizFETj','BV13Ej9zZEM9',
    'BV1tTi5BrEES','BV1eXtZzgE7C','BV1HXejzDEhn','BV1qos3zgEu1','BV1VimhYiEXr'])
def test_live_verified_parent_ban_supersedes_absent_or_outdated_seed(bvid):
    source='https://www.bilibili.com/video/'+bvid+'?p=2&from=share'
    for seeds in ([],[dict(bvid=bvid,no_reprint=0,direct_dispatch=True)]):
        assert '明确禁止转载' in restriction(source,seeds)
        state=dict(dispatched=[dict(slug='old',ts=4,source_url=source)])
        assert check('https://github.com/org/repo/releases/download/staging/old.mp4',
                     'old',seeds,state)


def test_verified_bans_do_not_block_other_parent_or_nested_urls():
    assert restriction('https://www.bilibili.com/video/BV1a84y1c7B7',[]) is None
    assert restriction('https://example.com/?url=https://www.bilibili.com/video/BV15ByfBqEoQ',[]) is None
    assert restriction('https://www.bilibili.com.evil.test/video/BV15ByfBqEoQ',[]) is None
