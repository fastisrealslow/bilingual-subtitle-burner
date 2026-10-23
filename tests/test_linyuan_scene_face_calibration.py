"""Scene metric calibration must not enlarge pixels or alter other covers."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from cover_face_quality import measure


def test_native_minimum_is_independent_of_apparent_detail():
    with pytest.raises(ValueError,match='96 native'):
        measure(np.random.default_rng(42).integers(0,256,(180,47,3),dtype=np.uint8))


@pytest.mark.parametrize('width',[96,107,128])
def test_small_native_face_is_measured_without_any_resampling(width):
    import cv2
    im=np.random.default_rng(42).integers(0,256,(160,width,3),dtype=np.uint8)
    result=measure(im)
    native=float(cv2.Laplacian(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var())
    assert result['value']==result['native_value']==native
    assert result['measured_size']==[width,160] and result['downsample_only']


def test_flat_or_blurred_large_pixels_cannot_create_detail():
    for im in [np.zeros((410,325,3),dtype=np.uint8),
               np.full((410,325,3),127,dtype=np.uint8)]:
        result=measure(im)
        assert result['value']==0 and result['value']<result['floor']
        assert result['measured_size']==[128,161]
