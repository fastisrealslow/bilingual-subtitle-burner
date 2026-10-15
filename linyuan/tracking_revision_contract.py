"""Exact, authorized archive and immutable accepted media binding."""
import hashlib
from pathlib import Path

BVID='BV1UEpC6bEQB'
SLUG='ly-1013-5a1382'
OLD_SHA='8cdaf95155cefe57d0aad3858368a0eb44420d17d831ae1a9bcd0ba4ee88d342'
SOURCE_SHA='ced49dbeb34439f98149da57e9d361a18f8d62c852dea4a49b6b54729d2b73ea'
TITLE='林园：牛市大概率延续，可转债进可攻退可守'
NEW_SHA='60e8dc90eb7e04386812344d4cf126b04b646cfb425f4f9e3203ed94499d2506'
ARTIFACT_ID=11949201565


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
