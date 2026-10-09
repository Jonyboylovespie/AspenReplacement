"""Private, atomic writes for account snapshots and encrypted sessions."""
import os
from pathlib import Path
import tempfile


def atomic_write(path, payload):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
