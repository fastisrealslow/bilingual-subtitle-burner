"""Recreate committed time-range evidence from exact original Actions reports."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_timeline as T

REPO='fastisrealslow/bilingual-subtitle-burner'


def report(evidence,directory):
    run=evidence['run_id'];identity=evidence['artifact_id']
    info=json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/actions/runs/{run}/artifacts']))
    a=next(a for a in info['artifacts'] if a['id']==identity)
    assert not a['expired'] and a['name'].startswith('raw-asr-') and a['size_in_bytes']<2_000_000
    folder=directory/str(identity)
    subprocess.run(['gh','run','download',str(run),'--repo',REPO,'--name',a['name'],'--dir',str(folder)],
        check=True,capture_output=True,timeout=90)
    paths=list(folder.rglob('asr_raw_chunks.json'));assert len(paths)==1
    rows=json.loads(paths[0].read_text());assert isinstance(rows,list) and len(rows)==1
    assert T.digest(rows[0])==evidence['report_sha256']
    return rows[0]


def main():
    verified=[]
    with tempfile.TemporaryDirectory() as directory:
        for proof in json.loads(T.REGISTRY.read_text())['proofs']:
            a,b=[report(e,Path(directory)) for e in proof['evidence']]
            old,new=proof['evidence']
            actual=T.verify(a,b,source_key=proof['source_key'],old_run=old['run_id'],old_artifact=old['artifact_id'],
                new_run=new['run_id'],new_artifact=new['artifact_id'])
            assert actual==proof
            verified.append(dict(proof_sha256=T.digest(actual),source_key=proof['source_key'],duration_sec=proof['duration_sec']))
    result=dict(passed=bool(verified),proofs=verified,media_approved=False,production_dispatch=False)
    Path('source-timeline-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
