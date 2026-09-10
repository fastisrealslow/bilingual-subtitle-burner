"""Measure encoded black borders before titles/subtitles are positioned."""
from pathlib import Path
import subprocess


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


def refine_native_crop(src, video_filter, width, height, work, minimum=480):
    """Sample the exact cleaning filter, then append a measured border crop."""
    import cv2
    cap = cv2.VideoCapture(str(src))
    fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if not fps or count <= 0:
        raise ValueError('黑边预检无法取得源时长')
    frames = []
    sample = Path(work) / 'border-preview.png'
    for i in range(12):
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss',
            str(count / fps * (i + .5) / 12), '-i', str(src), '-vf', video_filter,
            '-frames:v', '1', str(sample)], check=True, timeout=45)
        frame = cv2.imread(str(sample))
        if frame is None or frame.shape[:2] != (height, width):
            raise ValueError('黑边预检未取得清理后的实际画面')
        frames.append(frame)
    w, h, x, y = black_border_crop(frames)
    if min(w, h) < minimum:
        raise ValueError('去黑边后源画质不足，禁止放大冒充合格')
    proof = dict(version=1, samples=len(frames), input=[width, height],
                 crop=[w, h, x, y])
    if (w, h) != (width, height):
        video_filter += f',crop={w}:{h}:{x}:{y}'
    return video_filter, w, h, proof
