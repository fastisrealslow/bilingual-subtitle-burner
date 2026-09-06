import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('fetch_yicai',Path(__file__).resolve().parents[1]/'linyuan/ci_fetch_yicai.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_official_html_media_query_preserved():
    assert module.extract_video_url('<video src="https://media.example/a.mp4?auth=a&amp;t=3">')=='https://media.example/a.mp4?auth=a&t=3'
    assert module.extract_video_url('{"file":"https:\\/\\/media.example/a.mp4"}')=='https://media.example/a.mp4'
    with pytest.raises(ValueError):
        module.extract_video_url('<html>No video</html>')
