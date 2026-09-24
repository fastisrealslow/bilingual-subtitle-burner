"""Read selected members of an immutable ZIP without downloading other videos."""
import io
import json
from pathlib import Path
import re
import shutil
import time
import zipfile

import requests


class RangeFile(io.RawIOBase):
    """A bounded, strictly validated HTTP range reader for zipfile."""

    def __init__(self, url, session=None, timeout_sec=180, max_bytes=1024**3):
        self.url = url
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.deadline = time.monotonic() + timeout_sec
        self.max_bytes = max_bytes
        self.size = None
        self.position = 0
        self.transferred = 0
        self._range(0, 0)

    def _range(self, start, end):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Selected artifact download deadline exceeded')
        expected = end - start + 1
        if expected > 8 * 1024**2:
            raise ValueError('Artifact range read exceeds memory budget')
        with self.session.get(self.url, headers={'Range': f'bytes={start}-{end}',
                'Accept-Encoding': 'identity'}, stream=True,
                timeout=(min(10, remaining), min(30, remaining))) as response:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if response.status_code != 206 or not match:
                raise ValueError('Artifact endpoint did not honor byte range')
            first, last, total = map(int, match.groups())
            if ((first, last) != (start, end) or not 0 < total <= self.max_bytes
                    or end >= total or self.size not in (None, total)):
                raise ValueError('Artifact range identity or size mismatch')
            self.size = total
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if time.monotonic() >= self.deadline:
                    raise TimeoutError('Selected artifact download deadline exceeded')
                data.extend(chunk)
                self.transferred += len(chunk)
                if len(data) > expected:
                    raise ValueError('Artifact range returned excess bytes')
            if len(data) != expected:
                raise ValueError('Artifact range response truncated')
            return bytes(data)

    def seekable(self):
        return True

    def close(self):
        if getattr(self, '_owns_session', False) and not self.closed:
            self.session.close()
        super().close()

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END):
            raise ValueError('Invalid seek origin')
        position = offset + (0 if whence == io.SEEK_SET else self.position if whence == io.SEEK_CUR else self.size)
        if not 0 <= position <= self.size:
            raise ValueError('Artifact seek outside ZIP')
        self.position = position
        return position

    def read(self, size=-1):
        count = self.size - self.position if size is None or size < 0 else min(size, self.size - self.position)
        if not count:
            return b''
        data = self._range(self.position, self.position + count - 1)
        self.position += len(data)
        return data


def extract_part(url, part_index, destination, **reader_options):
    """Preserve original metadata and CRC-check every selected file to EOF.

    The caller still applies all normal video/hash/subtitle/cover gates.
    Files are staged and only promoted after the whole selected set succeeds.
    """
    import tempfile
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with RangeFile(url, **reader_options) as reader, zipfile.ZipFile(reader) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or names.count('meta.json') != 1:
            raise ValueError('Ambiguous artifact member names')
        if archive.getinfo('meta.json').file_size > 4 * 1024**2:
            raise ValueError('Artifact metadata exceeds limit')
        metadata = archive.read('meta.json')
        payload = json.loads(metadata)
        parts = payload if isinstance(payload, list) else [payload]
        if type(part_index) is not int or not 0 <= part_index < len(parts):
            raise ValueError('Invalid artifact part index')
        part = parts[part_index]
        wanted = [part.get('final'), part.get('cover'), *(part.get('subtitle_files') or [])]
        if part.get('subtitle_edit_proof_version') == 1:
            wanted += list(part.get('subtitle_edit_proofs') or [])
        if any(not isinstance(n, str) or not n or Path(n).name != n or n == 'meta.json' for n in wanted):
            raise ValueError('Invalid selected delivery path')
        if len(wanted) != len(set(wanted)):
            raise ValueError('Duplicate selected delivery path')
        for name in wanted:
            entry = archive.getinfo(name)
            if entry.is_dir() or entry.file_size > reader.max_bytes:
                raise ValueError('Invalid selected delivery size')
        with tempfile.TemporaryDirectory(prefix='range-part-', dir=destination) as tmp:
            stage = Path(tmp)
            for name in wanted:
                with archive.open(name) as source, (stage/name).open('wb') as target:
                    shutil.copyfileobj(source, target, length=1024**2)
            (stage/'meta.json').write_bytes(metadata)
            for name in [*wanted, 'meta.json']:
                (stage/name).replace(destination/name)
        return {'transferred_bytes': reader.transferred, 'archive_bytes': reader.size,
                'selected_files': len(wanted) + 1}
