"""A selected ZIP part must be exact, bounded and independent of other MP4s."""
import io
import json
from pathlib import Path
import re
import sys
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import artifact_range as ar


class Response:
    def __init__(self, data, start, end, total, status=206):
        self.data=data
        self.status_code=status
        self.headers={'Content-Range':f'bytes {start}-{end}/{total}'}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):
        for i in range(0,len(self.data),size):yield self.data[i:i+size]


class Session:
    def __init__(self,data,mutate=None):self.data=data;self.ranges=[];self.mutate=mutate
    def get(self,url,**kwargs):
        start,end=map(int,re.fullmatch(r'bytes=(\d+)-(\d+)',kwargs['headers']['Range']).groups())
        assert kwargs['stream'] and kwargs['headers']['Accept-Encoding']=='identity'
        self.ranges.append((start,end))
        result=Response(self.data[start:end+1],start,end,len(self.data))
        if self.mutate:self.mutate(result,len(self.ranges))
        return result


def bundle(**overrides):
    meta=[dict(final='final_3.mp4',cover='cover_3.jpg',subtitle_files=['subtitles_3.ass'],
               subtitle_edit_proof_version=1,subtitle_edit_proofs=['subtitle_edit_proof_3.json'],**overrides)]
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_STORED) as z:
        z.writestr('meta.json',json.dumps(meta))
        z.writestr('unrelated.mp4',b'X'*(5*1024**2))
        z.writestr('final_3.mp4',b'exact selected video'*200)
        z.writestr('cover_3.jpg',b'cover')
        z.writestr('subtitles_3.ass',b'actual original ASS')
        z.writestr('subtitle_edit_proof_3.json',b'{"original":"original proof"}')
        if overrides.get('cover_proof'):
            z.writestr('cover_3_feed_square.jpg',b'actual centre crop')
            z.writestr('cover_3_list_160.jpg',b'actual list thumbnail')
    return data.getvalue(),meta


def modern_bundle():
    return bundle(cover_proof=dict(feed_safe_crop=[280,0,1000,720],
        feed_square='cover_3_feed_square.jpg',thumbnail='cover_3_list_160.jpg'))


def test_range_transfer_preserves_both_actual_feed_surfaces(tmp_path):
    data,meta=modern_bundle()
    result=ar.extract_part('https://storage.test/immutable.zip',0,tmp_path,session=Session(data))
    assert json.loads((tmp_path/'meta.json').read_bytes())==meta
    assert (tmp_path/'cover_3_feed_square.jpg').read_bytes()==b'actual centre crop'
    assert (tmp_path/'cover_3_list_160.jpg').read_bytes()==b'actual list thumbnail'
    assert result['selected_files']==7


@pytest.mark.parametrize('name',[None,'../outside.jpg','..\\outside.jpg','meta.json','cover_3.jpg'])
def test_invalid_feed_surface_name_fails_before_any_files_are_promoted(tmp_path,name):
    data,_=bundle(cover_proof=dict(feed_safe_crop=[280,0,1000,720],feed_square=name))
    with pytest.raises(ValueError):
        ar.extract_part('https://storage.test/x',0,tmp_path,session=Session(data))
    assert not list(tmp_path.iterdir())


def test_missing_actual_feed_surface_never_promotes_partial_delivery(tmp_path):
    data,_=bundle(cover_proof=dict(feed_safe_crop=[280,0,1000,720],feed_square='missing.jpg'))
    with pytest.raises(KeyError):
        ar.extract_part('https://storage.test/x',0,tmp_path,session=Session(data))
    assert not list(tmp_path.iterdir())


def test_whole_bundle_fallback_preserves_actual_feed_surfaces(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import index as fc
    data,meta=modern_bundle()
    monkeypatch.setattr(fc,'download_inventory_range_part',lambda *a:False)
    monkeypatch.setattr(fc.tempfile,'gettempdir',lambda:str(tmp_path))
    monkeypatch.setattr(fc,'download_reviewed_zip',lambda aid,path,**k:path.write_bytes(data))
    assert fc.download_inventory_part(123,0,tmp_path)
    assert json.loads((tmp_path/'meta.json').read_bytes())==meta
    assert (tmp_path/'cover_3_feed_square.jpg').read_bytes()==b'actual centre crop'
    assert (tmp_path/'cover_3_list_160.jpg').read_bytes()==b'actual list thumbnail'


@pytest.mark.parametrize('missing_feed',[False,True])
def test_release_transfer_fetches_feed_before_large_video(tmp_path,monkeypatch,missing_feed):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import index as fc
    _,meta=modern_bundle();calls=[]
    monkeypatch.setattr(fc,'delivery_release_asset',lambda name:name)
    def transfer(asset,path,**kwargs):
        calls.append(asset)
        if asset=='slug.meta.json':path.write_text(json.dumps(meta))
        else:path.write_bytes(b'actual delivery companion')
        return not (missing_feed and asset=='slug.cover_3_feed_square.jpg')
    monkeypatch.setattr(fc,'download_release_asset',transfer)
    assert fc.download_release_part('slug',0,tmp_path) is not missing_feed
    assert 'slug.cover_3_feed_square.jpg' in calls
    if missing_feed:assert 'slug.final_3.mp4' not in calls
    else:
        assert calls.index('slug.cover_3_feed_square.jpg')<calls.index('slug.final_3.mp4')
        assert (tmp_path/'cover_3_list_160.jpg').is_file()


def test_selected_part_transfers_only_required_members_and_preserves_proof(tmp_path):
    data,meta=bundle();session=Session(data)
    result=ar.extract_part('https://storage.test/immutable.zip',0,tmp_path,session=session)
    assert json.loads((tmp_path/'meta.json').read_bytes())==meta
    assert (tmp_path/'final_3.mp4').read_bytes()==b'exact selected video'*200
    assert (tmp_path/'subtitles_3.ass').read_bytes()==b'actual original ASS'
    assert (tmp_path/'subtitle_edit_proof_3.json').is_file()
    assert not (tmp_path/'unrelated.mp4').exists()
    assert result['transferred_bytes']<100_000 and result['archive_bytes']>5*1024**2
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        start=z.getinfo('unrelated.mp4').header_offset+128
        end=start+4*1024**2
    assert not any(a<end and b>start for a,b in session.ranges)


@pytest.mark.parametrize('fault',['ignores_range','wrong_offset','different_size','truncated','excess'])
def test_invalid_range_never_promotes_partial_files(tmp_path,fault):
    data,_=bundle()
    def mutate(response,n):
        if n<3:return
        if fault=='ignores_range':response.status_code=200
        elif fault=='wrong_offset':response.headers['Content-Range']='bytes 0-0/'+str(len(data))
        elif fault=='different_size':response.headers['Content-Range']=response.headers['Content-Range'].replace('/'+str(len(data)),'/'+str(len(data)+1))
        elif fault=='truncated':response.data=response.data[:-1]
        else:response.data+=b'extra'
    with pytest.raises(ValueError):
        ar.extract_part('https://storage.test/immutable.zip',0,tmp_path,session=Session(data,mutate))
    assert not list(tmp_path.iterdir())


def test_selected_file_crc_failure_never_releases_metadata_or_video(tmp_path):
    data,_=bundle();data=data.replace(b'exact selected video',b'wrong selected video',1)
    assert len(b'exact selected video')==len(b'wrong selected video')
    with pytest.raises(zipfile.BadZipFile):ar.extract_part('https://storage.test/x',0,tmp_path,session=Session(data))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('index',[-1,1,True])
def test_out_of_range_or_boolean_indices_fail_closed(tmp_path,index):
    data,_=bundle()
    with pytest.raises(ValueError,match='part index'):ar.extract_part('https://storage.test/x',index,tmp_path,session=Session(data))


def test_unsafe_companion_paths_are_never_extracted(tmp_path):
    # Rebuild metadata so CRC remains valid and path validation is exercised.
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        z.writestr('meta.json',json.dumps(dict(final='final.mp4',cover='cover.jpg',subtitle_files=['../outside.ass'])))
    with pytest.raises(ValueError,match='path'):ar.extract_part('https://storage.test/x',0,tmp_path,session=Session(output.getvalue()))
    assert not list(tmp_path.iterdir())


def test_deadline_and_archive_size_bound_reads(monkeypatch):
    data,_=bundle();session=Session(data)
    with pytest.raises(ValueError,match='size mismatch'):ar.RangeFile('https://storage.test/x',session=session,max_bytes=20)
    clock=[0.0];monkeypatch.setattr(ar.time,'monotonic',lambda:clock[0])
    reader=ar.RangeFile('https://storage.test/x',session=session,timeout_sec=3)
    clock[0]=4
    with pytest.raises(TimeoutError):reader.read(1)


def test_publisher_uses_selected_part_without_whole_bundle_fallback(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import index as fc
    monkeypatch.setattr(fc,'download_inventory_range_part',lambda aid,index,dest:True)
    monkeypatch.setattr(fc,'download_reviewed_zip',lambda *a,**k:pytest.fail('unrelated whole bundle downloaded'))
    assert fc.download_inventory_part(123,0,tmp_path)


def test_slow_range_attempt_leaves_bounded_resume_budget(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import index as fc
    times=iter([0,481]);monkeypatch.setattr(fc.time,'monotonic',lambda:next(times))
    monkeypatch.setattr(fc,'download_inventory_range_part',lambda *a:False)
    monkeypatch.setattr(fc.tempfile,'gettempdir',lambda:str(tmp_path))
    calls=[]
    def fallback(*args,**kwargs):calls.append(kwargs);raise RuntimeError('still unavailable')
    monkeypatch.setattr(fc,'download_reviewed_zip',fallback)
    assert not fc.download_inventory_part(123,0,tmp_path)
    assert calls==[dict(attempts=2,preserve_partial=True)]
