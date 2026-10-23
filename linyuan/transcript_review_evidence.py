"""Limits of evidence available to a text-only transcript reviewer."""
import re

# Match positive claims, not requests to check audio or lack-of-audio notices.
AUDIO_ACCESS_CLAIM = re.compile(
    r'听感(?:上)?(?:更像|像|是|为|表明|显示)'
    r'|(?:已|已经|经)(?:听过|听取|核听|回听|复听|核实(?:了)?(?:原音|音频|发音))'
    r'|(?:从|根据)(?:原音|音频|发音|听感)(?:可|来|判|证|听)')
