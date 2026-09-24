"""Compatibility imports for isolated title trials."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from title_draft_profiles import (concise_draft, concise_messages,
    source_limits_schema, source_limits_messages)
