"""Load a gitignored .env without overriding variables already set in the shell."""
import os
from pathlib import Path


def load_local_env(path=None):
    """Fill unset environment variables from path. Missing files are ignored."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / '.env'
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
