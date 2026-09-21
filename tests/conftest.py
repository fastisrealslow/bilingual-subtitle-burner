import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Historical production fixtures deliberately retain their 120-second
# contract. New reference-policy integration tests launch fresh processes
# with reference_v1 (and without an override) and verify the new defaults.
# Metadata validation independently reads each artifact's declared policy.
os.environ.setdefault('LINYUAN_CONTENT_POLICY', 'legacy120')
for sub in ("scripts", "steps"):
    sys.path.insert(0, str(ROOT / sub))
