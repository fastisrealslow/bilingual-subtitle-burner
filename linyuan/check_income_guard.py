"""Replay the actual baseline false approval, without model/media approval."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def verify(archives):
    import title_rewrite as title
    import publishing_experiment as experiment
    with zipfile.ZipFile(archives[12428005891]) as z:
        raw=json.loads(z.read('_tmp/cues_raw.json'))
        bad=json.loads(z.read('_tmp/copywrite_5.json'))
        quality=json.loads(z.read('_tmp/source_quality.json'))
    with zipfile.ZipFile(archives[12417458565]) as z:
        old_raw=json.loads(z.read('_tmp/cues_raw.json'))
        positive=json.loads(z.read('_tmp/copywrite_1.json'))
        selected=json.loads(z.read('_tmp/story_selection.json'))['selected']
    assert raw==old_raw
    assert quality['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
    pick=next(row for row in selected if row['story_selection']['kind']=='company_case')
    source=''.join(c['text'] for c in raw[pick['start']:pick['end']+1])
    assert bad['title']=='林园：投资是艺术，选茅台因收入爆发期'
    assert title.income_boom_error(bad['title'],bad['cover_title'],source)
    assert title.forecast_copy_error(bad['title'],bad['cover_title'],source)
    assert title.error(bad['title'],bad['title_rewrite'],source)
    assert positive['title']=='林园：当年投茅台是消费升级理论的选择'
    assert title.forecast_copy_error(positive['title'],positive['cover_title'],source) is None
    assert experiment.title_focus_error(positive['title'],experiment.title_focus(pick['story_selection']),cover=positive['cover_title']) is None
    return dict(source_sha256=quality['source_sha256'],
        source_digest=experiment.digest(source),
        title_policy_sha256=hashlib.sha256(Path(title.__file__).read_bytes()).hexdigest(),
        actual_baseline_false_approval_rejected=True,actual_positive_not_blocked_by_this_guard=True,
        diagnostic_only=True,media_approved=False,registered_stock=False,published=False,passed=True)


def main():
    with tempfile.TemporaryDirectory(prefix='income-original-') as tmp:
        archives={}
        for artifact in (12428005891,12417458565):
            path=Path(tmp)/(str(artifact)+'.zip')
            with path.open('wb') as handle:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                    stdout=handle,check=True,timeout=180)
            archives[artifact]=path
        result=verify(archives)
    Path('income-guard-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
