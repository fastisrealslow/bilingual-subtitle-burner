"""Actual CPU title production must reject inferred trading commands."""
import hashlib
import json
import subprocess
from pathlib import Path
import urllib.request


def main():
    import produce_cn as P
    import title_rewrite as T
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b' and P.automatic_only()
    fixture=json.loads(Path('tests/fixtures/linyuan_inferred_trade_directive.json').read_text())
    cues=fixture['cues'];text=''.join(c['text'] for c in cues)
    out=Path('title-trade-directive-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,source_run_id=fixture['source_run_id'],
        source_sha256=fixture['source_sha256'],registered_stock=False,published=False,
        original_cues_sha256=hashlib.sha256(json.dumps(cues,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    def save():(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    raw=subprocess.check_output(['gh','api',
        'repos/fastisrealslow/bilingual-subtitle-burner/contents/linyuan/title_rewrite.py?ref=66f3d4c49aa881b5e54b4775504db465cb79c61b',
        '-H','Accept: application/vnd.github.raw+json'],timeout=60)
    assert hashlib.sha256(raw).hexdigest()=='6b7f774af6ebe16ed3e554c26e185002540ce75270423386fd9da2fbc2ebf77a'
    old=dict(__name__='actual_old_title_policy')
    exec(compile(raw,'actual-old-title-policy','exec'),old)
    result['control_error']=old['error'](fixture['old_title'],fixture['old_proof'],text)
    result['current_old_proof_error']=T.error(fixture['old_title'],fixture['old_proof'],text)
    save()
    assert result['control_error'] is None and '买卖指令' in result['current_old_proof_error']
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:
        tags=json.load(response)
    digest=next(m['digest'] for m in tags['models'] if m['name']==P.LOCAL_LLM_MODEL)
    assert digest=='56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90'
    result['model_digest']=digest
    # Use the production writer; no preferred/old title or manually selected
    # candidate is passed to the model. Raw responses remain in this artifact.
    result['copy']=P.copywrite(cues,list(range(len(cues))),'林园','访谈','',out,
        source_sha256=fixture['source_sha256'])
    save()
    copy=result['copy']
    assert not T.error(copy['title'],copy['title_rewrite'],text)
    assert not T.unsupported_trade_directive_error(copy['title'],copy['cover_title'],text)
    assert list(out.glob('title_response*.txt')),'No actual CPU writer responses were retained'
    assert hashlib.sha256(json.dumps(cues,sort_keys=True,ensure_ascii=False).encode()).hexdigest()==result['original_cues_sha256']
    result['passed']=True;save()
    print(json.dumps(dict(passed=True,title=copy['title'],cover=copy['cover_title'],
        registered_stock=False,published=False),ensure_ascii=False))


if __name__=='__main__':main()
