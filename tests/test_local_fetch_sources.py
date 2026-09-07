import hashlib

from linyuan import local_fetch_sources as sources


def record(content=b'mother'):
    return {
        'key': 'one',
        'url': 'https://example.invalid/video',
        'sha256': hashlib.sha256(content).hexdigest(),
    }


def fake_fetcher(tmp_path, body=b'mother'):
    script = tmp_path / 'fetch.py'
    script.write_text(
        'import pathlib,sys\n'
        f'pathlib.Path(sys.argv[sys.argv.index("--out")+1]).write_bytes({body!r})\n')
    return script


def test_downloads_once_and_reuses_exact_hash(tmp_path):
    output = tmp_path / 'mothers'
    fetcher = fake_fetcher(tmp_path)
    first = sources.fetch([record()], output, fetcher=fetcher)
    second = sources.fetch([record()], output, fetcher=tmp_path / 'missing.py')
    assert first['sources'][0]['status'] == 'downloaded'
    assert second['sources'][0]['status'] == 'reused'
    assert first['cloud_calls'] == 0


def test_hash_mismatch_never_replaces_source(tmp_path):
    output = tmp_path / 'mothers'
    fetcher = fake_fetcher(tmp_path, b'wrong')
    try:
        sources.fetch([record()], output, fetcher=fetcher)
    except ValueError:
        pass
    else:
        raise AssertionError('hash mismatch must fail')
    assert not (output / 'one.mp4').exists()
    assert not (output / 'one.part.mp4').exists()
