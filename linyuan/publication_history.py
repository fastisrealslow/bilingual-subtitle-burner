"""Load publication receipts atomically before expensive production starts."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess


def validate(state):
    if not isinstance(state, dict) or not isinstance(state.get('published'), dict):
        raise ValueError('发布历史不可用，不能把未核对的母片当成未使用')
    if not isinstance(state.get('dispatched'), list):
        raise ValueError('发布历史缺少调度记录')
    return state


def read(path):
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))


def fetch(repo, api=None):
    api = api or (lambda path: json.loads(subprocess.check_output(['gh', 'api', path])))
    doc=api(f'repos/{repo}/contents/linyuan/.automation/fc_state.json?ref=main')
    data=doc
    if not doc.get('content'):
        data=api(f"repos/{repo}/git/blobs/{doc['sha']}")
        if data.get('sha') != doc['sha'] or data.get('encoding') != 'base64':
            raise ValueError('发布历史的文件版本不一致')
    return validate(json.loads(base64.b64decode(data['content'], validate=False)))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    state=fetch(os.environ['GITHUB_REPOSITORY'])
    dest=Path(args.out);temporary=dest.with_suffix(dest.suffix+'.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False,separators=(',',':')))
    temporary.replace(dest)
    print(f"发布历史已核验：{len(state['published'])}批回执")


if __name__=='__main__':main()
