import http.client
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
import face_model_download as D
import produce_cn as P


def http_error(code):
    return urllib.error.HTTPError('https://example.invalid/model', code, 'upstream', {}, None)


def test_actual_production_transport_failure_replays_as_recoverable(tmp_path, monkeypatch):
    evidence = json.loads((Path(__file__).parent/'fixtures/linyuan_actual_face_model_transport_failure.json').read_text())
    for index, failure in enumerate(evidence['failures']):
        assert any('VisualResponseFormatError: 本地人物模型准备失败：HTTP Error 504: Gateway Time-out' in line
                   for line in failure['excerpt'])
        op = Mock(side_effect=[http_error(504), io.BytesIO(b'complete-model')])
        monkeypatch.setattr(D.urllib.request, 'urlopen', op)
        monkeypatch.setattr(D.time, 'sleep', lambda _: None)
        target = tmp_path/f'model-{index}.onnx'
        D.download(target, 'https://example.invalid/model', 10)
        assert op.call_count == 2 and target.read_bytes() == b'complete-model'


@pytest.mark.parametrize('error', [http_error(504), http_error(502), http_error(503),
    http_error(500), http_error(429), http_error(408), TimeoutError('read timeout'),
    urllib.error.URLError('connection reset'), ConnectionResetError('reset'),
    http.client.IncompleteRead(b'partial')])
def test_transient_transport_failure_then_complete_file(tmp_path, monkeypatch, error):
    # Production runs 42582576839 / 42582564289 / 42582551587 and four
    # earlier runs stopped after their first actual HTTP 504 response.
    op = Mock(side_effect=[error, io.BytesIO(b'correct-model')])
    sleep = Mock()
    monkeypatch.setattr(D.urllib.request, 'urlopen', op)
    monkeypatch.setattr(D.time, 'sleep', sleep)
    target = tmp_path/'model.onnx'
    D.download(target, 'https://example.invalid/model', 10)
    assert target.read_bytes() == b'correct-model'
    assert op.call_count == 2
    sleep.assert_called_once_with(2)
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('code', [400, 401, 403, 404, 422])
def test_permanent_http_error_does_not_retry_or_replace(tmp_path, monkeypatch, code):
    target = tmp_path/'model.onnx'
    target.write_bytes(b'prior')
    op = Mock(side_effect=http_error(code)); sleep = Mock()
    monkeypatch.setattr(D.urllib.request, 'urlopen', op)
    monkeypatch.setattr(D.time, 'sleep', sleep)
    with pytest.raises(urllib.error.HTTPError):
        D.download(target, 'https://example.invalid/model', 10)
    assert op.call_count == 1
    sleep.assert_not_called()
    assert target.read_bytes() == b'prior'
    assert not list(tmp_path.glob('*.part'))


def test_retry_exhaustion_preserves_cause_and_removes_partial(tmp_path, monkeypatch):
    error = http_error(504)
    op = Mock(side_effect=error); sleep = Mock()
    monkeypatch.setattr(D.urllib.request, 'urlopen', op)
    monkeypatch.setattr(D.time, 'sleep', sleep)
    monkeypatch.setattr(P, 'LOCAL_FACE_MODEL_DIR', tmp_path)
    with pytest.raises(P.VisualResponseFormatError) as caught:
        P._local_face_models()
    assert caught.value.__cause__ is error
    assert op.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [2, 5]
    assert not list(tmp_path.iterdir())


def test_partial_attempt_is_not_appended_or_published(tmp_path, monkeypatch):
    class Broken(io.BytesIO):
        def read(self, n):
            if self.tell():
                assert not (tmp_path/'model.onnx').exists()
                raise ConnectionResetError('partial transport')
            return super().read(4)
    op = Mock(side_effect=[Broken(b'junk'), io.BytesIO(b'correct-model')])
    monkeypatch.setattr(D.urllib.request, 'urlopen', op)
    monkeypatch.setattr(D.time, 'sleep', lambda _: None)
    D.download(tmp_path/'model.onnx', 'https://example.invalid/model', 10)
    assert (tmp_path/'model.onnx').read_bytes() == b'correct-model'
    assert not list(tmp_path.glob('*.part'))


def test_short_success_response_still_fails_size_gate(tmp_path, monkeypatch):
    op = Mock(return_value=io.BytesIO(b'bad')); sleep = Mock()
    monkeypatch.setattr(D.urllib.request, 'urlopen', op)
    monkeypatch.setattr(D.time, 'sleep', sleep)
    with pytest.raises(ValueError, match='模型文件不完整'):
        D.download(tmp_path/'model.onnx', 'https://example.invalid/model', 10)
    assert op.call_count == 1
    sleep.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_normal_model_pair_and_cache_keep_original_urls_and_sizes(tmp_path, monkeypatch):
    op = Mock(side_effect=[io.BytesIO(b'd'*200_000), io.BytesIO(b'r'*10_000_000)])
    sleep = Mock()
    monkeypatch.setattr(D.urllib.request, 'urlopen', op)
    monkeypatch.setattr(D.time, 'sleep', sleep)
    monkeypatch.setattr(P, 'LOCAL_FACE_MODEL_DIR', tmp_path)
    pair = P._local_face_models()
    assert [p.stat().st_size for p in pair] == [200_000, 10_000_000]
    assert [c.args[0].full_url for c in op.call_args_list] == [P.LOCAL_FACE_DETECTOR_URL, P.LOCAL_FACE_RECOGNIZER_URL]
    assert P._local_face_models() == pair
    assert op.call_count == 2
    sleep.assert_not_called()


def test_real_http_504_response_recovers_without_mocking_transport(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if len(requests) == 1:
                self.send_error(504, 'Gateway Time-out')
                return
            payload = b'complete-model-payload'
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(D.time, 'sleep', lambda _: None)
    try:
        target = tmp_path/'model.onnx'
        D.download(target, f'http://127.0.0.1:{server.server_port}/model', 10)
        assert target.read_bytes() == b'complete-model-payload'
        assert requests == ['/model', '/model']
        assert not list(tmp_path.glob('*.part'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
