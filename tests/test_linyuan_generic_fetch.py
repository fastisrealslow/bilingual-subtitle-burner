"""Real format selection and media validation for the #706 JPG incident."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import ci_fetch_generic as g
import ci_fetch_bilibili as b


def select(formats, selector=g.FORMAT):
    with YoutubeDL({'format': selector, 'quiet': True, 'no_warnings': True}) as ydl:
        return ydl.process_ie_result(dict(id='706', title='source', extractor='Weibo',
                                         formats=formats), download=False)


def preview():
    return dict(format_id='scrubber_hd', url='https://example.test/preview.jpg', ext='jpg')


def test_real_selector_rejects_706_preview_that_old_fallback_selected():
    assert select([preview()], 'bv*[height<=1080]+ba/b[height<=1080]/b')['ext'] == 'jpg'
    with pytest.raises(ExtractorError, match='Requested format is not available'):
        select([preview()])


def test_real_selector_preserves_highest_media_and_split_audio():
    formats = [preview(), dict(format_id='720', url='https://example.test/720.mp4',
        ext='mp4', height=720, vcodec='h264', acodec='aac'),
        dict(format_id='1080', url='https://example.test/1080.mp4', ext='mp4',
             height=1080, vcodec='h264', acodec='none'),
        dict(format_id='audio', url='https://example.test/audio.m4a', ext='m4a',
             vcodec='none', acodec='aac')]
    chosen = select(formats)
    assert [f['format_id'] for f in chosen['requested_formats']] == ['1080', 'audio']


def test_actual_download_path_ignores_stale_jpg_and_sidecars(monkeypatch, tmp_path):
    (tmp_path/'video.jpg').write_bytes(b'old cache')
    (tmp_path/'video.mp4.download.json').write_text('{}')
    media = tmp_path/'video.mp4'
    media.write_bytes(b'mocked media; separately validated below')
    def download(*args, **kwargs):
        (tmp_path/'downloaded-path.txt').write_text(str(media)+'\n')
    monkeypatch.setattr(g.subprocess, 'run', download)
    checked = []
    monkeypatch.setattr(g, 'validate_media', lambda path: checked.append(path))
    assert g.fetch('https://example.test/source', tmp_path, tmp_path/'report.json') == media
    assert checked == [media]


def test_missing_real_format_writes_retryable_report(monkeypatch, tmp_path):
    (tmp_path/'downloaded-path.txt').write_text('stale path\n')
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['yt-dlp'])
    monkeypatch.setattr(g.subprocess, 'run', fail)
    report = tmp_path/'report.json'
    with pytest.raises(subprocess.CalledProcessError):
        g.fetch('https://example.test/source', tmp_path, report)
    result = json.loads(report.read_text())
    assert result['retryable'] and not result['passed']
    assert result['failure_stage'] == 'source-fetch'
    assert not (tmp_path/'downloaded-path.txt').exists()


def test_real_jpg_rejected_by_media_gate_with_report(monkeypatch, tmp_path):
    jpg = tmp_path/'video.jpg'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=64x64',
                    '-frames:v','1',str(jpg)], check=True)
    report = tmp_path/'source_quality.json'
    monkeypatch.setattr(sys, 'argv', ['fetch', '--validate-only', str(jpg),
                                      '--failure-report', str(report)])
    with pytest.raises(RuntimeError, match='audio=0'):
        b.main()
    result = json.loads(report.read_text())
    assert result['retryable'] and not result['passed']
    assert 'audio=0' in result['reason']


def test_complete_cache_follows_validation_in_production():
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/linyuan-produce-cn.yml').read_text()
    assert workflow.index('name: 素材质量门禁') < workflow.index('name: 保存完整母片')
    assert 'if: steps.source_gate.outcome' in workflow
    assert 'ls _src/video.*' not in workflow
