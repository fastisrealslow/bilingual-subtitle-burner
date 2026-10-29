"""Replay the exact 29 October failed title with uncached production CPU review."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
import publishing_experiment as E
from reuse_title_verification import validate_candidate_pool
from title_rewrite import error


def main():
    fixture=json.loads(Path('tests/fixtures/linyuan_story_title_20261029.json').read_text())
    units=fixture['source_cues'];selection=fixture['story_evidence']
    assert E.digest(''.join(units))==selection['transcript_sha256']
    cues=[dict(text=t,start=i,end=i+1) for i,t in enumerate(units)]
    report=dict(source_run_id=fixture['source_run_id'],source_sha256=fixture['source_sha256'],
        fixture_sha256=E.digest(fixture),text_only=True,accepted_mp4_count=0,production_dispatch=False,
        model=p.LOCAL_LLM_MODEL,calls=[],passed=False)
    original=p.llm
    def uncached(*args,**kwargs):
        kwargs['read_cache']=False
        request=dict(prompt=args[0],schema=kwargs.get('response_schema'))
        response=original(*args,**kwargs)
        report['calls'].append(dict(**request,response=response))
        Path('story-title-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return response
    p.llm=uncached
    os.environ['LINYUAN_EDITORIAL_PROFILE']=E.TREATMENT
    began=time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as directory:
            result=p.copywrite(cues,list(range(len(cues))),'林园','访谈','',Path(directory),
                source_sha256=fixture['source_sha256'],story_evidence=selection)
        validate_candidate_pool(result)
        assert result['title_rewrite']['review']['method']=='cpu_text_review'
        assert not error(result['title'],result['title_rewrite'],''.join(units))
        assert not E.title_focus_error(result['title'],E.title_focus(selection),cover=result['cover_title'])
        assert '招商银行' in result['title']
        report.update(passed=True,result=result)
    except Exception as exc:
        report['error']=str(exc)
        raise
    finally:
        report['elapsed_seconds']=round(time.monotonic()-began,2)
        Path('story-title-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in {'calls','result'}},ensure_ascii=False))


if __name__=='__main__':main()
