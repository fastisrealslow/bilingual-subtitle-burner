"""Source context transcribed from inspected original frames, bound to bytes."""
from copy import deepcopy

CONTEXTS = {
    '64fa677c6235f9121dc444b0d432990e1b49e3fb69cc0d590f851a93c88dea6b': {
        'occasion': '原片画面标注：2024年1月13日，林园深圳演讲',
        'basis': 'original_on_screen_label',
        'evidence_run_id': '35921449354',
        'evidence_frame': 'original-181.50.jpg',
        'evidence_frame_sha256': '70f50c4e70c13dddaaa249942fa9055dde09624f97c6eb1bc0c6f11bfbe7ce8d',
        'scope': '日期来自原片画面标注；未独立核验拍摄日期，不表示当前市场判断。',
    },
}


def context_for(source_sha256):
    return deepcopy(CONTEXTS.get(source_sha256, {}))
