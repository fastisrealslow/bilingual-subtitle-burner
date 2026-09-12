"""Reject static portraits after compensating camera pan/zoom.

This is a conservative temporal quality gate, not an identity or lip-sync model.
It inspects only the live window, never moving captions or template decorations.
"""
VERSION = 2026091201


def residual_motion(first, second):
    import cv2
    import numpy as np
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        correlation, warp = cv2.findTransformECC(first, second, warp, cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 80, 1e-5), None, 5)
    except cv2.error:
        return {'moving': False, 'reason': 'alignment_failed'}
    # Cuts, large occlusions and failed registration are not speaking evidence.
    if correlation < .9:
        return {'moving': False, 'reason': 'cut_or_unregistered', 'correlation': float(correlation)}
    aligned = cv2.warpAffine(second, warp, (240, 180),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    delta = cv2.absdiff(cv2.GaussianBlur(first, (5, 5), 0),
                       cv2.GaussianBlur(aligned, (5, 5), 0))[35:145, 55:185]
    changed = float((delta > 12).mean())
    return dict(moving=changed >= .02, changed_fraction=changed,
                mean_residual=float(delta.mean()), correlation=float(correlation))


def window_for_meta(meta):
    layout = meta.get('layout_proof') or {}
    rect = dict(layout.get('live_region') or dict(x=44,y=360,width=632,height=470))
    subtitles = layout.get('subtitle_region') or {}
    # Landscape captions overlap the source window. Their changing glyphs
    # must never serve as evidence that a printed portrait is moving.
    if subtitles and rect['y'] < subtitles['y'] < rect['y'] + rect['height']:
        rect['height'] = subtitles['y'] - rect['y']
    return rect


def verify_window(path, rect):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps > 0 else 0
        if duration < 5:
            raise ValueError('真人动态验证无法读取有效视频')
        samples = []
        for t in np.linspace(1, duration - 2, 12):
            frames = []
            for tt in (t, t + .6):
                cap.set(cv2.CAP_PROP_POS_MSEC, float(tt) * 1000)
                ok, frame = cap.read()
                if not ok:
                    raise ValueError('真人动态验证采样解码失败')
                x,y,w,h = (int(rect[k]) for k in ('x','y','width','height'))
                if min(x,y) < 0 or min(w,h) <= 0 or x+w > frame.shape[1] or y+h > frame.shape[0]:
                    raise ValueError('真人动态验证窗口越界')
                gray = cv2.cvtColor(frame[y:y+h,x:x+w], cv2.COLOR_BGR2GRAY)
                frames.append(cv2.resize(gray, (240,180)))
            samples.append(dict(time=float(t), **residual_motion(*frames)))
        # Require distributed evidence; a single transition or brief animated
        # introduction cannot qualify a two-minute portrait as live footage.
        thirds = [sum(s['moving'] for s in samples[i:i+4]) for i in (0,4,8)]
        passed = sum(thirds) >= 6 and min(thirds) >= 1
        return dict(version=VERSION, passed=passed, samples=samples,
                    moving_by_third=thirds, window=dict(rect),
                    policy='affine_compensated_local_motion',
                    limitation='Temporal motion evidence; identity is checked separately')
    finally:
        cap.release()
