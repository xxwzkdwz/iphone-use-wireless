"""Private, independent state; never reads a VICO installation or its secrets."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def state_home() -> Path:
    return Path(os.environ.get("VICO_IPHONE_HOME", str(Path.home() / ".local/share/vico-iphone"))).expanduser().resolve()


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def write_json(path: Path, value: dict) -> None:
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    if path.stat().st_size > 256 * 1024:
        raise ValueError(f"State file is too large: {path.name}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Invalid state: {path.name}")
    return value


@contextmanager
def operation_lock(home: Path):
    private_directory(home)
    fd = os.open(home / "operation.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another iPhone tool call is in progress; wait for that result.") from exc
        yield
    finally:
        os.close(fd)


def process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
