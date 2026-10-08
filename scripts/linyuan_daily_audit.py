#!/usr/bin/env python3
"""Local 17:00 Beijing Codex audit; launchd schedules, Codex investigates/fixes."""
import argparse
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')


def latest_publications(state, count=4):
    """Select distinct published videos across dates, never four source batches."""
    if not isinstance(state.get('published'), dict):
        raise ValueError('Missing publication ledger')
    videos = {}
    for slug, batch in state['published'].items():
        for part in batch.get('parts') or [batch]:
            if part.get('status') != 'published' or not part.get('bvid') or not part.get('ts'):
                continue
            bvid = part['bvid']
            row = dict(bvid=bvid, slug=slug, submitted_at=part['ts'],
                       title=part.get('title'), fingerprints=part.get('fingerprints') or {})
            # Repeated bookkeeping entries are not a newer publication.
            if bvid not in videos or row['submitted_at'] < videos[bvid]['submitted_at']:
                videos[bvid] = row
    return sorted(videos.values(), key=lambda r: (r['submitted_at'], r['bvid']), reverse=True)[:count]


def scheduled_day(now):
    local=now.astimezone(BEIJING)
    return (local.date() if local.hour>=17 else local.date()-timedelta(days=1)).isoformat()


def due(now, state):
    if now.timestamp() < state.get('not_before', 0):
        return False
    day=scheduled_day(now)
    if not state.get('date'):
        return now.astimezone(BEIJING).hour>=17
    if state['date']>day:
        return False
    if state['date']!=day:
        return True
    if state.get('status') == 'completed' or state.get('attempts', 0) >= 2:
        return False
    return now.timestamp() - state.get('finished_at', state.get('started_at', 0)) >= 1800


def fetch_main(repo,folder):
    """Bound network retries and update the exact remote tracking reference."""
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never')
    command=['git','-C',str(repo),'-c','credential.interactive=false',
        '-c','http.lowSpeedLimit=1','-c','http.lowSpeedTime=30','fetch','--no-tags',
        'origin','+refs/heads/main:refs/remotes/origin/main']
    for attempt in range(3):
        try:
            subprocess.run(command,check=True,timeout=90,capture_output=True,env=env)
            return
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
            detail=getattr(exc,'stderr',None) or str(exc)
            if isinstance(detail,bytes):detail=detail.decode(errors='replace')
            (folder/f'fetch-error-{attempt+1}.log').write_text(detail)
            if attempt==2:raise
            time.sleep((2,5)[attempt])


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def run(repo, home, codex, prompt_path, now=None):
    """A process lock covers the entire run; failures are recorded, never passes."""
    now = now or datetime.now(BEIJING)
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    with (home/'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'already_running'
        state_path = home/'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if not due(now, state):
            return 'not_due'
        day = scheduled_day(now)
        attempt = state.get('attempts', 0) + 1 if state.get('date') == day else 1
        folder = home/'runs'/f'{day}-{attempt}'
        folder.mkdir(parents=True, exist_ok=True)
        state = dict(date=day, status='running', started_at=now.timestamp(),
                     attempts=attempt, report=str(folder/'report.md'))
        save(state_path, state)
        try:
            fetch_main(repo,folder)
            worktree = folder/'worktree'
            subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '--detach',
                            str(worktree), 'origin/main'], check=True, timeout=180, capture_output=True)
            ledger = json.loads((worktree/'linyuan/.automation/fc_state.json').read_text())
            manifest = dict(checked_at=now.isoformat(), videos=latest_publications(ledger),
                            selection='latest_four_distinct_published_receipts_across_dates',
                            public_visibility_verified=False)
            save(folder/'selected-videos.json', manifest)
            prompt = prompt_path.read_text() + (
                '\n\n本次运行目录：' + str(folder) + '\n隔离工作区：' + str(worktree)
                + '\n初选回执（仍须核对公开状态）：\n' + json.dumps(manifest, ensure_ascii=False))
            # Use the user's existing model/auth/permission configuration.
            # Do not move credentials to Actions or change global permissions.
            command = [str(codex), 'exec', '--json', '--color', 'never', '-C', str(worktree),
                       '--output-last-message', str(folder/'report.md'), '-']
            with (folder/'events.jsonl').open('w') as events, (folder/'stderr.log').open('w') as errors:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=events,
                                           stderr=errors, text=True, start_new_session=True)
                try:
                    process.communicate(prompt, timeout=10800)
                except subprocess.TimeoutExpired:
                    import signal
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise TimeoutError('Daily audit exceeded three hours; review retained worktree')
                if process.returncode:
                    raise RuntimeError(f'Codex exited with status {process.returncode}; see stderr.log')
            if not (folder/'report.md').is_file() or not (folder/'report.md').read_text().strip():
                raise RuntimeError('Codex returned no audit report')
            state.update(status='completed', finished_at=time.time(),
                         note='Agent execution completed; video verdicts are in report.md, not inferred here')
        except Exception as exc:
            state.update(status='failed', finished_at=time.time(), error=str(exc))
            save(state_path, state)
            if not (folder/'report.md').exists():
                (folder/'report.md').write_text('巡检未完成。启动或执行失败：'+str(exc)+'\n请查看同目录错误日志；不得把这次任务计作视频已检查。\n')
            (home/'latest-report.md').write_text((folder/'report.md').read_text())
            raise
        save(state_path, state)
        (home/'latest-report.md').write_text((folder/'report.md').read_text())
        return 'completed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--codex', type=Path, required=True)
    parser.add_argument('--prompt', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Validate setup without running an agent')
    args = parser.parse_args()
    if args.check:
        assert args.repo.is_dir() and args.prompt.is_file() and os.access(args.codex, os.X_OK)
        subprocess.run(['git', '-C', str(args.repo), 'rev-parse', '--show-toplevel'], check=True)
        subprocess.run([str(args.codex), 'login', 'status'], check=True)
        print('Setup checked; schedule=17:00 Asia/Shanghai; no audit agent launched')
        return
    print(run(args.repo, args.home, args.codex, args.prompt), flush=True)


if __name__ == '__main__':
    main()
