import json

from linyuan import local_preflight


def manifest(digest):
    return {'sources': [{
        'key': 'mother', 'url': 'https://example.invalid/video',
        'sha256': digest, 'cached_asr': 'cache/cues_raw.json'}]}


def test_preflight_matches_local_video_and_cached_asr(tmp_path, monkeypatch):
    source = tmp_path / 'sources'
    cache = tmp_path / 'asr'
    source.mkdir()
    (cache / 'cache').mkdir(parents=True)
    video = source / 'mother.mp4'
    video.write_bytes(b'mother-video')
    (cache / 'cache/cues_raw.json').write_text('[]')
    digest = local_preflight.sha256(video)
    monkeypatch.setattr(local_preflight.shutil, 'which', lambda name: f'/bin/{name}')
    report = local_preflight.inspect(source, cache, manifest(digest))
    assert report['cloud_calls'] == 0
    assert report['summary']['source_videos_ready'] == 1
    assert report['summary']['cached_asr_ready'] == 1


def test_preflight_does_not_treat_asr_cache_as_source_video(tmp_path, monkeypatch):
    source = tmp_path / 'sources'
    cache = tmp_path / 'asr'
    source.mkdir()
    (cache / 'cache').mkdir(parents=True)
    (cache / 'cache/cues_raw.json').write_text(json.dumps([]))
    monkeypatch.setattr(local_preflight.shutil, 'which', lambda name: None)
    report = local_preflight.inspect(source, cache, manifest('missing'))
    assert report['summary']['source_videos_ready'] == 0
    assert not report['summary']['full_production_ready']
