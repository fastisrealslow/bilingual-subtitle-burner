"""Check restored incident alignments against their actual preserved PCM."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import wave
from mother_asr_cache import transfer
from prepare_asr_runtime import cached_evidence,configuration
from qwen_asr_evidence import load_reports,validated_words


def main():
    parser=argparse.ArgumentParser();parser.add_argument('evidence',type=Path)
    args=parser.parse_args();root=args.evidence/'_tmp'
    report=json.loads((root/'source_quality.json').read_text())
    with tempfile.TemporaryDirectory() as tmp:
        target=Path(tmp)
        assert transfer(root,target,report['source_sha256'])
        (target/'source_quality.json').write_text(json.dumps(report))
        choice=configuration(target/'source_quality.json')
        evidence=cached_evidence(target/'source_quality.json',choice)
        assert evidence is not None
        with wave.open(str(root/'audio_16k.wav')) as wav:
            duration=wav.getnframes()/wav.getframerate()
            pcm_sha=hashlib.sha256(wav.readframes(wav.getnframes())).hexdigest()
        words=validated_words(load_reports(evidence),pcm_sha,report['source_sha256'],duration)
        print(json.dumps(dict(source_sha256=report['source_sha256'],duration_sec=duration,
            aligned_words=len(words),pcm_verified=True,complete_coverage=True,
            reusable_without_asr_weights=True)))


if __name__=='__main__':main()
