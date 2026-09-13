"""Replace one unposted overstatement with the guest's exact, complete quote."""
import json
from pathlib import Path
import sys

import title_rewrite as titles
from reuse_title_verification import validate_guest_fixture

OLD = '林园：高端消费因壁垒强，需求依旧旺盛'
QUOTE = '高端消费，还是非常好，旺盛，没有问题'
SHA = 'b49fb3a54a1f6f0b0fb4a2c4723491e1cf9a479ca33e8eaf54cf78ef35eb9052'


def repair(directory):
    directory = Path(directory)
    path = directory / 'meta.json'
    parts = json.loads(path.read_text())
    matches = [p for p in parts if p.get('final') == 'final_4.mp4']
    assert len(matches) == 1
    part = matches[0]
    assert part['fingerprints']['sha256'] == SHA
    import hashlib
    assert hashlib.sha256((directory / part['final']).read_bytes()).hexdigest() == SHA
    raw = json.loads((directory / 'subtitle_edit_proof_4-1.json').read_text())['raw_text']
    assert QUOTE in raw
    if part['title'] != '林园：' + QUOTE:
        assert part['title'] == OLD
        part.setdefault('editorial_revision_history', []).append(dict(
            title=part['title'], title_rewrite=part['title_rewrite'],
            reason='原稿把可能相关写成确定因果，并引用了主持人提问；改用嘉宾完整原话'))
        # This is the existing exact-source-quote fallback, not a claimed CPU
        # verdict. The question on the actual video/cover is already faithful.
        item = dict(title='林园：' + QUOTE, cover_title=part['cover_title'],
                    subject=QUOTE, evidence=[QUOTE])
        copy = titles._package(item, raw, dict(method='source_quote', quote=QUOTE), [item])
        part.update(copy)
        part['cover_copy'] = dict(text=part['cover_title'], kind='editorial_claim', evidence=[QUOTE])
    assert titles.error(part['title'], part['title_rewrite'], raw) is None
    validate_guest_fixture(dict(fixture='linyuan_0913_landscape_title.json', **part))
    path.write_text(json.dumps(parts, ensure_ascii=False, indent=2))
    return dict(title=part['title'], cover=part['cover_title'],
                evidence=part['title_rewrite']['evidence'], method='source_quote',
                video_sha256=SHA, video_unchanged=True)


if __name__ == '__main__':
    print(json.dumps(repair(sys.argv[1]), ensure_ascii=False))
