"""Wait for the deployed repair handler, then use the tracked, idempotent invoker."""
import json
import subprocess
import time
from pathlib import Path
from invoke_reviewed_updates import main

# Asset manifests are read at invocation time; the already-triggered code
# deployment need not run again. Match code bytes, not a moving main log commit.
expected=subprocess.check_output(['git','hash-object','linyuan/fc/index.py'],text=True).strip()
deadline=time.monotonic()+900
checked={}
while time.monotonic()<deadline:
    runs=json.loads(subprocess.check_output(['gh','run','list','--workflow','fc-production-deploy.yml','--limit','12','--json','headSha,status,conclusion']))
    ready=False
    for run in runs:
        sha=run['headSha']
        if run['conclusion']!='success':continue
        if sha not in checked:
            result=subprocess.run(['gh','api',f'repos/fastisrealslow/bilingual-subtitle-burner/contents/linyuan/fc/index.py?ref={sha}','--jq','.sha'],capture_output=True,text=True)
            checked[sha]=result.returncode==0 and result.stdout.strip()==expected
        if checked[sha]:ready=True;break
    if ready:break
    time.sleep(15)
else:raise SystemExit('Stage revision deployment not verified in time')
main(trigger='apply-stage-revision-0912',state_key='stage_revision_0912',bvids=('BV1CLYd6LEWE',),
     version_file='linyuan/fc/stage_revision.py',prefix='stage-revision-0912',report='stage-revision-verification.json')
