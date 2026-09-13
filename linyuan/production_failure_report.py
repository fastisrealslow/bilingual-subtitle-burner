"""An unhandled workflow failure is recoverable, never a content verdict."""
import json
import os
from pathlib import Path


def report_missing(path, steps, slug):
    path=Path(path)
    if path.exists():return False
    failed=[name for name, row in steps.items() if row.get('outcome')=='failure']
    if not failed or any(name in {'src','source_gate'} for name in failed):return False
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(slug=slug,accepted=0,accepted_finals=[],
        selection_completed=False,retryable=True,rejected=[dict(stage='workflow-runtime',
            error_type='WorkflowRuntimeFailure',retryable=True,
            reason='工作流步骤失败，未形成素材不合格结论：'+','.join(failed))]),ensure_ascii=False))
    return True


if __name__=='__main__':
    slug=os.environ['RUN_SLUG']
    report_missing(Path('deliver')/slug/'batch_report.json',
                   json.loads(os.environ['PRODUCTION_STEPS']),slug)
