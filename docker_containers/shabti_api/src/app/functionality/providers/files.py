"""Files the configurator writes for a running installation, re-read whenever they change.

Keyed on the modification time rather than cached for good, as `embeddings_config` is: providers
and their keys can be managed after install, and the API picks the change up without a restart.
"""

import os
from collections.abc import Callable
from typing import Any

_cache: dict[str, tuple[float, Any]] = {}


def mtime(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def read_cached(path: str, parse: Callable[[str], Any]) -> Any | None:
    """What `parse` made of the file, or None when there is no file.

    A file that can't be read or parsed raises, and is tried again on the next call.
    """
    modified = mtime(path)
    if modified is None:
        return None
    cached = _cache.get(path)
    if cached and cached[0] == modified:
        return cached[1]
    with open(path) as f:
        value = parse(f.read())
    _cache[path] = (modified, value)
    return value
