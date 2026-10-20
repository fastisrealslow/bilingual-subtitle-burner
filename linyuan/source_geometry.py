"""Measure encoded black borders before titles/subtitles are positioned."""
from pathlib import Path
import subprocess


def landscape_crop_plan(preview, width, height, detector_factory, minimum=480):
    """Plan a fixed 16:9 crop from the whole-interval 1fps clean preview.

    Retain each face plus a face-width on both sides for shoulders. Missing
    faces, group shots and incompatible camera cuts keep the original view.
    This framing evidence never replaces the final picture/identity gates.
    """
    import cv2
    import math
    target = int(height * 16 / 9) // 2 * 2
    proof = dict(version=1, applied=False, input=[width, height],
                 target_aspect='16:9', final_quality_approved=False)
    if width <= target + 2 or min(target, height) < minimum:
        return None, {**proof, 'reason': 'already_fits_or_not_wide'}
    if (width - target) / width > .30:
        return None, {**proof, 'reason': 'crop_would_remove_over_30_percent'}
    cap = cv2.VideoCapture(str(preview))
    expected = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    count = 0
    lower, upper = 0, width - target
    try:
        detect = detector_factory()
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            count += 1
            if frame.shape[:2] != (height, width):
                raise ValueError('横屏取景预检尺寸不一致')
            faces = detect(frame)
            if faces is None or len(faces) != 1:
                return None, {**proof, 'samples': count, 'reason': 'missing_or_multiple_faces'}
            x, y, fw, fh = map(float, faces[0][:4])
            if (not all(math.isfinite(v) for v in (x, y, fw, fh))
                    or fw <= 0 or fh <= 0 or x < 0 or y < 0
                    or x + fw > width or y + fh > height):
                return None, {**proof, 'samples': count, 'reason': 'incomplete_face'}
            left, right = max(0, x - fw), min(width, x + 2 * fw)
            lower, upper = max(lower, right - target), min(upper, left)
            if math.ceil(lower / 2) * 2 > math.floor(upper / 2) * 2:
                return None, {**proof, 'samples': count, 'reason': 'subject_crosses_crop'}
    finally:
        cap.release()
    if count < 6 or expected <= 0 or count != expected:
        raise ValueError('横屏取景预检未完整解码选段')
    lower, upper = math.ceil(lower / 2) * 2, math.floor(upper / 2) * 2
    x = int(max(lower, min(upper, (width - target) // 4 * 2)))
    crop = (target, height, x, 0)
    return crop, {**proof, 'applied': True, 'samples': count, 'crop': list(crop),
                  'reason': 'whole_interval_face_and_shoulder_margin'}


def black_border_crop(frames, max_fraction=.15):
    """Only trim uniform black strips present in every decoded sample.

    Use the smallest observed strip, so a camera change or a naturally dark
    frame cannot enlarge the crop into real content. Never resize or pad.
    """
    import numpy as np
    if len(frames) < 6:
        raise ValueError('黑边预检抽帧不足')
    height, width = frames[0].shape[:2]
    margins = []
    for frame in frames:
        if frame.shape[:2] != (height, width):
            raise ValueError('黑边预检尺寸不一致')
        gray = frame.mean(axis=2) if frame.ndim == 3 else frame
        rows = (gray.mean(axis=1) < 5) & (gray.std(axis=1) < 2)
        cols = (gray.mean(axis=0) < 5) & (gray.std(axis=0) < 2)
        def edge_length(values):
            nonblack = np.flatnonzero(~values)
            return int(nonblack[0]) if len(nonblack) else len(values)
        margins.append([edge_length(cols), edge_length(cols[::-1]),
                        edge_length(rows), edge_length(rows[::-1])])
    left, right, top, bottom = np.min(margins, axis=0).tolist()
    # Large black regions need a separate picture review, not an aggressive crop.
    if left + right > width * max_fraction or top + bottom > height * max_fraction:
        raise ValueError('持续黑区超过安全裁边范围')
    left, right, top, bottom = [((v + 1) // 2 * 2 if v >= 4 else 0)
                                for v in (left, right, top, bottom)]
    return (width - left - right, height - top - bottom, left, top)


def has_black_fill(frame):
    """Same encoded-edge criterion in preflight and final verification."""
    import cv2
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    height,width=gray.shape
    edges=[gray[:max(2,height//40),:],gray[-max(2,height//40):,:],
           gray[:,:max(2,width//40)],gray[:,-max(2,width//40):]]
    return any(float(e.mean())<5 and float(e.std())<2 for e in edges)


def refine_native_crop(src, video_filter, width, height, work, minimum=480,
                       start=0.0,duration=None):
    """Sample the exact cleaning filter, then append a measured border crop."""
    import cv2
    cap = cv2.VideoCapture(str(src))
    fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if not fps or count <= 0:
        raise ValueError('黑边预检无法取得源时长')
    source_duration=count/fps
    duration=source_duration-start if duration is None else duration
    if start<0 or duration<=0 or start+duration>source_duration+.1:
        raise ValueError('黑边预检区间越出母片')
    frames = []
    sample = Path(work) / 'border-preview.png'
    for i in range(12):
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss',
            str(start+duration*(i + .5)/12), '-i', str(src), '-vf', video_filter,
            '-frames:v', '1', str(sample)], check=True, timeout=45)
        frame = cv2.imread(str(sample))
        if frame is None or frame.shape[:2] != (height, width):
            raise ValueError('黑边预检未取得清理后的实际画面')
        frames.append(frame)
    w, h, x, y = black_border_crop(frames)
    if min(w, h) < minimum:
        raise ValueError('去黑边后源画质不足，禁止放大冒充合格')
    remaining=sum(has_black_fill(frame[y:y+h,x:x+w]) for frame in frames)
    proof = dict(version=2, samples=len(frames), input=[width, height],
                 crop=[w, h, x, y],source_start=start,duration=duration,
                 remaining_black_edge_hits=remaining)
    if remaining>=max(2,len(frames)//2):
        # An intermittent mark on a bar can hide its uniformity. Do not crop
        # real picture by using the largest detected margin; route to a moving
        # window or a different source before titles and full encoding.
        import json
        (Path(work)/'border-rejection.json').write_text(json.dumps(proof,indent=2))
        raise ValueError('清理预检仍有持续黑色填充边，须重新取景')
    if (w, h) != (width, height):
        video_filter += f',crop={w}:{h}:{x}:{y}'
    return video_filter, w, h, proof
