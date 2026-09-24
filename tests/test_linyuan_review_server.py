from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serve_linyuan_review import ReviewHandler


def test_browser_ranges_and_private_files(tmp_path):
    folder = tmp_path / 'output'
    folder.mkdir()
    (folder / 'movie.mp4').write_bytes(b'0123456789')
    (tmp_path / '.env').write_text('private')
    (folder / 'escape.mp4').symlink_to(tmp_path / '.env')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(ReviewHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for value, expected, content_range in [('bytes=3-5', b'345', 'bytes 3-5/10'),
                                                ('bytes=7-', b'789', 'bytes 7-9/10'),
                                                ('bytes=-2', b'89', 'bytes 8-9/10')]:
            with urllib.request.urlopen(urllib.request.Request(base+'/output/movie.mp4', headers={'Range':value})) as response:
                assert response.status == 206
                assert response.headers['Content-Range'] == content_range
                assert response.headers['Content-Type'] == 'video/mp4'
                assert response.read() == expected
        for path, headers, status in [('/.env', {}, 404), ('/output/escape.mp4', {}, 404),
                                      ('/output/movie.mp4', {'Range':'bytes=20-'}, 416),
                                      ('/output/movie.mp4', {'Range':'bytes=-0'}, 416)]:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base+path, headers=headers))
            assert error.value.code == status
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
