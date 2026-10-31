"""Identify the source tree a running Python process was started with."""
import hashlib
from pathlib import Path


def source_fingerprint(directory):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).glob('*.py')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


DIRECTORY = Path(__file__).parent
# Capture at import, not per request: refreshing a page cannot reload Python.
LOADED_SOURCE = source_fingerprint(DIRECTORY)


def runtime_status():
    return {'loaded_source': LOADED_SOURCE,
            'restart_required': source_fingerprint(DIRECTORY) != LOADED_SOURCE}
