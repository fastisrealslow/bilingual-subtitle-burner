import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("scripts", "steps"):
    sys.path.insert(0, str(ROOT / sub))
