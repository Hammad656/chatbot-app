"""Private local document storage, shared between API and worker containers."""

import os
import re
from pathlib import Path
from uuid import uuid4


def path_for(key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("Invalid document storage key.")
    return Path(os.getenv("UPLOAD_DIR", "/tmp/resolveai-uploads")).resolve() / key


def save_upload(content: bytes) -> str:
    key = uuid4().hex
    path = path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(content)
    return key


def read_upload(key: str) -> bytes:
    return path_for(key).read_bytes()


def delete_upload(key: str | None) -> None:
    if key:
        path_for(key).unlink(missing_ok=True)
