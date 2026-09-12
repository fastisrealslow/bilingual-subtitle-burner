"""Measure usable inventory from actual deliverables, independently of jobs."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).parent/'fc'))
import index as fc
import source_outcomes
import headline_policy
import live_motion

VERSION = 1
INVENTORY = Path(__file__).parent/'.automation/source_inventory.json'


def api(path):
    return json.loads(subprocess.check_output(['gh','api',f'repos/{fc.REPO}/{path}']))


def validate_part(meta, directory):
    """A job exit code or metadata approval flag alone is insufficient."""
    name = meta.get('final')
    if not isinstance(name, str) or Path(name).name != name:
        return 'Invalid final filename'
    path = Path(directory)/name
    if not path.is_file():
        return 'Actual MP4 missing'
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != (meta.get('fingerprints') or {}).get('sha256'):
        return 'Actual MP4 fingerprint mismatch'
    if meta.get('render_mode') == 'live_video_card':
        # Revalidate old stock from actual encoded pixels. Only this measured,
        # SHA-bound proof may supplement legacy metadata at publication time.
        try:
            motion = live_motion.verify_window(path, live_motion.window_for_meta(meta))
        except ValueError as exc:
            return '真人动作验收失败：' + str(exc)
        meta.setdefault('final_live_identity', {})['motion'] = motion
    error = (fc.artifact_quality_error(meta) or fc.artifact_subtitle_error(meta, directory)
             or fc.artifact_cover_error(meta, directory))
    if error:
        return error
    error = fc.editorial.metadata_error(meta, fc.mp4_duration(path))
    if error:
        return error
    # Read the encoded dimensions, so a claimed landscape flag cannot fill 14:00.
    dimensions = json.loads(subprocess.check_output(['ffprobe','-v','error',
        '-select_streams','v:0','-show_entries','stream=width,height',
        '-of','json',str(path)], timeout=30))['streams'][0]
    declared = meta.get('resolution') or {}
    if any(dimensions[key] != declared.get(key) for key in ('width','height')):
        return 'Actual MP4 dimensions differ from metadata'
    probe = subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),
        '-map','0:v:0','-map','0:a:0','-f','null','-'], capture_output=True, timeout=120)
    if probe.returncode:
        return 'Actual MP4 audio/video decode failed'
    return None


def audit_materials(items):
    result={}
    for item in items:
        src=item.get('source','unknown')
        counts=result.setdefault(src,Counter())
        counts['records']+=1
        extra=item.get('extra') or {}
        if isinstance(extra,str):
            try:extra=json.loads(extra)
            except ValueError:extra={}
        if extra.get('source_role')=='reference' or src=='competitor_reference':
            counts['reference_only']+=1
            continue
        url=item.get('url') or ''
        video=bool(item.get('video_url') or extra.get('video_url') or extra.get('mp4_url')
                   or extra.get('has_video') or 'bilibili.com/video/' in url or 'news.qq.com' in url)
        if not video:
            continue
        counts['video_candidates']+=1
        try:duration=float(extra.get('duration') or 0)
        except (TypeError,ValueError):duration=0
        counts['duration_unknown' if duration<=0 else 'too_short' if duration<120
               else 'over_limit' if duration>5400 else 'duration_eligible']+=1
        if 600<=duration<=5400:counts['long_mother_candidates']+=1
    return result


def inventory_counts(records, state):
    latest={e['slug']:e for e in fc._latest_dispatches(state)}
    live=audio=landscape=0
    for record in records:
        slug=record['slug']; entry=latest.get(slug,{})
        if slug in fc.REVIEW_PAUSED_SLUGS or entry.get('failed'):
            continue
        completed=fc.processed_part_indices(entry)
        for part in record.get('parts',[]):
            if part.get('status')!='verified' or part['index'] in completed:
                continue
            if part['render_mode']=='audio_card':audio+=1
            else:live+=1
            if fc.is_landscape(part) and part.get('content_type')!='full_interview':landscape+=1
    return dict(verified_live=live,verified_audio_card=audio,verified_landscape=landscape,
                landscape_target=fc.TARGET_LANDSCAPE_RESERVE,
                daily_mix_usable=live, target_reserve=fc.TARGET_READY_RESERVE)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--max-new',type=int,default=4)
    args=parser.parse_args()
    state=fc.load_state()
    if not state.get('dispatched'):
        raise SystemExit('Production state unavailable; do not replace inventory with empty state')
    previous=json.loads(INVENTORY.read_text()) if INVENTORY.exists() else {}
    validation_sha=hashlib.sha256(Path(__file__).read_bytes()+Path(source_outcomes.__file__).read_bytes()+Path(fc.editorial.__file__).read_bytes()
                                 +Path(fc.__file__).read_bytes()+Path(headline_policy.__file__).read_bytes()+Path(live_motion.__file__).read_bytes()).hexdigest()
    old={r['artifact_id']:r for r in previous.get('artifacts',[])} if (
        previous.get('version')==VERSION and previous.get('validation_sha256')==validation_sha
        and previous.get('quality_gate_version')==fc.QUALITY_GATE_VERSION) else {}
    rules_changed=previous.get('validation_sha256')!=validation_sha
    candidates={e['slug']:e for e in fc._latest_dispatches(state)
                if not e.get('failed') and e.get('production_rules_version')==fc.PRODUCTION_RULES_VERSION
                and e['slug'] not in fc.REVIEW_PAUSED_SLUGS}
    # The artifact index survives beyond the most recent 30 production runs.
    # Metadata refresh can therefore no longer make older reserve clips vanish.
    found={}
    for page in range(1,4):
        rows=api(f'actions/artifacts?per_page=100&page={page}').get('artifacts',[])
        for a in rows:
            slug=a['name'].removeprefix('deliver-')
            if a['name'].startswith('deliver-') and slug in candidates and not a.get('expired'):
                found.setdefault(slug,a)
        if len(rows)<100:break
    # A rule upgrade invalidates cached approvals, but must not publish a
    # partially rechecked stock count (four new batches used to hide older
    # reserves and trigger unnecessary refills). Finish the full recheck first.
    validation_budget=len(found) if rules_changed else args.max_new
    records=[];checked=0
    for slug,a in found.items():
        if a['id'] in old:
            records.append(old[a['id']]);continue
        if checked>=validation_budget:continue
        checked+=1
        record=dict(slug=slug,artifact_id=a['id'],run_id=a['workflow_run']['id'],
                    checked_at=int(time.time()),source_url=candidates[slug].get('source_url'),parts=[])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive=Path(tmp)/'artifact.zip'
                fc.download_reviewed_zip(a['id'],archive,attempts=2)
                with zipfile.ZipFile(archive) as z:
                    # Extract only flat actual delivery files; no prefixed copy,
                    # traversal paths, symlinks or arbitrary executable content.
                    for info in z.infolist():
                        n=info.filename
                        if '/' in n or '\\' in n or n.startswith(slug+'.') or info.is_dir():continue
                        if n=='meta.json' or n.endswith(('.mp4','.ass','.jpg')):
                            (Path(tmp)/n).write_bytes(z.read(info))
                data=json.loads((Path(tmp)/'meta.json').read_text())
                metas=data if isinstance(data,list) else [data]
                for i,m in enumerate(metas):
                    if i in fc.processed_part_indices(candidates[slug]):continue
                    error=validate_part(m,tmp)
                    record['parts'].append(dict(index=i,final=m.get('final'),title=m.get('title'),
                        duration_sec=m.get('duration_sec'),render_mode=m.get('render_mode'),
                        content_type=m.get('content_type'),resolution=m.get('resolution') or {},
                        source_sha256=m.get('source_sha256'),segments=m.get('segments'),
                        sha256=(m.get('fingerprints') or {}).get('sha256'),
                        subtitle_sha256=m.get('subtitle_text_sha256'),
                        motion=(m.get('final_live_identity') or {}).get('motion'),
                        status='rejected' if error else 'verified',reason=error))
            records.append(record)
        except Exception as exc:
            # A network failure is retryable. Do not cache it as a media verdict.
            print('Inventory transfer/validation unavailable',slug,type(exc).__name__)
    # Keep unexpired previously inspected reserves not reached by pagination.
    current_slugs={r['slug'] for r in records}
    records.extend(r for r in old.values() if r['slug'] in candidates and r['slug'] not in current_slugs
                   and r['slug'] not in found and time.time()-r['checked_at']<80*86400)
    payload=json.loads((Path(__file__).parent/'dashboard/data.json').read_text())
    items=payload if isinstance(payload,list) else payload.get('items',[])
    result=dict(version=VERSION,validation_sha256=validation_sha,quality_gate_version=fc.QUALITY_GATE_VERSION,
        editorial_policy_version=fc.editorial.VERSION,updated_at=int(time.time()),
        materials=audit_materials(items),inventory=inventory_counts(records,state),
        source_outcomes=source_outcomes.audit(state,records,fc._latest_dispatches(state),
            fc.processed_part_indices,fc.REVIEW_PAUSED_SLUGS),
        in_flight_placeholders=fc._pending_final_count(state),artifacts=records)
    INVENTORY.parent.mkdir(parents=True,exist_ok=True)
    INVENTORY.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['updated_at','inventory','materials']},ensure_ascii=False))


if __name__=='__main__':main()
