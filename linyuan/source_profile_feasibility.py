"""Necessary experimental-cover dimensions only; never a media/story approval."""
import json
import os
from pathlib import Path
import re


def native_scene_unavailable(width,height):
    # Keep these necessary limits identical to live_tracking.scene_cover_crop.
    # Boundary regressions call that real crop gate too. Do not infer a usable
    # face, clean image or finished cover from passing these three limits.
    if width<960 or height<540 or width/height<1.35:
        return '原始画面不足以提供清晰横向现场封面'
    return None


def profile_for_source(source_report,work):
    """Keep the original request, using ordinary QA for known impossible T.

    Run39906333491 classified 42 candidates on a native 852x480 source that
    could never supply the mandatory 960x540 scene cover. This skips that
    classification expense; all baseline argument/ASR/picture/copy/media gates
    still run, and actual treatment acceptance remains zero for the fallback.
    """
    from publishing_experiment import profile,TREATMENT,BASELINE,SELECTION_REVISION
    requested=profile()
    if requested!=TREATMENT or not isinstance(source_report,dict) or source_report.get('passed') is not True:
        return requested
    size=source_report.get('resolution') or {}
    if not isinstance(size,dict):return requested
    width,height=size.get('width'),size.get('height');source_sha=source_report.get('source_sha256')
    if (type(width) is not int or type(height) is not int or min(width,height)<=0
            or not isinstance(source_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',source_sha)):
        return requested  # Unknown is not permission for a guessed reroute.
    reason=native_scene_unavailable(width,height)
    if reason is None:return requested
    report=dict(requested_profile=requested,actual_route=BASELINE,revision=SELECTION_REVISION,
        source_sha256=source_sha,source_resolution=dict(width=width,height=height),
        classification_skipped=True,reason='native_scene_cover_geometry_unavailable',
        detail=reason,final_quality_approved=False)
    (Path(work)/'source_profile_feasibility.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    os.environ['LINYUAN_EDITORIAL_PROFILE']=BASELINE
    print('[实验选题] 原片无法提供原像素横向现场封面；按常规片继续验收，不消耗故事分类预算，不计作实验片',flush=True)
    return requested
