"""Planning uses actual same-mother output, never requested labels as stock."""
import copy
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'/'fc'))
import index as fc


def parent(slug,sha):return dict(slug=slug,source_sha256=sha,output_layout='portrait')


def part(sha,width,height,**extra):
    return dict(source_sha256=sha,resolution=dict(width=width,height=height),
        render_mode='live_video_card',status='verified',**extra)


def test_actual_mothers_are_not_assigned_in_reverse_formats():
    # Actual public production receipts: d83 mother had three 720x1280 clips;
    # 6f5 mother had a 1920x1080 crop. This is planning, not new clip approval.
    portrait=parent('ly-0916-5cc2b6','1df744406655d5b19d20e13ccb0c8138955953aaa6d49978ee6fbb0053b320be')
    wide=parent('ly-0914-8791b8-clean0915','6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d')
    state=dict(published={})
    for p,w,h in [(portrait,720,1280),(wide,1920,1080)]:
        row=part(p['source_sha256'],w,h);row.update(status='published',bvid='actual-receipt',source_segments=[dict(start=1,end=30)])
        state['published'][p['slug']]=dict(parts=[row])
    candidates=[(portrait,dict(historical_mother=True)),(wide,dict(historical_mother=True))]
    needs=dict(portrait=2,landscape=2);before=copy.deepcopy((state,candidates,needs))
    plan=fc.experiment_reuse_plan(candidates,state,needs)
    assert [(p['slug'],layout) for p,r,layout in plan]==[(portrait['slug'],'portrait'),(wide['slug'],'landscape')]
    assert (state,candidates,needs)==before


def test_requested_portrait_and_mismatched_source_hash_are_not_observed_geometry():
    p=parent('one','a'*64)
    assert fc.experiment_reuse_layouts(p,{},dict(published={}))==set()
    record=dict(parts=[part('b'*64,720,1280)])
    assert fc.experiment_reuse_layouts(p,record,{})==set()
    assert fc.experiment_reuse_plan([(p,record)],{},dict(landscape=1,portrait=0))[0][2]=='landscape'


def test_unknown_geometry_retains_exploration_and_no_extra_dispatches():
    candidates=[(parent(str(i),'a'*64),{}) for i in range(5)]
    plan=fc.experiment_reuse_plan(candidates,{},dict(portrait=2,landscape=1))
    assert len(plan)==3 and len({p['slug'] for p,r,l in plan})==3
    assert [l for p,r,l in plan].count('portrait')==2
    assert [l for p,r,l in plan].count('landscape')==1
    assert fc.experiment_reuse_plan(candidates,{},dict(portrait=0,landscape=0))==[]


def test_verified_same_mother_format_precedes_unknown_or_incompatible_hint():
    p=parent('portrait','a'*64);u=parent('unknown','b'*64);w=parent('wide','c'*64)
    candidates=[(w,dict(parts=[part('c'*64,1280,720)])),(u,{}),
                (p,dict(parts=[part('a'*64,720,1280)]))]
    plan=fc.experiment_reuse_plan(candidates,{},dict(portrait=2,landscape=0))
    assert [(p['slug'],l) for p,r,l in plan]==[('portrait','portrait'),('unknown','portrait')]


def test_invalid_static_unverified_or_full_geometry_gives_no_preference():
    p=parent('one','a'*64)
    rows=[part('a'*64,720,720),part('a'*64,0,1280),part('a'*64,True,1280),
          part('a'*64,'720',1280),part('a'*64,720,1280,content_type='full_interview')]
    row=part('a'*64,720,1280);row['status']='rejected';rows.append(row)
    row=part('a'*64,720,1280);row['render_mode']='audio_card';rows.append(row)
    assert fc.experiment_reuse_layouts(p,dict(parts=rows),{})==set()


def test_unpublished_or_unbound_receipts_give_no_preference():
    p=parent('one','a'*64);row=part('a'*64,720,1280);row['status']='published'
    assert not fc.experiment_reuse_layouts(p,{},dict(published=dict(one=dict(parts=[row]))))
    row['bvid']='BVtest'
    assert not fc.experiment_reuse_layouts(p,{},dict(published=dict(one=dict(parts=[row]))))
