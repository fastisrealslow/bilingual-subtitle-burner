"""Find archives without trading away freshness or increasing request budgets."""
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import monitor_v2 as m


def source(tmp_path, state=None, **changes):
    config=dict(keyword='林园',pages=6,archive_keywords=['林园 演讲','林园 2019'],
                _seed_scan_path=str(tmp_path/'scan.json'))
    config.update(changes)
    return m.WeiboSearchSource(config,state if state is not None else {})


def test_six_page_budget_includes_recent_results_and_one_rotating_archive(tmp_path):
    src=source(tmp_path)
    first=[(q,list(p)) for q,p in src.query_pages()]
    assert first==[('林园',[1,2,3]),('林园 演讲',[1,2,3])]
    saved=json.loads((tmp_path/'scan.json').read_text())
    resumed=source(tmp_path,{'_seed_scan':saved})
    second=[(q,list(p)) for q,p in resumed.query_pages()]
    assert second==[('林园',[1,2,3]),('林园 2019',[1,2,3])]
    third=[(q,list(p)) for q,p in resumed.query_pages()]
    assert third==[('林园',[1,2,3]),('林园 演讲',[4,5,6])]
    assert all(sum(map(lambda r:len(r[1]),plan))==6 for plan in (first,second,third))


def test_archive_cursor_is_not_consumed_if_recent_query_is_not_completed(tmp_path):
    src=source(tmp_path);plan=src.query_pages()
    assert next(plan)[0]=='林园'
    assert 'weibo_archive_queries' not in src.scan_state


def test_three_archive_topics_share_the_same_six_page_budget_and_resume(tmp_path):
    src=source(tmp_path,archive_keywords=['林园 2019','林园 2017','林园 第一桶金','林园 实地调研'],
               archive_queries_per_run=3)
    first=[(q,list(p)) for q,p in src.query_pages()]
    assert first==[('林园',[1,2,3]),('林园 2019',[1]),('林园 2017',[1]),('林园 第一桶金',[1])]
    resumed=source(tmp_path,{'_seed_scan':json.loads((tmp_path/'scan.json').read_text())},
        archive_keywords=src.config['archive_keywords'],archive_queries_per_run=3)
    second=[(q,list(p)) for q,p in resumed.query_pages()]
    assert second==[('林园',[1,2,3]),('林园 实地调研',[1]),('林园 2019',[2]),('林园 2017',[2])]
    assert sum(len(p) for _,p in first)==sum(len(p) for _,p in second)==6


def test_topic_cursor_does_not_skip_queries_not_yet_requested(tmp_path):
    src=source(tmp_path,archive_queries_per_run=3)
    plan=src.query_pages();next(plan);next(plan)
    saved=json.loads((tmp_path/'scan.json').read_text())
    assert saved['weibo_archive_queries']['cursor']==1
    assert set(saved['weibo_archive_pages'])=={'林园 演讲'}


@pytest.mark.parametrize('changes',[
    dict(archive_keywords=[]),dict(pages=1),dict(archive_keywords=['林园',' ',None]),
])
def test_legacy_and_small_budgets_do_not_grow_requests(tmp_path,changes):
    src=source(tmp_path,**changes);plan=[(q,list(p)) for q,p in src.query_pages()]
    assert len(plan)==1 and plan[0][0]=='林园'
    assert len(plan[0][1])==src.config.get('pages',6)


def test_exact_api_requests_remain_within_budget_and_source_outage_stops_the_provider(tmp_path,monkeypatch):
    calls=[]
    def request(url,headers):
        query=parse_qs(urlsplit(url).query);calls.append(query)
        if query['q']==['林园 演讲']:
            raise HTTPError(url,429,'restricted',{},None)
        media=dict(duration=1800,stream_url_hd='https://cdn.example.com/live.mp4')
        row=dict(mid=str(5355011066565500+len(calls)),text_raw='林园完整访谈',
                 user=dict(screen_name='实际作者'),page_info=dict(media_info=media))
        return json.dumps(dict(statuses=[row]))
    monkeypatch.setattr(m.WeiboSearchSource,'_visitor_session',lambda _:(request,'token'))
    rows=source(tmp_path).fetch(None)
    assert len(calls)==4 and len(rows)==3
    assert all(r['author']=='实际作者' for r in rows)
    assert sum(q['q']==['林园 演讲'] for q in calls)==1
    assert source(tmp_path).can_fetch() is False


def test_search_and_exact_refresh_share_provider_cooldown_without_resetting_cursors(tmp_path):
    src=source(tmp_path)
    src._record_provider_restriction(HTTPError('https://weibo.com/',429,'restricted',{},None))
    assert src.can_fetch() is False
    exact=m.WeiboVideoSource(src.config,{})
    assert exact.can_fetch() is False
    assert not (tmp_path/'scan.json').exists()


def test_empty_first_page_restriction_stops_generic_monitor_retries(tmp_path,monkeypatch):
    calls=[]
    def request(url,headers):
        calls.append(url)
        raise HTTPError(url,429,'restricted',{},None)
    monkeypatch.setattr(m.WeiboSearchSource,'_visitor_session',lambda _:(request,''))
    def no_backoff(*_):raise AssertionError('Do not retry a provider during cooldown')
    monkeypatch.setattr(m.WeiboSearchSource,'backoff',no_backoff)
    assert m._run_source(m.WeiboSearchSource,source(tmp_path).config,{},None)==[]
    assert len(calls)==1


def test_configured_discovery_queries_are_not_counted_as_video_seeds():
    from seed_campaign import census
    root=Path(__file__).resolve().parents[1]/'linyuan'
    config=next(r for r in json.loads((root/'monitor_v2_config.json').read_text()) if r['type']=='weibo_search')
    assert config['pages']==6 and '林园 第一桶金' in config['archive_keywords']
    assert '林园 2010' in config['archive_keywords'] and '林园 2026' in config['archive_keywords']
    assert census(root)['counting_policy']=='unique_parent_video_id_not_parts_or_search_queries'
