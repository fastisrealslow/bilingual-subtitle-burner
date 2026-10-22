"""Stdlib-only preparation receipt; never change source or inventory verdicts."""
import json
import os
from pathlib import Path


def report(python_outcome, media_outcome, inventory):
    if python_outcome == media_outcome == 'success':
        return None
    if not isinstance(inventory, dict):
        inventory = {}
    return dict(kind='inventory_runtime_preparation',
                stage='python_runtime' if python_outcome != 'success' else 'media_tools',
                source_rejected=False, inventory_refreshed=False,
                previous_inventory_updated_at=inventory.get('updated_at'),
                python_outcome=python_outcome, media_outcome=media_outcome)


def main():
    try:
        inventory = json.loads(Path('linyuan/.automation/source_inventory.json').read_text())
    except (OSError, ValueError):
        inventory = {}
    result = report(os.environ.get('INVENTORY_PYTHON_OUTCOME'),
                    os.environ.get('INVENTORY_MEDIA_OUTCOME'), inventory)
    if result is not None:
        Path('inventory-preparation-failure.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))


if __name__ == '__main__':
    main()
