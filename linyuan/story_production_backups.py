"""Retain normal whole-answer backups when all proposed stories fail quality."""
from contextlib import contextmanager
import os


def plan(originals, selected):
    if not selected:
        return list(originals), None
    # A story can fail its scene cover while the SAME whole answer still works
    # as a normal video. Keep it as a baseline backup too; every old gate runs
    # again and accepted-range dedup remains unchanged. Never invent a range.
    backups = [{k: v for k, v in p.items() if k != 'story_selection'} for p in originals]
    return list(selected) + backups, len(selected)


@contextmanager
def writer_profile(baseline_backup):
    """Only the fallback writer changes; requested intent and counts survive."""
    if not baseline_backup:
        yield
        return
    key = 'LINYUAN_EDITORIAL_PROFILE'
    previous = os.environ.get(key)
    os.environ[key] = 'baseline'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
