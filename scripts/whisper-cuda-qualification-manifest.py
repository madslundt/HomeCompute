"""Strict staged WAV manifest validation for guarded Whisper qualification."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import wave

STAGING_DIR = re.compile(r"(?:/private)?/tmp/homecompute-whisper-stt\.[A-Za-z0-9_-]+\Z")
INPUT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INPUTS = 4
MAX_AUDIO_SECONDS = 7_200
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_NATIVE_TIMEOUT = 14_400
def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _regular_private_file(path: Path, owner: int, *, max_bytes: int):
    try:
        info = path.lstat()
    except OSError:
        raise ValueError("private manifest input is unavailable") from None
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != owner or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > max_bytes):
        raise ValueError("private manifest input ownership, mode or size is invalid")
    return info


def _read_fd(fd: int, max_bytes: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("private manifest input exceeds its bound")
    return b"".join(chunks)


def _validate_audio(audio: bytes, frames: int, max_seconds: int) -> None:
    if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
        raise ValueError("manifest frame count is invalid")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getcomptype() != "NONE" or wav.getnchannels() != 1 or
                    wav.getsampwidth() != 2 or wav.getframerate() != 16_000 or
                    wav.getnframes() != frames or frames > max_seconds * 16_000):
                raise ValueError("audio must match the declared mono 16 kHz PCM16 sample count")
            expected = frames * 2
            if len(wav.readframes(frames)) != expected:
                raise ValueError("audio PCM payload is truncated")
    except (wave.Error, EOFError, OSError):
        raise ValueError("audio is not a complete mono 16 kHz PCM16 WAV") from None


def _validate_manifest(path: Path, owner: int):
    parent = path.parent
    if (not STAGING_DIR.fullmatch(str(parent)) or parent.is_symlink() or
            not parent.is_dir()):
        raise ValueError("manifest must be inside the private operator staging directory")
    parent_info = parent.lstat()
    if parent_info.st_uid != owner or stat.S_IMODE(parent_info.st_mode) != 0o700:
        raise ValueError("operator staging directory ownership or mode is invalid")
    if path.name != "qualification-manifest.json":
        raise ValueError("manifest must use the fixed qualification-manifest.json name")
    before = _regular_private_file(path, owner, max_bytes=MAX_MANIFEST_BYTES)
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parent, dir_flags)
    try:
        opened_dir = os.fstat(directory_fd)
        if ((opened_dir.st_dev, opened_dir.st_ino) != (parent_info.st_dev, parent_info.st_ino) or
                opened_dir.st_uid != owner or stat.S_IMODE(opened_dir.st_mode) != 0o700):
            raise ValueError("operator staging directory changed during open")
        manifest_fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                              dir_fd=directory_fd)
        try:
            after = os.fstat(manifest_fd)
            if ((after.st_dev, after.st_ino) != (before.st_dev, before.st_ino) or
                    after.st_uid != owner or stat.S_IMODE(after.st_mode) != 0o600 or
                    after.st_size > MAX_MANIFEST_BYTES):
                raise ValueError("manifest changed during open")
            raw = _read_fd(manifest_fd, MAX_MANIFEST_BYTES)
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        finally:
            os.close(manifest_fd)
    except BaseException:
        os.close(directory_fd)
        raise
    required = {"schema_version", "max_audio_seconds", "max_upload_bytes",
                "native_timeout_seconds", "inputs"}
    if (not isinstance(document, dict) or set(document) != required or
            type(document.get("schema_version")) is not int or document.get("schema_version") != 1):
        os.close(directory_fd)
        raise ValueError("qualification manifest fields or version are invalid")
    maximum = document["max_audio_seconds"]
    upload = document["max_upload_bytes"]
    timeout = document["native_timeout_seconds"]
    if (not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= MAX_AUDIO_SECONDS or
            not isinstance(upload, int) or isinstance(upload, bool) or
            not maximum * 32_000 + 44 <= upload <= MAX_UPLOAD_BYTES or
            not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_NATIVE_TIMEOUT):
        os.close(directory_fd)
        raise ValueError("qualification manifest limits are invalid or inconsistent")
    inputs = document["inputs"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_INPUTS:
        os.close(directory_fd)
        raise ValueError("qualification manifest must contain one to four inputs")
    ids = set()
    filenames = set()
    aggregate_bytes = 0
    validated = []
    try:
        for row in inputs:
            if not isinstance(row, dict) or set(row) != {"id", "file", "bytes", "sha256", "frames"}:
                raise ValueError("qualification input fields are invalid")
            input_id, filename = row["id"], row["file"]
            if (not isinstance(input_id, str) or not INPUT_ID.fullmatch(input_id) or input_id in ids or
                    not isinstance(filename, str) or not INPUT_ID.fullmatch(filename) or filename == "." or
                    filename == path.name or filename in filenames or
                    not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or
                    not 44 <= row["bytes"] <= upload or
                    not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
                raise ValueError("qualification input metadata is invalid")
            ids.add(input_id)
            filenames.add(filename)
            aggregate_bytes += row["bytes"]
            if aggregate_bytes > MAX_UPLOAD_BYTES:
                raise ValueError("aggregate qualification audio exceeds its byte bound")
            input_path = parent / filename
            before = _regular_private_file(input_path, owner, max_bytes=upload)
            if before.st_size != row["bytes"]:
                raise ValueError("audio byte count differs from manifest")
            input_fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                opened = os.fstat(input_fd)
                if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                        (before.st_dev, before.st_ino, row["bytes"]) or opened.st_uid != owner or
                        stat.S_IMODE(opened.st_mode) != 0o600):
                    raise ValueError("audio changed during open")
                audio = _read_fd(input_fd, upload)
            finally:
                os.close(input_fd)
            if hashlib.sha256(audio).hexdigest() != row["sha256"]:
                raise ValueError("audio SHA-256 differs from manifest")
            _validate_audio(audio, row["frames"], maximum)
            validated.append({"id": input_id, "bytes": row["bytes"], "sha256": row["sha256"],
                              "frames": row["frames"], "audio": audio})
    except BaseException:
        os.close(directory_fd)
        raise
    return {"max_audio_seconds": maximum, "max_upload_bytes": upload,
            "native_timeout_seconds": timeout, "inputs": validated,
            "operator_dir_fd": directory_fd, "operator_uid": owner}


