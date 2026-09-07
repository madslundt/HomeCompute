"""Secure benchmark configuration, path handling, and artifact I/O."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class BenchmarkError(Exception):
    """A configuration or execution error that is safe to show to the operator."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _lstat(path: Path, context: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise BenchmarkError(f"{context} does not exist: {path}") from exc
    except OSError as exc:
        raise BenchmarkError(f"cannot inspect {context} {path}: {exc}") from exc


def _reject_unsafe_tree(path: Path, context: str) -> None:
    metadata = _lstat(path, context)
    if stat.S_ISLNK(metadata.st_mode):
        raise BenchmarkError(f"{context} must not contain symbolic links: {path}")
    if stat.S_ISREG(metadata.st_mode):
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise BenchmarkError(f"{context} must contain only regular files and directories: {path}")
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        raise BenchmarkError(f"cannot inspect {context} {path}: {exc}") from exc
    for entry in entries:
        _reject_unsafe_tree(Path(entry.path), context)


def _safe_path(
    path: Path,
    root: Path,
    context: str,
    *,
    directory: bool = False,
    recursive: bool = False,
) -> Path:
    root = _absolute(root)
    path = _absolute(path)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BenchmarkError(f"{context} must remain under {root}: {path}") from exc
    root_metadata = _lstat(root, f"{context} root")
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise BenchmarkError(f"{context} root must be a real directory: {root}")
    current = root
    for part in relative.parts:
        current = current / part
        metadata = _lstat(current, context)
        if stat.S_ISLNK(metadata.st_mode):
            raise BenchmarkError(f"{context} must not use symbolic links: {current}")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise BenchmarkError(f"{context} parent is not a directory: {current}")
    metadata = _lstat(path, context)
    expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected:
        kind = "directory" if directory else "regular file"
        raise BenchmarkError(f"{context} must be a {kind}, not a symbolic link or special file: {path}")
    if recursive:
        _reject_unsafe_tree(path, context)
    return path


def _safe_reference(
    base: Path,
    reference: Any,
    root: Path,
    context: str,
    *,
    directory: bool = False,
    recursive: bool = False,
) -> Path:
    if not isinstance(reference, str) or not reference:
        raise BenchmarkError(f"{context} must be a non-empty path string")
    referenced = Path(reference)
    path = referenced if referenced.is_absolute() else base / referenced
    return _safe_path(path, root, context, directory=directory, recursive=recursive)


def _prepare_parent(path: Path) -> None:
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, mode=0o700)
    metadata = _lstat(parent, "output parent")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BenchmarkError(f"output parent must be a real directory: {parent}")


def _validate_output_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BenchmarkError(f"output artifact must be a safe regular non-symlink file: {path}")

def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path = _absolute(path)
    _prepare_parent(path)
    _validate_output_file(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    path = _absolute(path)
    metadata = _lstat(path, "JSON input")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BenchmarkError(f"JSON input must be a regular non-symlink file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    _write_bytes_atomic(path, payload)


def write_json_exclusive(path: Path, value: Any) -> None:
    path = _absolute(path)
    _prepare_parent(path)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BenchmarkError(f"selection output already exists: {path}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def append_jsonl(path: Path, value: Any) -> None:
    path = _absolute(path)
    _prepare_parent(path)
    _validate_output_file(path)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (json.dumps(value, sort_keys=True) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    path = _absolute(path)
    if not path.exists():
        return []
    metadata = _lstat(path, "JSONL input")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BenchmarkError(f"JSONL input must be a regular non-symlink file: {path}")
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"invalid JSON in {path} line {number}: {exc}") from exc
        if not isinstance(row, dict):
            raise BenchmarkError(f"{path} line {number} must contain a JSON object")
        rows.append(row)
    return rows


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def require_fields(value: dict[str, Any], fields: list[str], context: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise BenchmarkError(f"{context} is missing: {', '.join(missing)}")


@dataclass(frozen=True)
class LoadedPlan:
    path: Path
    root: Path
    value: dict[str, Any]
    cases: list[dict[str, Any]]
