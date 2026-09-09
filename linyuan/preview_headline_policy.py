"""Render production cover functions on real historical titles, without publishing."""
import json
from pathlib import Path
import produce_cn as P
from headline_policy import attach_copy

out=Path('headline-preview');out.mkdir(exist_ok=True)
reference=P._download_speaker_reference('林园',out)
portrait=P.extract_audio_card_portrait(reference,out/'portrait.png')
if not portrait:raise RuntimeError('missing verified portrait')
samples=[
 ('dividend','林园：市场的股息率有8%，我买过，现在连吃的喝的都有了，这位置低的不得了'),
 ('medicine','林园：现在消费和医药的回报是我从事资本市场以来最值得的时候'),
 ('timing','林园：我们说的买入，你要投重手的话一定择时非常重要'),
 ('scarcity','林园：所有的产品都会变得一文不值'),
 ('full','林园：57分钟完整访谈，谈AI、机器人、消费和医药的长期机会'),
 ('wine','林园：电视机整天在降价，酒在涨价，两个相反的方向'),
]
rows=[]
for name,title in samples:
    copy=attach_copy({'title':title},title)
    for style in ('light','dark'):
        path=out/f'{name}-{style}.jpg'
        P.make_audio_card(path,'林园',copy['cover_title'],width=1280,height=720,
                          portrait_path=portrait,require_portrait=True,cover_style=style)
    rows.append({'sample':name,**copy})
# Real CPU identity pass using a reference-derived guest and an unrelated blank image.
from PIL import Image
blank=out/'blank.jpg';Image.new('RGB',(640,360),'gray').save(blank)
matched,box,proof=P.select_verified_cover_face([blank,portrait],reference)
assert matched==portrait
(out/'identity-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
(out/'copy.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print(json.dumps(rows,ensure_ascii=False,indent=2))
