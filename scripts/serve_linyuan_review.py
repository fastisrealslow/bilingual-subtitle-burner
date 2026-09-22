#!/usr/bin/env python3
"""Serve local review media with seekable HTTP ranges; never bind publicly."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGE = '/output/benchmark-20260921/comparison.html'


class ReviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        if args and str(args[1] if len(args) > 1 else '') not in ('200', '206', '304'):
            super().log_message(format, *args)

    def send_head(self):
        self.remaining = None
        raw = unquote(urlsplit(self.path).path)
        if raw == '/':
            self.send_response(302)
            self.send_header('Location', PAGE)
            self.end_headers()
            return None
        root = Path(self.directory).resolve()
        path = Path(self.translate_path(self.path)).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            self.send_error(403)
            return None
        allowed = (relative.parts[:1] in [('output',), ('docs',)]
                   or relative.parts[:2] == ('linyuan', 'simulations'))
        if not allowed or any(p.startswith('.') for p in relative.parts) or not path.is_file():
            self.send_error(404)
            return None
        file = path.open('rb')
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
            valid = bool(match and any(match.groups()) and size)
            if valid:
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
                valid = 0 <= start <= end < size
            if not valid:
                file.close()
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return None
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(end - start + 1))
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        file.seek(start)
        self.remaining = end - start + 1
        return file

    def copyfile(self, source, outputfile):
        try:
            if self.remaining is None:
                shutil.copyfileobj(source, outputfile)
                return
            left = self.remaining
            while left:
                chunk = source.read(min(left, 256 * 1024))
                if not chunk:
                    break
                outputfile.write(chunk)
                left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Seeking or switching videos deliberately cancels a transfer.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(ReviewHandler, directory=str(ROOT)))
    print(f'本机浏览器打开：http://127.0.0.1:{server.server_port}{PAGE}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
