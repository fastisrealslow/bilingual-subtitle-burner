"""Load bounded preview inputs; never accept editorial answers or publication flags."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse


def parse(data):
    if not isinstance(data, dict) or set(data) - {'source', 'speaker', 'occasion', 'max_outputs', 'revision', 'local_text_model', 'draft_profile'}:
        raise ValueError('Preview accepts source metadata only, not titles, ranges or publication overrides')
    source = data.get('source')
    if not isinstance(source, str) or urlparse(source).scheme != 'https' or not urlparse(source).netloc:
        raise ValueError('Preview requires an HTTPS source URL')
    maximum = data.get('max_outputs', 1)
    if type(maximum) is not int or not 1 <= maximum <= 3:
        raise ValueError('Preview max_outputs must be 1–3')
    model=data.get('local_text_model','qwen3:8b')
    profile=data.get('draft_profile','production')
    if model not in ('qwen3:8b','qwen3.5:9b') or profile not in ('production','answer_subject'):
        raise ValueError('Unsupported preview model or automatic drafting profile')
    result = dict(RUN_SOURCE=source, RUN_SPEAKER=data.get('speaker', '林园'),
                  RUN_OCCASION=data.get('occasion', '访谈'), RUN_MAX_OUTPUTS=str(maximum),
                  RUN_TEXT_MODEL=model,LINYUAN_TITLE_DRAFT_PROFILE=profile)
    for value in result.values():
        if not isinstance(value, str) or not value.strip() or len(value) > 2000 or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid preview metadata')
    return result


if __name__ == '__main__':
    values = parse(json.loads(Path(sys.argv[1]).read_text()))
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        for name, value in values.items():
            stream.write(name + '=' + value + '\n')
    print('Automatic preview configured; no publication or FC invocation')
