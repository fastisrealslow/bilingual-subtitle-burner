"""Measure scene-face detail at a fixed display scale without enlarging it."""
REFERENCE_WIDTH = 128
MIN_NATIVE_FACE = 96
SHARPNESS_FLOOR = 60
METRIC = 'face_laplacian_downsample_only_128_v1'


def measure(crop):
    import cv2
    if crop is None or crop.ndim != 3 or crop.shape[2] != 3:
        raise ValueError('Scene sharpness requires a decoded BGR face')
    height,width=crop.shape[:2]
    if min(width,height)<MIN_NATIVE_FACE:
        raise ValueError('Scene sharpness cannot enlarge a face below 96 native pixels')
    def variance(image):
        return float(cv2.Laplacian(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var())
    native=variance(crop)
    if width>REFERENCE_WIDTH:
        sampled=cv2.resize(crop,(REFERENCE_WIDTH,round(height*REFERENCE_WIDTH/width)),
                           interpolation=cv2.INTER_AREA)
    else:
        sampled=crop
    return dict(value=variance(sampled),native_value=native,metric=METRIC,
                native_size=[width,height],measured_size=[sampled.shape[1],sampled.shape[0]],
                reference_width=REFERENCE_WIDTH,downsample_only=True,floor=SHARPNESS_FLOOR)
