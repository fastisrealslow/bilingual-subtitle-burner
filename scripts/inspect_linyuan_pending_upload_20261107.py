"""Read only the configured owner's exact pending archive title."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from bili_archive_status import archive_status
title='林园：现在港股不跟美股，跟着 A 股走，两地上市除非特好不买'
result=archive_status([],275211725,os.environ['BILIBILI_COOKIES'],expected_titles=[title],include_statistics=True)
result.update(slug='ly-1104-992bb2',state_mutated=False,production_dispatch=False)
Path('pending-upload-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
