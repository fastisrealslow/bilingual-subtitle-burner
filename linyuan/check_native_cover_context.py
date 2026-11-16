"""Replay real accepted stock on original mother pixels; never publish/register."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

CASES=[
    dict(slug='ly-story-771f0eb10e',run=43699743489,delivery=14157686418,
         mother=14156246118,sha='7b5fd7cce9f08f5db46533425dabd079dcf66d0f21338b091d8dcd97323ce066',
         expected_rejection='现场原画封面存在黑色填充边'),
    dict(slug='ly-1116-3e733f',run=43858429518,delivery=14227391430,
         mother=14223386153,sha='b64b493504d1d9bb11d979c1cb99c194e684b30bc0fc097cd528bbab864de9ff',
         expected_rejection='现场原画封面人物身份或清晰度不足'),
    dict(slug='ly-1115-5bc4d3',run=43688837707,delivery=14155304155,
         mother=14156246118,mother_run=43699743489,
         sha='7b5fd7cce9f08f5db46533425dabd079dcf66d0f21338b091d8dcd97323ce066'),
]


@contextmanager
def capture_rejected_candidates(directory):
    """Keep actual input pixels for diagnosis; never alter a quality result."""
    import cv2
    import cover_scene_geometry as geometry
    import presentation
    original_border=geometry.border_exclusions
    original_save=presentation.save_scene_cover
    attempts=[]
    def borders(frames):
        marks,proof=original_border(frames)
        cv2.imwrite(str(directory/'native-first-sample.jpg'),frames[0])
        (directory/'native-border-proposal.json').write_text(json.dumps(proof,indent=2))
        return marks,proof
    def scene(image,path,face,identity):
        number=len(attempts)+1
        image.save(directory/f'native-scene-attempt-{number}.jpg')
        attempt=dict(face=list(face),identity=identity)
        attempts.append(attempt)
        try:
            return original_save(image,path,face,identity)
        except ValueError as exc:
            attempt['error']=str(exc)
            raise
        finally:
            (directory/'native-scene-attempts.json').write_text(json.dumps(attempts,ensure_ascii=False,indent=2))
    geometry.border_exclusions=borders
    presentation.save_scene_cover=scene
    try:yield
    finally:
        geometry.border_exclusions=original_border
        presentation.save_scene_cover=original_save


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for b in iter(lambda:handle.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def fetch(artifact,path,run,name=None):
    repo=os.environ['GITHUB_REPOSITORY']
    info=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/actions/artifacts/{artifact}'],timeout=60))
    if info['expired'] or info['workflow_run']['id']!=run or name and info['name']!=name:
        raise ValueError('Actual artifact provenance differs')
    with Path(path).open('wb') as handle:
        subprocess.run(['gh','api',f'repos/{repo}/actions/artifacts/{artifact}/zip'],
                       stdout=handle,check=True,timeout=360)


def main():
    import produce_cn as P
    from source_supply import validate_part
    out=Path('native-cover-context-verification');out.mkdir(exist_ok=True)
    report=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
                actual_treatment_stock_added=0,producer_sha256=sha(Path(P.__file__)),cases=[])
    def save():
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    save()
    for case in CASES:
        result=dict(slug=case['slug'],source_sha256=case['sha'],passed=False,
                    original_run_id=case['run'],original_delivery_artifact=case['delivery'])
        report['cases'].append(result);save()
        try:
            with tempfile.TemporaryDirectory(prefix='native-cover-stock-') as tmp:
                tmp=Path(tmp);delivery=tmp/'delivery.zip';mother_zip=tmp/'mother.zip'
                fetch(case['delivery'],delivery,case['run'],'deliver-'+case['slug'])
                bundle=tmp/'bundle';bundle.mkdir()
                with zipfile.ZipFile(delivery) as z:
                    for info in z.infolist():
                        if info.is_dir():continue
                        if Path(info.filename).name!=info.filename or '\\' in info.filename:
                            raise ValueError('Unsafe accepted bundle filename')
                        with z.open(info) as source,(bundle/info.filename).open('wb') as target:
                            shutil.copyfileobj(source,target)
                original_metadata=(bundle/'meta.json').read_bytes()
                rows=json.loads(original_metadata)
                if not isinstance(rows,list):rows=[rows]
                meta=rows[0]
                if meta.get('slug')!=case['slug'] or meta.get('source_sha256')!=case['sha']:
                    raise ValueError('Accepted media/source identity differs')
                error=validate_part(copy.deepcopy(meta),bundle)
                if error:raise ValueError('Original media validation: '+str(error))
                video_sha=sha(bundle/meta['final'])
                if video_sha!=(meta.get('fingerprints') or {}).get('sha256'):
                    raise ValueError('Accepted video bytes differ')
                spans=meta['segments']
                if len(spans)!=1:raise ValueError('This replay requires one original continuous interval')
                start,end=spans[0]['start'],spans[0]['end']
                result.update(title=meta['title'],start=start,end=end,video_sha256=video_sha,
                    original_profile=meta.get('editorial_profile'),old_cover_proof=meta['cover_proof'])
                dest=out/case['slug'];dest.mkdir()
                shutil.copyfile(bundle/meta['cover'],dest/'before.jpg')
                fetch(case['mother'],mother_zip,case.get('mother_run',case['run']))
                mother=tmp/'mother.mp4'
                with zipfile.ZipFile(mother_zip) as z:
                    names=[n for n in z.namelist() if Path(n).name=='video.mp4']
                    if len(names)!=1:raise ValueError('Unambiguous original mother required')
                    with z.open(names[0]) as source,mother.open('wb') as target:
                        shutil.copyfileobj(source,target)
                if sha(mother)!=case['sha']:raise ValueError('Original mother bytes differ')
                reference=P._download_speaker_reference(meta['speaker'],dest)
                # Deliberately nonexistent portrait body: the scene path must
                # use original pixels, not depend on a 632x470 body window.
                with capture_rejected_candidates(dest):
                    style,reason=P.make_live_clip_cover(mother,tmp/'unused-portrait.mp4',start,end,
                        meta['cover_title'],meta['speaker'],dest/'after.jpg',reference_path=reference,
                        style='scene',allow_editorial_fallback=False)
                proof=json.loads((dest/'after.jpg.proof.json').read_text())
                if (style!='scene' or reason is not None or any(proof.get(k) is not True for k in
                        ('no_added_text','no_black_bars','no_qr','face_fully_visible','feed_safe_face'))):
                    raise ValueError('Actual native scene did not pass all cover gates')
                if (bundle/'meta.json').read_bytes()!=original_metadata or sha(bundle/meta['final'])!=video_sha:
                    raise ValueError('Read-only replay changed accepted content')
                result.update(passed=True,cover_sha256=sha(dest/'after.jpg'),proof=proof,
                              accepted_video_and_metadata_unchanged=True)
                save()
        except Exception as exc:
            result.update(error=type(exc).__name__+': '+str(exc));save()
        result['expected_rejection']=case.get('expected_rejection')
        result['regression_passed']=(result['passed'] if not case.get('expected_rejection') else
            result.get('error')=='VisualQualityError: '+case['expected_rejection'])
        save()
    report['passed']=all(c['passed'] for c in report['cases']);save()
    report['regression_passed']=all(c['regression_passed'] for c in report['cases']);save()
    if not report['regression_passed']:raise ValueError('Native cover replay regression; no stock or publication approval')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
