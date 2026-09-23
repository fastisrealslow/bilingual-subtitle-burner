"""Map rejected window text back to a completely locked source crop.

This only proposes an additional exclusion for one new render. It never drops
an OCR finding or approves media. Camera movement and interview cuts fail closed.
"""
import math


def source_exclusions(tracking, logos, width, height):
    framing = tracking.get('framing', {})
    frames = tracking.get('frames', 0)
    crop = tracking.get('first_crop')
    samples = framing.get('crop_samples') or []
    if (tracking.get('engine') != 'yunet_sface_per_frame_cpu'
            or tracking.get('passed') is not True or tracking.get('mode')
            or frames < 3 or tracking.get('decoded_frames') != frames
            or tracking.get('encoded_frames') != frames
            or framing.get('held_frames') != frames - 1
            or framing.get('pan_frames') != 0 or framing.get('cut_frames') != []
            or framing.get('geometry_reframes') != []
            or not crop or crop != tracking.get('last_crop') or not samples
            or any(s.get('crop') != crop for s in samples)):
        return []
    if len(crop) != 4 or min(width, height) <= 0:
        return []
    x, y, w, h = crop
    if not all(math.isfinite(v) for v in crop) or not (0 <= x < x+w <= width and 0 <= y < y+h <= height):
        return []
    result = []
    for box in logos:
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            return []
        a, b, c, d = box
        if not (0 <= a < c <= 1 and 0 <= b < d <= 1):
            return []
        # Two source pixels keep resampling from leaving a sliver of text.
        result.append((max(0, (x+a*w-2)/width), max(0, (y+b*h-2)/height),
                       min(1, (x+c*w+2)/width), min(1, (y+d*h+2)/height)))
    return result
