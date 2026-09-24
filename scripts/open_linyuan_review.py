#!/usr/bin/env python3
"""Open the local media review and start its loopback server when needed."""
import argparse
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765/output/benchmark-20260921/comparison.html'


def ready():
    try:
        with urllib.request.urlopen(URL, timeout=2) as response:
            return response.status == 200 and '林园'.encode() in response.read(1024)
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    if not (ROOT/'output/benchmark-20260921/comparison.html').is_file():
        raise SystemExit('本地对比网页尚未生成；需要先取得媒体并运行网页生成脚本。')
    if not ready():
        log = ROOT/'output/benchmark-20260921/review-server.log'
        with log.open('ab') as output:
            process = subprocess.Popen([sys.executable, str(ROOT/'scripts/serve_linyuan_review.py')],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        for _ in range(20):
            if ready():
                break
            if process.poll() is not None:
                raise SystemExit(f'预览服务未能启动，请查看 {log}')
            time.sleep(.25)
        else:
            raise SystemExit(f'预览服务未响应，请查看 {log}')
    print(URL)
    if not args.no_open:
        webbrowser.open(URL)


if __name__ == '__main__':
    main()
