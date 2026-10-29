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


def test_configured_discovery_queries_are_not_counted_as_video_seeds():
    from seed_campaign import census
    root=Path(__file__).resolve().parents[1]/'linyuan'
    config=next(r for r in json.loads((root/'monitor_v2_config.json').read_text()) if r['type']=='weibo_search')
    assert config['pages']==6 and '林园 第一桶金' in config['archive_keywords']
    assert '林园 2010' in config['archive_keywords'] and '林园 2026' in config['archive_keywords']
    assert census(root)['counting_policy']=='unique_parent_video_id_not_parts_or_search_queries'
