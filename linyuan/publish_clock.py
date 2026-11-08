"""Wake the trusted CI coordinator; no video work on the paid timer invocation."""
WORKFLOW='linyuan-publish-runner.yml'
REPO='fastisrealslow/bilingual-subtitle-burner'


def request(gh):
    rows=gh('GET',f'/actions/workflows/{WORKFLOW}/runs?per_page=5',timeout=15).get('workflow_runs')
    if not isinstance(rows,list):raise ValueError('Publisher clock run list unavailable')
    for row in rows:
        if (row.get('path')!='.github/workflows/'+WORKFLOW or row.get('head_branch')!='main'
                or (row.get('head_repository') or {}).get('full_name')!=REPO):
            raise ValueError('Publisher clock run identity unavailable')
        if row.get('status') in {'queued','pending','requested','waiting','in_progress'}:
            return dict(scheduled=False,reason='publisher_already_active',published=0)
    # Never repeat an ambiguous POST, and never fall through to a second uploader.
    gh('POST',f'/actions/workflows/{WORKFLOW}/dispatches',{'ref':'main','inputs':{'preview':'false'}},timeout=20)
    return dict(scheduled=True,workflow=WORKFLOW,ref='main',published=0)
