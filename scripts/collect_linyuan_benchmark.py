#!/usr/bin/env python3
"""Refresh the fixed public 16-video benchmark; retain partial-stream limits.
Reference media is analysis only and never used as production footage.
"""
from pathlib import Path
import json,urllib.request,datetime,cv2
from concurrent.futures import ThreadPoolExecutor,as_completed
REPO=Path(__file__).resolve().parents[1]
ROOT=REPO/'output/benchmark-20260921'
H={'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/'}
def get(url):
 return urllib.request.urlopen(urllib.request.Request(url,headers=H),timeout=35)
def one(row):
 b=row['bvid'];o=ROOT/'reference'/b;o.mkdir(parents=True,exist_ok=True)
 try:
  with get('https://api.bilibili.com/x/web-interface/view?bvid='+b) as r:d=json.load(r)
  if d.get('code')!=0:raise ValueError('view code '+str(d.get('code')))
  v=d['data'];assert v['owner']['mid']==1700344493
  v['fetched_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();v['sample_group']=row['group'];(o/'view.json').write_text(json.dumps(v,ensure_ascii=False,indent=2))
  with get(v['pic'].replace('http:','https:')) as r:(o/'cover.jpg').write_bytes(r.read())
  with get(f'https://api.bilibili.com/x/player/playurl?bvid={b}&cid={v["cid"]}&qn=32&fnval=16&fourk=0') as r:p=json.load(r)
  streams=[s for s in p.get('data',{}).get('dash',{}).get('video',[]) if s['codecs'].startswith('avc')]
  durl=p.get('data',{}).get('durl') or []
  if not streams and not durl:raise ValueError('no anonymous preview')
  stream=max(streams,key=lambda s:s['width']*s['height']) if streams else dict(baseUrl=durl[0]['url'],id=p['data']['quality']);video=o/'preview-video-only.mp4'
  if not video.exists():
   partial=video.with_suffix('.partial')
   with get(stream.get('baseUrl') or stream['base_url']) as r,partial.open('wb') as f:
    while True:
     block=r.read(1024*1024)
     if not block:break
     f.write(block)
   partial.replace(video)
  cap=cv2.VideoCapture(str(video));fps=cap.get(cv2.CAP_PROP_FPS);duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/fps if fps else 0
  frames=[]
  for t in [2,min(15,v['duration']*.25),min(45,v['duration']*.7)]:
   cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,frame=cap.read()
   if ok:cv2.imwrite(str(o/f'frame-{len(frames)}.jpg'),frame);frames.append(t)
  proof={'video_only':True,'audio_reviewed':False,'actual_dimensions':[int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))],'decoded_duration':duration,'frame_times':frames,'source_dimensions':v['dimension'],'stream_quality':stream['id']};cap.release()
  if not frames:raise ValueError('no decodable preview frames')
  (o/'inspection.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
  (o/'fetch-error.txt').unlink(missing_ok=True)
  return b,v['stat']['view'],v['dimension'],len(frames)
 except Exception as e:
  (o/'fetch-error.txt').write_text(str(e));return b,'ERROR',str(e)
def main():
 records=json.loads((REPO/'linyuan/simulations/benchmark-20260921/references.json').read_text())
 ROOT.mkdir(parents=True,exist_ok=True)
 selection=[dict(bvid=r['bvid'],group=r['group']) for r in records['rows']]
 (ROOT/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2))
 with ThreadPoolExecutor(max_workers=3) as ex:
  for f in as_completed([ex.submit(one,r) for r in selection]):print(f.result(),flush=True)
 for row in records['rows']:
  folder=ROOT/'reference'/row['bvid']
  if not (folder/'view.json').exists():continue
  view=json.loads((folder/'view.json').read_text())
  row.update(views=view['stat']['view'],likes=view['stat']['like'],replies=view['stat']['reply'],fetched_at=view['fetched_at'])
  if (folder/'fetch-error.txt').exists():row['refresh_error']=(folder/'fetch-error.txt').read_text()
  if (folder/'inspection.json').exists():row['inspection']=json.loads((folder/'inspection.json').read_text())
 (ROOT/'benchmark.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
