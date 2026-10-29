"""Exact social links are leads; only actual matching video metadata is admitted."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))

spec = importlib.util.spec_from_file_location('weibo_exact_test',
    Path(__file__).resolve().parents[1]/'linyuan/monitor_v2.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
MID = '5331066086756985'


def status(mid=MID, text='林园谈投资和股市', video=True):
    return dict(mid=mid, text_raw=text, user=dict(screen_name='实际发布者'),
        page_info=dict(media_info=dict(duration=600,
            stream_url_hd='https://cdn.example/video.mp4?Expires=123' if video else '')))


def fetch(monkeypatch, payload, urls=None):
    calls=[]
    def request(url, headers):
        calls.append(url)
        return json.dumps(payload)
    monkeypatch.setattr(m.WeiboVideoSource, '_visitor_session', lambda _: (request, 'token'))
    rows=m.WeiboVideoSource({'urls': urls or [f'https://weibo.com/2/detail/{MID}']}, {}).fetch(None)
    return rows,calls


def test_actual_api_title_author_media_are_used(monkeypatch):
    rows,calls=fetch(monkeypatch, {'data':status()})
    assert len(rows)==1 and rows[0]['author']=='实际发布者'
    assert rows[0]['title']=='林园谈投资和股市'
    assert json.loads(rows[0]['extra'])['duration']==600
    assert calls==['https://weibo.com/ajax/statuses/show?id='+MID]


def test_verified_research_seed_cannot_become_a_production_candidate(monkeypatch,tmp_path):
    monkeypatch.setattr(m.WeiboVideoSource,'_visitor_session',lambda _:(
        lambda *a,**kw:json.dumps(status()),'token'))
    rows=m.WeiboVideoSource(dict(urls=[f'https://m.weibo.cn/detail/{MID}'],
        research_only_mids=[MID]),{}).fetch(None)
    extra=json.loads(rows[0]['extra'])
    assert extra['direct_dispatch'] is False and extra['source_role']=='catalog_only'
    from fc import index as fc
    assert fc.pick(rows,dict(dispatched=[],rejected=[],published={}),10)==[]
    monkeypatch.setattr(m,'DB_PATH',tmp_path/'catalog.db');m.init_db()
    m.upsert_items(rows)
    # A subsequent search or stronger title must not silently release the hold.
    plain={**rows[0],'extra':json.dumps(dict(mid=MID,has_video=True,duration=600))}
    m.upsert_items([plain])
    with m.sqlite3.connect(m.DB_PATH) as conn:
        saved=json.loads(conn.execute('select extra from items').fetchone()[0])
    assert saved['direct_dispatch'] is False


def test_research_seed_hold_applies_to_existing_weibo_ledger_before_export(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'DB_PATH',tmp_path/'catalog.db');m.init_db()
    m.upsert_items([dict(id='weibo_search:'+MID,source='weibo_search',title='林园谈投资',
        url='https://m.weibo.cn/detail/'+MID,author='实际发布者',publish_time='',
        extra=json.dumps(dict(mid=MID,has_video=True,duration=600)))])
    (tmp_path/'weibo_seeds.json').write_text(json.dumps(dict(urls=[],research_only_mids=[MID])))
    assert m.enforce_seed_restrictions(tmp_path)==1
    with m.sqlite3.connect(m.DB_PATH) as conn:
        saved=json.loads(conn.execute('select extra from items').fetchone()[0])
    assert saved['source_role']=='catalog_only' and saved['direct_dispatch'] is False


def test_actual_financial_topic_marker_does_not_make_interview_noise(monkeypatch):
    raw='2026.08.07林园专访：为何依旧坚守消费和医药？#圣叹财经[超话]#'
    rows,_=fetch(monkeypatch,status(text=raw))
    assert rows[0]['title'].endswith('#圣叹财经#')
    assert json.loads(rows[0]['extra'])['raw_text']==raw
    from fc import index as fc
    assert len(fc.pick(rows,dict(dispatched=[],rejected=[],published={}),10))==1


def test_topic_names_and_unstructured_noise_remain_blocked(monkeypatch):
    from fc import index as fc
    for raw in ['林园访谈 #灯花笑[超话]#','林园专访 超话应援打榜']:
        rows,_=fetch(monkeypatch,status(text=raw))
        assert fc.pick(rows,dict(dispatched=[],rejected=[],published={}),10)==[]


def test_existing_database_rows_are_reprojected_without_rewriting_original(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'DB_PATH',tmp_path/'monitor.db')
    monkeypatch.setattr(m,'__file__',str(tmp_path/'monitor_v2.py'))
    (tmp_path/'dashboard').mkdir()
    m.init_db()
    raw='林园最新访谈 #圣叹财经[超话]#'
    row=dict(id='weibo_search:'+MID,source='weibo_search',title=raw,
        url='https://m.weibo.cn/detail/'+MID,author='实际发布者',publish_time='',
        extra=json.dumps(dict(has_video=True,duration=600)))
    m.upsert_items([row]);m.export_dashboard_data()
    saved=json.loads((tmp_path/'dashboard/data.json').read_text())[0]
    assert saved['title']=='林园最新访谈 #圣叹财经#'
    assert saved['extra']['discovery_raw_title']==raw
    conn=m.sqlite3.connect(m.DB_PATH)
    assert conn.execute('SELECT title FROM items').fetchone()[0]==raw
    conn.close()


def test_no_text_only_unrelated_or_mismatched_status_is_stock(monkeypatch):
    for data in [status(video=False),status(text='虎林园动物园投资'),
                 status(text='其他人谈股市'),status(mid='5331066086756999')]:
        assert fetch(monkeypatch, data)[0]==[]


def test_invalid_hosts_and_schemes_never_request(monkeypatch):
    rows,calls=fetch(monkeypatch,status(),[
        f'http://weibo.com/{MID}',f'https://evil.example/{MID}',
        f'https://weibo.com.evil.example/{MID}',f'https://weibo.com/2/detail/no-id'])
    assert rows==[] and calls==[]


def test_duplicate_seed_requests_once(monkeypatch):
    rows,calls=fetch(monkeypatch,status(),[f'https://weibo.com/{MID}',f'https://m.weibo.cn/detail/{MID}'])
    assert len(rows)==1 and len(calls)==1


def test_bad_payload_is_rejected_without_leaking_signed_media(monkeypatch,capsys):
    assert fetch(monkeypatch, {'data':None,'error':'SECRET'})[0]==[]
    assert 'SECRET' not in capsys.readouterr().err


def test_direct_sources_run_before_expensive_discovery_and_are_registered():
    configs=json.loads((Path(__file__).resolve().parents[1]/'linyuan/monitor_v2_config.json').read_text())
    assert [c['type'] for c in configs[:2]]==['yicai_video','weibo_video']
    assert m.SOURCES['weibo_video'] is m.WeiboVideoSource


@pytest.mark.parametrize('restrictions', [0, 1])
def test_priority_fetch_filters_channels_and_checkpoints_before_slow_sources(monkeypatch, restrictions):
    seen=[];exports=[]
    policy_calls=[]
    def enforce():
        assert not seen, 'Source restrictions must be reconciled before discovery'
        policy_calls.append(True)
        return restrictions
    monkeypatch.setattr(m,'init_db',lambda:None)
    monkeypatch.setattr(m,'enforce_seed_restrictions',enforce)
    monkeypatch.setattr(m,'load_state',lambda:{})
    monkeypatch.setattr(m,'save_state',lambda _:None)
    monkeypatch.setattr(m,'cdp_available',lambda:False)
    monkeypatch.setattr(m,'upsert_items',lambda _:[])
    monkeypatch.setattr(m,'export_dashboard_data',lambda:exports.append(len(seen)))
    def source(cls,*args):
        seen.append(cls.name)
        return [dict(author='实际发布者')]
    monkeypatch.setattr(m,'run_source',source)
    m.main(['yicai_video','weibo_video'])
    assert seen==['yicai_video','weibo_video']
    assert policy_calls==[True]
    assert exports==([0] if restrictions else [])+[1,2,2]


def test_early_checkpoint_dispatches_real_verification_before_slow_research():
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/linyuan-monitor.yml').read_text()
    assert workflow.index('--source-types yicai_video weibo_video')<workflow.index('source_research.py --max-items')
    assert workflow.index('linyuan-source-inventory.yml/dispatches')<workflow.index('source_research.py --max-items')
    assert workflow.index('linyuan-source-inventory.yml/dispatches')<workflow.index('seed_pool_growth.py --target 5000')
    assert workflow.count('bash persist_monitor_metadata.sh')==3
    assert workflow.index('source_gap_backfill.py')<workflow.index('checkpoint verified long catalogue and original clips')<workflow.index('source_research.py --max-items')
