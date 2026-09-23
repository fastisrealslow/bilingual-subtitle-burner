"""Two-line editorial cover from an already verified source frame.

This renderer never certifies identity or rewrites copy. The caller supplies the
verified face and reviewed headline; all geometry is recorded for inspection.
"""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path


def font_path():
    candidates = [os.environ.get('COVER_FONT_PATH'),
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf',
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
    for name in candidates:
        if name and Path(name).is_file():
            return name
    raise ValueError('缺少中文字体；设置 COVER_FONT_PATH 指向中文字体文件')


@lru_cache(maxsize=8)
def font_face_index(path):
    if not str(path).lower().endswith('.ttc'):
        return 0
    from fontTools.ttLib import TTCollection
    collection = TTCollection(path, lazy=True)
    try:
        choices = []
        for index, face in enumerate(collection.fonts):
            names = [n.toUnicode() for n in face['name'].names if n.nameID in (1,2,4,6)]
            name = ' '.join(names)
            simplified = 'PingFang SC' in name or 'Noto Sans CJK SC' in name or 'Hiragino Sans GB' in name
            weight = 2 if 'Bold' in name else 1 if 'Semibold' in name or 'Medium' in name else 0
            choices.append((simplified, weight, -index))
        return -max(choices)[2]
    finally:
        collection.close()


def portrait_crop(size, face, ratio=320/584):
    """Include face + headroom without distorting or guessing a cutout mask."""
    w, h = size
    x, y, fw, fh = map(float, face)
    if min(fw, fh) <= 0 or x < 0 or y < 0 or x+fw > w or y+fh > h:
        raise ValueError('封面人脸边界无效')
    # The narrow panel can show shoulders and context, with explicit headroom.
    cw = min(w, max(fw*1.6, fh*1.65*ratio))
    ch = cw/ratio
    if ch > h:
        ch = h
        cw = ch*ratio
    left = max(0, min(x+fw/2-cw/2, w-cw))
    top = max(0, min(y-fh*.30, h-ch))
    rect = (round(left), round(top), round(left+cw), round(top+ch))
    if (rect[0] > x or rect[1] > max(0, y-fh*.15)
            or rect[2] < x+fw or rect[3] < y+fh):
        # A close-up cannot fit a tall crop. Keep its natural aspect inside
        # the designed panel instead of cropping the face or stretching it.
        rect = (max(0, int(x-fw*.3)), max(0, int(y-fh*.3)),
                min(w, int(x+fw*1.3+.5)), min(h, int(y+fh*1.3+.5)))
    return rect


def render(image, path, face, headline, speaker, font, font_index=None):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from presentation import cover_headline, cover_proof
    from fontTools.ttLib import TTFont
    if font_index is None:
        font_index = font_face_index(font)
    with TTFont(font, fontNumber=font_index, lazy=True) as face_font:
        cmap = face_font.getBestCmap() or {}
        if any(ord(ch) not in cmap for ch in headline+speaker+'访谈摘录原声观点' if not ch.isspace()):
            raise ValueError('封面字体缺少文案字形，不能输出方框字')
    path = Path(path)
    crop = portrait_crop(image.size, face)
    canvas = Image.new('RGB', (1280, 720), '#101c2b')
    draw = ImageDraw.Draw(canvas)
    # One accent color and one visual hierarchy, consistent across episodes.
    draw.rectangle((0, 0, 12, 720), fill='#efbd58')
    panel = (936, 68, 1256, 652)
    portrait = ImageOps.contain(image.convert('RGB').crop(crop), (320, 584), Image.Resampling.LANCZOS)
    portrait_xy = (panel[0]+(320-portrait.width)//2, panel[1]+(584-portrait.height)//2)
    canvas.paste(portrait, portrait_xy)
    draw.line((912, 68, 912, 652), fill='#334557', width=2)
    label = ImageFont.truetype(font, 44, index=font_index)
    small = ImageFont.truetype(font, 26, index=font_index)
    title_font = ImageFont.truetype(font, 96, index=font_index)
    draw.text((48, 66), speaker, font=label, fill='#efbd58')
    draw.text((48, 134), '访谈摘录', font=small, fill='#adbac9')
    lines = cover_headline(headline, speaker, max_lines=3)
    boxes = []
    for i, line in enumerate(lines):
        xy = (48, 204+i*122) if len(lines)==3 else (48, 272+i*132)
        box = draw.textbbox(xy, line, font=title_font)
        if box[2] > 912:
            raise ValueError('封面文案超出文字区；不能缩成小字或覆盖人脸')
        draw.text(xy, line, font=title_font, fill='#f5f5f1' if i == 0 else '#efbd58')
        boxes.append(box)
    draw.line((48, 606, 120, 606), fill='#efbd58', width=4)
    draw.text((48, 624), '林园访谈 · 原声观点' if speaker == '林园' else speaker+' · 原声观点',
              font=small, fill='#adbac9')
    canvas.save(path, quality=95)
    proof = cover_proof(canvas, path, lines, 96, boxes, style='editorial')
    x, y, fw, fh = face
    sx, sy = portrait.width/(crop[2]-crop[0]), portrait.height/(crop[3]-crop[1])
    mapped_face = [portrait_xy[0]+(x-crop[0])*sx, portrait_xy[1]+(y-crop[1])*sy,
                   portrait_xy[0]+(x+fw-crop[0])*sx, portrait_xy[1]+(y+fh-crop[1])*sy]
    proof.update(design_version=1, portrait_panel=list(panel), source_crop=list(crop),
                 face_box=mapped_face, face_fully_visible=True, text_face_overlap=False,
                 sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    Path(str(path)+'.proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    return proof
