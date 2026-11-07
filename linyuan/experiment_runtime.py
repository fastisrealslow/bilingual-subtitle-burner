"""Read-only runtime canary for the exact tokenizer used by story proof checks."""
import hashlib
from pathlib import Path

JIEBA_VERSION = '0.42.1'


def health():
    import jieba
    import documented_company_story as documentary
    from source_timeline import registry_sha256
    from publishing_experiment import title_focus
    root=Path(jieba.__file__).parent
    focus=title_focus(dict(kind='company_case',subject_quote='我买了茅台，',
                          detail_quote='因为消费升级。'))
    return dict(version=JIEBA_VERSION,installed_version=jieba.__version__,
        dictionary_sha256=hashlib.sha256((root/'dict.txt').read_bytes()).hexdigest(),
        module_sha256=hashlib.sha256((root/'__init__.py').read_bytes()).hexdigest(),
        documented_company_story_sha256=hashlib.sha256(Path(documentary.__file__).read_bytes()).hexdigest(),
        source_timeline_evidence_sha256=registry_sha256(),
        passed=jieba.__version__==JIEBA_VERSION and '茅台' in focus['subjects']
               and '消费升级' in focus['details'] and documentary.named_company('招商银行')
               and not documentary.named_company('我当时写过文章。'))
