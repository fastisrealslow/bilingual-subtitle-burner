import importlib.util
from pathlib import Path
import time

PATH = Path(__file__).resolve().parents[1] / 'linyuan/fc/index.py'
spec = importlib.util.spec_from_file_location('recent_probe_fc', PATH)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def setup(monkeypatch):
    state = dict(dispatched=[], published={}, rejected=[])
    monkeypatch.setattr(fc, 'load_state', lambda: state)
    monkeypatch.setattr(fc, '_collect_source_rejections', lambda _: 0)
    monkeypatch.setattr(fc, 'source_inventory', lambda _: dict(
        inventory_fresh=True, verified_portrait=15, verified_landscape=3, daily_mix_usable=18))
    return state


def test_full_reserve_does_not_dispatch_old_uploads_as_recent(monkeypatch):
    setup(monkeypatch)
    calls=[]
    def api(method, path, **kw):
        calls.append((method, path))
        assert method == 'GET'
        if path.startswith('/actions/'): return {'workflow_runs': []}
        return b'{"items":[]}'
    monkeypatch.setattr(fc, 'gh', api)
    def pick(items, state, n, audit):
        return [{'title': '林园2020.12.14访谈', 'publish_time': '2026-10-13T01:00:00Z'}]
    monkeypatch.setattr(fc, 'pick', pick)
    result=fc._dispatch_admitted()
    assert result['reserve_full'] == 1 and result['recent_context_candidates'] == 0
    assert all(method == 'GET' for method, _ in calls)


def test_daily_recent_probe_budget_does_not_expand_normal_worker_limit(monkeypatch):
    state=setup(monkeypatch)
    state['recent_context_probe_date']=time.strftime('%Y-%m-%d', time.gmtime(time.time()+8*3600))
    monkeypatch.setattr(fc, 'gh', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('No new dispatch')))
    assert fc._dispatch_admitted()['recent_context_probe_done'] == 1


def test_reported_old_tracking_chatter_is_blocked_before_cached_publication():
    meta=dict(source_sha256='ced49dbeb34439f98149da57e9d361a18f8d62c852dea4a49b6b54729d2b73ea',
              framing_proofs=[dict(version=2026092101,pan_frames=3042,held_frames=15,cut_frames=[])])
    assert '持续追脸裁切' in fc.artifact_quality_error(meta)
    meta['framing_proofs'][0]['pan_frames']=0
    assert '持续追脸裁切' not in (fc.artifact_quality_error(meta) or '')
