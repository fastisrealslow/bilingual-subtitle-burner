"""Durable, bounded round-robin source refresh; links never count as stock."""
import json
from pathlib import Path


def rotating_batch(name, values, path, limit, key=lambda value: str(value), state=None):
    """Checkpoint before each request, so failed/slow seeds cannot starve the tail."""
    unique, seen = [], set()
    for value in values:
        identity = key(value)
        if identity and identity not in seen:
            unique.append(value)
            seen.add(identity)
    if not unique:
        return
    limit = max(1, min(int(limit), len(unique)))
    path = Path(path) if path is not None else None
    if state is None:
        state = json.loads(path.read_text()) if path is not None and path.exists() else {}
    if not isinstance(state, dict):
        raise ValueError('invalid seed scan state')
    cursor = state.get(name, {}).get('cursor', 0)
    if type(cursor) is not int or cursor < 0:
        raise ValueError('invalid seed scan cursor')
    for offset in range(limit):
        index = (cursor + offset) % len(unique)
        state[name] = dict(cursor=(index + 1) % len(unique), seed_count=len(unique))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(path)
        yield unique[index]
