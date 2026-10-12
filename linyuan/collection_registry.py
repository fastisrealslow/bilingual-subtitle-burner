"""Durable operational collection IDs; never put automation IDs in public copy.

Persist a creation reservation BEFORE the platform POST. An unknown outcome
stays blocked, never creates another collection. CI writes one explicit ledger
with the exact current GitHub blob lease; local runs use an atomic JSON file.
"""
import base64
import json
import os
from pathlib import Path
import subprocess

OWNER_MID = 275211725
REPO = 'fastisrealslow/bilingual-subtitle-burner'
KEY = 'linyuan/.automation/managed_collections.json'


class RegistryError(Exception):
    pass


def empty_registry():
    return dict(version=1, owner_mid=OWNER_MID, collections={})


def validate(value):
    if (not isinstance(value, dict) or value.get('version') != 1
            or value.get('owner_mid') != OWNER_MID
            or not isinstance(value.get('collections'), dict)):
        raise RegistryError('registry_shape_or_owner_mismatch')
    return value


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.remote = os.environ.get('GITHUB_ACTIONS') == 'true'
        self.sha = None
        if self.remote and os.environ.get('GITHUB_REPOSITORY') != REPO:
            raise RegistryError('registry_repository_mismatch')

    def request(self, args, **options):
        try:
            return subprocess.run(['gh', 'api', *args], capture_output=True,
                                  text=True, timeout=60, **options)
        except (OSError, subprocess.SubprocessError):
            raise RegistryError('registry_transport_failed') from None

    def load(self):
        if not self.remote:
            try:
                return validate(json.loads(self.path.read_text())) if self.path.exists() else empty_registry()
            except (OSError, ValueError):
                raise RegistryError('registry_unreadable') from None
        result = self.request([f'repos/{REPO}/contents/{KEY}?ref=main'])
        if result.returncode:
            if '(HTTP 404)' in result.stderr:
                return empty_registry()
            raise RegistryError('registry_remote_read_failed')
        try:
            record = json.loads(result.stdout)
            self.sha = record['sha']
            if record['type'] != 'file' or record['encoding'] != 'base64':
                raise ValueError()
            return validate(json.loads(base64.b64decode(record['content'])))
        except (KeyError, ValueError, TypeError):
            raise RegistryError('registry_remote_invalid') from None

    def save(self, value):
        validate(value)
        content = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        if self.remote:
            payload = dict(message='chore(collections): persist owned collection identity',
                           branch='main', content=base64.b64encode(content.encode()).decode())
            if self.sha:
                payload['sha'] = self.sha
            result = self.request(['--method', 'PUT',
                                   f'repos/{REPO}/contents/{KEY}', '--input', '-'],
                                  input=json.dumps(payload))
            if result.returncode:
                raise RegistryError('registry_remote_write_failed')
            try:
                self.sha = json.loads(result.stdout)['content']['sha']
            except (ValueError, KeyError, TypeError):
                raise RegistryError('registry_write_unconfirmed') from None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from batch_delivery import write_json
        write_json(self.path, value)
