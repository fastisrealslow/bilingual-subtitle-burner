"""Validate the real production decoder on complete clips across chunk seams."""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time
import zipfile

import asr_cpu_benchmark as bench


def main():
    root = Path('asr-validation')
    root.mkdir(exist_ok=True)
    samples = []
    for artifact, member, name in [
        (9987024252, 'linyuan-verified-live.mp4', 'interview-full'),
        (9986057664, '_tmp/audio_16k.wav', 'shareholder-full'),
    ]:
        archive = root / (name + '.zip')
        subprocess.run(['curl', '-fsSL', '--retry', '3', '--max-time', '180',
            '-H', 'Authorization: Bearer ' + os.environ['GH_TOKEN'],
            f'https://api.github.com/repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip',
            '-o', str(archive)], check=True, timeout=600)
        source = root / (name + Path(member).suffix)
        with zipfile.ZipFile(archive) as z:
            source.write_bytes(z.read(member))
        archive.unlink()
        samples.append(source)
    weights = bench.download_weights('sensevoice-int8', root/'weights')
    os.environ['ASR_BACKEND'] = 'sensevoice'
    os.environ['SENSEVOICE_MODEL_DIR'] = str(weights.resolve())
    import produce_cn as production
    bench.offline_only()
    def api_forbidden(*args, **kwargs):
        raise AssertionError('Production ASR tried to call an API')
    production.llm = api_forbidden
    reports = []
    for source in samples:
        work = root/source.stem
        start = time.perf_counter()
        cues = production.transcribe(source, work, api_key='must-not-be-used')
        seconds = time.perf_counter()-start
        duration = production._audio_duration(source)
        assert cues and all(0 <= c['start'] < c['end'] <= duration+.1 for c in cues)
        assert all(a['end'] <= b['start']+.05 for a, b in zip(cues, cues[1:])), 'overlapping subtitles'
        assert production.transcribe(source, work) == cues
        def stamp(seconds):
            ms = round(seconds*1000)
            return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
        (root/(source.stem+'.srt')).write_text('\n\n'.join(
            f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}'
            for i, c in enumerate(cues, 1))+'\n', encoding='utf-8')
        reports.append(dict(source=source.name, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            duration=duration, decode_and_extract_seconds=seconds, rtf=seconds/duration,
            peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            cues=len(cues), text=''.join(c['text'] for c in cues), cache_validated=True,
            asr_network_calls=0, backend=production.ASR_BACKEND,
            pipeline_version=production.ASR_PIPELINE_VERSION))
        bench.write_json(root/'validation.json', reports)
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
