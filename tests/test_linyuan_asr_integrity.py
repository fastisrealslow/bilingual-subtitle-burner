"""Speech content and cache regressions: numbers/negation must never disappear."""
import importlib.util
import hashlib
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('asr_integrity', Path(__file__).resolve().parents[1]/'linyuan/produce_cn.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_loop_cleanup_preserves_following_information():
    assert P._de_loop_text('我我我我买了100股，后来卖了20股') == '我我买了100股，后来卖了20股'


@pytest.mark.parametrize('second', ['今年的利润是200万元', '今年的利润不是100万元', '今年的亏损是100万元'])
def test_similar_statements_with_changed_facts_are_not_removed(second):
    cues = [dict(start=0, end=3, text='今年的利润是100万元'), dict(start=3, end=6, text=second)]
    assert P._dedup_consecutive(cues) == cues


def test_spoken_repetition_remains_but_overlapping_exact_duplicate_merges():
    a = dict(start=0, end=2, text='这笔钱不能动')
    b = dict(start=2, end=4, text=a['text'])
    assert P._dedup_consecutive([a, b]) == [a, b]
    assert P._dedup_consecutive([a, dict(b, start=1)]) == [dict(a, end=4)]


def test_ambiguous_normal_words_are_not_expanded_to_drug_names():
    text = '点击按钮，速效救心丸有速效作用，负利增长不能改成复利增长'
    assert P.fix_terms(text) == text


def test_legacy_smoothing_entry_point_never_calls_api(monkeypatch):
    def fail(*a, **k):
        pytest.fail('ASR processing must stay offline')
    monkeypatch.setattr(P, 'llm', fail)
    cues = [dict(start=0, end=2, text='2009年不是2019年')]
    assert P._llm_smooth_cues(cues, 'existing-production-key') == cues


def test_tokens_are_owned_before_grouping_across_seam():
    chunks = [dict(offset=0, duration=33, core_start=0, core_end=30,
                   tokens=['投', '资', '不', '能', '借', '钱'], timestamps=[28, 29, 29.8, 30.2, 30.6, 31]),
              dict(offset=27, duration=33, core_start=30, core_end=60,
                   tokens=['投', '资', '不', '能', '借', '钱', '。'], timestamps=[1, 2, 2.8, 3.2, 3.6, 4, 4.2])]
    tokens, timestamps = P._owned_sensevoice_tokens(chunks)
    assert ''.join(tokens) == '投资不能借钱。'
    cues = P._funasr_tokens_to_cues(tokens, timestamps, 0, 60)
    assert ''.join(c['text'] for c in cues) == '投资不能借钱。'
    assert cues[0]['start'] == 28
    assert cues[0]['end'] > 31


def test_missing_token_timestamps_are_rejected():
    with pytest.raises(ValueError, match='时间戳数量'):
        P._owned_sensevoice_tokens([dict(tokens=['一'], timestamps=[])])


def test_punctuation_after_capacity_flush_is_not_lost():
    tokens = list('投资之前必须先搞清楚企业的基本情况。')
    cues = P._funasr_tokens_to_cues(tokens, [i*.15 for i in range(len(tokens))], 0, 5)
    assert ''.join(c['text'] for c in cues) == ''.join(tokens)


def test_reviewed_phrase_keeps_its_explicit_end_time():
    cues=P._funasr_tokens_to_cues(
        ['没有一个人能守住，只是什么时候清算的事。'],[10],0,30,
        end_timestamps=[16.5])
    assert cues==[{'start':10,'end':16.5,
                   'text':'没有一个人能守住，只是什么时候清算的事。'}]


def test_cache_binds_audio_model_and_exact_output(tmp_path, monkeypatch):
    src = tmp_path/'input.mp4'
    src.write_bytes(b'first audio')
    model = tmp_path/'model'
    model.mkdir()
    (model/'model.int8.onnx').write_bytes(b'first model')
    monkeypatch.setattr(P, 'SENSEVOICE_DIR', str(model))
    monkeypatch.setattr(P, 'ASR_BACKEND', 'sensevoice')
    monkeypatch.setattr(P, '_audio_duration', lambda _: 3)
    calls = []
    def decode(src, work):
        calls.append(1)
        return [dict(start=0, end=3, text='投资之前必须先搞清楚企业的基本情况')]
    monkeypatch.setattr(P, '_transcribe_sensevoice', decode)
    work = tmp_path/'work'
    P.transcribe(src, work)
    P.transcribe(src, work)
    assert len(calls) == 1
    src.write_bytes(b'other audio')
    (work/'audio_16k.wav').write_bytes(b'stale PCM')
    (work/'highlights_part1.json').write_text('[{"start":0,"end":10}]')
    (work/'translation.json').write_text('["stale translation"]')
    P.transcribe(src, work)
    assert len(calls) == 2 and not (work/'audio_16k.wav').exists()
    assert not (work/'highlights_part1.json').exists()
    assert not (work/'translation.json').exists()
    assert len(list((work/'asr_stale').glob('*/highlights_part1.json'))) == 1
    (model/'model.int8.onnx').write_bytes(b'other model')
    P.transcribe(src, work)
    assert len(calls) == 3
    (work/'cues_raw.json').write_text(json.dumps([dict(start=0, end=3, text='wrong cached result')]))
    P.transcribe(src, work)
    assert len(calls) == 4


def test_unknown_backend_does_not_silently_load_large_v3(tmp_path, monkeypatch):
    monkeypatch.setattr(P, 'ASR_BACKEND', 'typo')
    with pytest.raises(ValueError, match='CPU 离线'):
        P.transcribe(tmp_path/'unused', tmp_path)


def test_qwen_verified_evidence_can_be_reused_without_weight_files(tmp_path, monkeypatch):
    src = tmp_path/'input.mp4'
    src.write_bytes(b'exact mother video')
    source_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    base = tmp_path/'linyuan'
    base.mkdir()
    (base/'reviewed_asr_corrections.py').write_text('VERSION = 1\n')
    (base/'asr_production_config.json').write_text(json.dumps({
        'backend': 'qwen3',
        'model_revisions': {'asr': 'asr-revision', 'aligner': 'align-revision'},
    }))
    evidence = tmp_path/'evidence'/'0'
    evidence.mkdir(parents=True)
    report = dict(source_video_sha256=source_sha, device='cpu',
        networking_during_inference=False, model_id='Qwen/Qwen3-ASR-0.6B',
        model_revision='asr-revision', alignment=dict(device='cpu',
            networking_during_inference=False,
            model_id='Qwen/Qwen3-ForcedAligner-0.6B',
            model_revision='align-revision'), chunks=[])
    (evidence/'aligned.json').write_text(json.dumps(report))
    monkeypatch.setattr(P, 'BASE', base)
    monkeypatch.setattr(P, 'ASR_BACKEND', 'qwen3')
    monkeypatch.setenv('QWEN3_EVIDENCE_DIR', str(evidence.parent))
    monkeypatch.delenv('QWEN3_ASR_DIR', raising=False)
    monkeypatch.delenv('QWEN3_ALIGNER_DIR', raising=False)
    identity = P._asr_cache_identity(src)
    assert identity['source_sha256'] == source_sha
    assert identity['model_revisions']['asr'] == 'asr-revision'
    assert identity['evidence_sha256'] == [hashlib.sha256((evidence/'aligned.json').read_bytes()).hexdigest()]
    moved=tmp_path/'another-run';moved.mkdir()
    (moved/'aligned.json').write_bytes((evidence/'aligned.json').read_bytes())
    monkeypatch.setenv('QWEN3_EVIDENCE_DIR',str(moved))
    assert P._asr_cache_identity(src)==identity
    monkeypatch.delenv('QWEN3_EVIDENCE_DIR')
    monkeypatch.chdir(tmp_path)
    # An unset evidence directory must never scan the entire working tree.
    with pytest.raises(ValueError,match='缺少已验证'):
        P._asr_cache_identity(src)


def test_qwen_evidence_with_unpinned_revision_is_rejected(tmp_path, monkeypatch):
    src = tmp_path/'input.mp4'
    src.write_bytes(b'exact mother video')
    source_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    base = tmp_path/'linyuan'
    base.mkdir()
    (base/'reviewed_asr_corrections.py').write_text('VERSION = 1\n')
    (base/'asr_production_config.json').write_text(json.dumps({
        'backend': 'qwen3',
        'model_revisions': {'asr': 'expected', 'aligner': 'align-revision'},
    }))
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'aligned.json').write_text(json.dumps(dict(
        source_video_sha256=source_sha, device='cpu', networking_during_inference=False,
        model_id='Qwen/Qwen3-ASR-0.6B', model_revision='different',
        alignment=dict(device='cpu', networking_during_inference=False,
            model_id='Qwen/Qwen3-ForcedAligner-0.6B', model_revision='align-revision'),
        chunks=[])))
    monkeypatch.setattr(P, 'BASE', base)
    monkeypatch.setattr(P, 'ASR_BACKEND', 'qwen3')
    monkeypatch.setenv('QWEN3_EVIDENCE_DIR', str(evidence))
    with pytest.raises(ValueError, match='固定模型版本'):
        P._asr_cache_identity(src)


def test_cue_cache_migration_keeps_alignment_and_bound_editing_caches(tmp_path,monkeypatch):
    src=tmp_path/'video.mp4';src.write_bytes(b'exact mother')
    sha=hashlib.sha256(src.read_bytes()).hexdigest()
    base=tmp_path/'base';base.mkdir();work=tmp_path/'new-slug';work.mkdir()
    (base/'reviewed_asr_corrections.py').write_text('VERSION=1')
    revisions=dict(asr='asr-rev',aligner='align-rev')
    (base/'asr_production_config.json').write_text(json.dumps(dict(model_revisions=revisions)))
    raw=work/'qwen_cpu';raw.mkdir()
    report=dict(source_video_sha256=sha,device='cpu',networking_during_inference=False,
        model_id='Qwen/Qwen3-ASR-0.6B',model_revision='asr-rev',chunks=[],
        alignment=dict(device='cpu',networking_during_inference=False,
            model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align-rev'))
    (raw/'aligned.json').write_text(json.dumps(report))
    cues=[dict(start=0,end=4,text='投资之前必须先搞清楚企业的基本情况')]
    (work/'cues_raw.json').write_text(json.dumps(cues))
    (work/'asr_cache.json').write_text(json.dumps(dict(identity=dict(source_sha256=sha,evidence={'/old/run': 'old'}))))
    (work/'highlights_block_1.json').write_text('{"identity":"independently validated later"}')
    monkeypatch.setattr(P,'BASE',base);monkeypatch.setattr(P,'ASR_BACKEND','qwen3')
    monkeypatch.delenv('QWEN3_EVIDENCE_DIR',raising=False)
    monkeypatch.setattr(P,'_audio_duration',lambda _:4)
    calls=[]
    def rebuild(_src,_work):
        assert (raw/'aligned.json').exists()
        calls.append('rebuild cues from immutable alignment')
        return cues
    monkeypatch.setattr(P,'_transcribe_qwen_cpu',rebuild)
    assert P.transcribe(src,work)==cues
    assert P.transcribe(src,work)==cues
    assert len(calls)==1 and (work/'highlights_block_1.json').exists()
