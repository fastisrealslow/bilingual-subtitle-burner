"""An unhandled workflow failure is recoverable, never a content verdict."""
import json
import os
from pathlib import Path


def alignment_failure(work):
    """Carry source-bound aligner diagnostics without accepting its timings."""
    work=Path(work)
    try:
        error=json.loads((work/'qwen_cpu'/'alignment_error.json').read_text())
        source=json.loads((work/'source_quality.json').read_text())
        recognition=json.loads((work/'qwen_cpu'/'recognition.json').read_text())
        if (error.get('version')!=1 or not error.get('source_video_sha256')
                or error['source_video_sha256']!=source.get('source_sha256')
                or error['source_video_sha256']!=recognition.get('source_video_sha256')
                or not error.get('source_pcm_sha256')
                or error['source_pcm_sha256']!=recognition.get('source_pcm_sha256')
                or not error.get('model_revision')
                or not isinstance(error.get('invalid_words'),list)
                or not error['invalid_words']
                or not isinstance(error.get('word_count'),int)
                or not 0<len(error['invalid_words'])<=error['word_count']):
            return None
        chunk=next((c for c in recognition['chunks']
                    if c.get('core_start')==error.get('core_start')),None)
        if not chunk or any(chunk.get(k)!=error.get(k) for k in ('offset','duration','text')):
            return None
        # An error left over from a previous attempt must not mask a later
        # selection/render failure after this core was successfully aligned.
        aligned=work/'qwen_cpu'/'aligned.json'
        if aligned.exists():
            prior=json.loads(aligned.read_text())
            if any(c.get('core_start')==error['core_start'] and 'words' in c
                   for c in prior.get('chunks',[])):
                return None
        return {k:error[k] for k in ('source_video_sha256','source_pcm_sha256',
            'model_revision','core_start','offset','duration','word_count','invalid_words')}
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return None


def report_missing(path, steps, slug):
    path=Path(path)
    if path.exists():return False
    failed=[name for name, row in steps.items() if row.get('outcome')=='failure']
    if not failed or any(name in {'src','source_gate'} for name in failed):return False
    rejection=dict(stage='workflow-runtime',error_type='WorkflowRuntimeFailure',retryable=True,
        reason='工作流步骤失败，未形成素材不合格结论：'+','.join(failed))
    diagnostic=alignment_failure(path.parent/'_tmp') if failed==['render'] else None
    if diagnostic:
        rejection.update(stage='asr-alignment',error_type='InvalidWordTiming',
            reason=(f"ASR强制对齐时序无效：core {diagnostic['core_start']}，"
                    f"{len(diagnostic['invalid_words'])}/{diagnostic['word_count']} 个词；"
                    '未形成素材不合格结论'),alignment_diagnostic=diagnostic)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(slug=slug,accepted=0,accepted_finals=[],
        selection_completed=False,retryable=True,rejected=[rejection]),ensure_ascii=False))
    return True


if __name__=='__main__':
    slug=os.environ['RUN_SLUG']
    report_missing(Path('deliver')/slug/'batch_report.json',
                   json.loads(os.environ['PRODUCTION_STEPS']),slug)
