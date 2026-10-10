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


def test_real_9b_control_must_keep_visited_company_scope_and_negation():
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/packaging-20261010/recent-title-corpus.json'
    case=json.loads(path.read_text())[2]
    source=''.join(c['text'] for c in case['cues'])
    assert T.research_scope_error('林园：上市企业整体不错，经营压力不大，大家信心很足',
        '上市企业不错，经营压力不大',source)
    assert T.research_scope_error('林园：我走访的上市企业经营压力不大',
        '走访公司经营压力不大',source) is None
    assert T.research_scope_error('林园：普通消费表现符合预期','普通消费符合预期',source)
    assert T.research_scope_error('林园：普通消费没有预期好，但也不错',
        '普通消费没预期好但也不错',source) is None


def test_second_real_canary_cannot_turn_buy_or_research_into_definite_buy():
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/packaging-20261010/recent-title-corpus.json'
    source=''.join(c['text'] for c in json.loads(path.read_text())[1]['cues'])
    assert T.relation_error('林园：体验是基本方法，吃了感觉好就买公司',
        '体验是基本方法，吃了感觉好就买公司',source)
    assert T.relation_error('林园：亲身试吃，再判断公司',
        '亲身试吃再判断公司',source) is None
    assert T.relation_error('林园：吃了感觉好就买公司或者去调研',
        '吃了感觉好就买公司或调研',source) is None
    assert T.relation_error('林园：基本做法是亲自尝一尝，舒服了就去调研',
        '基本做法是亲自尝一尝',source)
    assert T.relation_error('林园：让大家都吃，感觉好才去投资相关公司',
        '让大家都吃，感觉好才去投资相关公司',source)
