import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import editorial_policy as E
import title_rewrite as T


def test_recent_replay_uses_exact_original_title_inputs_and_one_attribution_control():
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/packaging-20261010/recent-title-corpus.json'
    corpus=json.loads(path.read_text())
    assert [case['id'] for case in corpus]==[
        'oct10-published-14','oct10-published-16','host-premise-control']
    for case in corpus:
        text=''.join(cue['text'] for cue in case['cues'])
        assert E.text_digest(text)==case['transcript_sha256']
        assert all(a['end']<=b['start'] for a,b in zip(case['cues'],case['cues'][1:]))
    for case in corpus[:2]:
        text=''.join(cue['text'] for cue in case['cues'])
        assert hashlib.sha256(T.compact(text).encode()).hexdigest()==case['original_title_source_sha256']
        assert case['raw_asr_artifact_id']>0 and len(case['source_sha256'])==64


def test_observed_9b_false_positive_cannot_borrow_most_from_basic_experience():
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/packaging-20261010/recent-title-corpus.json'
    case=json.loads(path.read_text())[1]
    transcript=''.join(c['text'] for c in case['cues'])
    candidate=dict(title='林园：嘴巴最容易搞懂，先吃再判断公司',
        cover_title='先吃再判断才是基本体验',subject='嘴巴',
        evidence=['对啊，这是最基本的体验嘛。','嗯，就体验非常重要。','那嘴巴为什么容易搞？'])
    assert '原文没有的比较' in T._candidate_error(candidate,transcript,'林园',(),check_layout=False)
