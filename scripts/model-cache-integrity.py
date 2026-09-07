#!/usr/bin/env python3
"""Create or verify the accepted immutable Hugging Face snapshot manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import multiprocessing
import re
import stat
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
CHECK_INTERVAL_SECONDS = 0.25


def fail(message: str) -> None:
    raise RuntimeError(message)


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def hash_regular(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            fail(f"special file rejected: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            fail(f"file mutated while hashing: {path}")
        return digest.hexdigest(), before.st_size
    finally:
        os.close(descriptor)


def inspect_snapshot(repo_root: Path, snapshot: Path, objects: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    pending = [snapshot]
    while pending:
        directory = pending.pop()
        relative_directory = directory.relative_to(snapshot)
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name, reverse=True)
        for child in children:
            path = Path(child.path)
            logical = (relative_directory / child.name).as_posix()
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                entries.append({"path": logical, "type": "directory"})
                pending.append(path)
                continue
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target):
                    fail(f"absolute symlink rejected: {path}")
                lexical_target = Path(os.path.abspath(os.path.join(path.parent, target)))
                if not within(lexical_target, repo_root):
                    fail(f"escaping symlink rejected: {path} -> {target}")
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    fail(f"invalid symlink rejected: {path}: {error}")
                if not within(resolved, repo_root) or not resolved.is_file():
                    fail(f"symlink target must be a regular file inside the repository cache: {path}")
                object_name = resolved.relative_to(repo_root).as_posix()
                manifest_entry = {"path": logical, "type": "symlink", "target": target, "object": object_name}
            elif stat.S_ISREG(metadata.st_mode):
                resolved = path
                object_name = resolved.relative_to(repo_root).as_posix()
                manifest_entry = {"path": logical, "type": "file", "object": object_name}
            else:
                fail(f"special file rejected: {path}")
            if object_name not in objects:
                digest, size = hash_regular(resolved)
                objects[object_name] = {"sha256": digest, "size": size}
            if manifest_entry["type"] == "symlink":
                after = path.lstat()
                before_identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
                after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                if before_identity != after_identity or not stat.S_ISLNK(after.st_mode) or os.readlink(path) != target:
                    fail(f"symlink mutated while inspecting: {path}")
            entries.append(manifest_entry)
    entries.sort(key=lambda entry: entry["path"])
    return entries


def build_manifest(cache_root_arg: str, repo_id: str, revisions: list[str]) -> dict[str, Any]:
    if not REPO_ID_RE.fullmatch(repo_id):
        fail("repository id must be exactly owner/name")
    unique_revisions = sorted(set(revisions))
    if not unique_revisions or any(not REVISION_RE.fullmatch(revision) for revision in unique_revisions):
        fail("every revision must be a full 40-hex commit")
    cache_root_path = Path(cache_root_arg)
    cache_metadata = cache_root_path.lstat()
    if not stat.S_ISDIR(cache_metadata.st_mode) or stat.S_ISLNK(cache_metadata.st_mode):
        fail(f"cache root must be a non-symlink directory: {cache_root_path}")
    cache_root = cache_root_path.resolve(strict=True)
    repo_root = cache_root / "hub" / f"models--{repo_id.replace('/', '--')}"
    repo_metadata = repo_root.lstat()
    if not stat.S_ISDIR(repo_metadata.st_mode) or stat.S_ISLNK(repo_metadata.st_mode):
        fail(f"repository cache must be a non-symlink directory: {repo_root}")
    repo_root = repo_root.resolve(strict=True)
    objects: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, list[dict[str, str]]] = {}
    for revision in unique_revisions:
        snapshot = repo_root / "snapshots" / revision
        metadata = snapshot.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            fail(f"snapshot must be a non-symlink directory: {snapshot}")
        snapshots[revision.lower()] = inspect_snapshot(repo_root, snapshot, objects)
    return {
        "schema_version": 1,
        "repo_id": repo_id,
        "revisions": snapshots,
        "objects": objects,
    }


def allocated_tree(cache_root: Path) -> tuple[int, int]:
    allocated = 0
    files = 0
    pending = [cache_root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as iterator:
            for child in iterator:
                metadata = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(Path(child.path))
                elif stat.S_ISREG(metadata.st_mode):
                    allocated += metadata.st_blocks * 512
                    files += 1
                elif not stat.S_ISLNK(metadata.st_mode):
                    fail(f"special cache entry rejected: {child.path}")
    return allocated, files


def download_snapshots(cache_root: str, repo_id: str, revisions: list[str], max_artifact_bytes: int) -> None:
    import resource
    from huggingface_hub import snapshot_download

    resource.setrlimit(resource.RLIMIT_FSIZE, (max_artifact_bytes, max_artifact_bytes))
    token = os.environ.get("HF_TOKEN") or False
    for revision in revisions:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            token=token,
            cache_dir=str(Path(cache_root) / "hub"),
        )


def bounded_fetch(
    cache_root_arg: str,
    repo_id: str,
    revisions: list[str],
    max_artifact_bytes: int,
    max_artifact_files: int,
    max_cache_bytes: int,
    max_cache_files: int,
    min_free_bytes: int,
) -> None:
    try:
        from huggingface_hub import HfApi
    except ImportError as error:
        fail(f"huggingface_hub is unavailable: {error}")
    if not REPO_ID_RE.fullmatch(repo_id):
        fail("repository id must be exactly owner/name")
    unique_revisions = sorted(set(revisions))
    if not unique_revisions or any(not REVISION_RE.fullmatch(revision) for revision in unique_revisions):
        fail("every revision must be a full 40-hex commit")
    limits = (max_artifact_bytes, max_artifact_files, max_cache_bytes, max_cache_files, min_free_bytes)
    if any(limit <= 0 for limit in limits):
        fail("fetch limits must be positive integers")
    cache_root_path = Path(cache_root_arg)
    metadata = cache_root_path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail(f"cache root must be a non-symlink directory: {cache_root_path}")
    cache_root = cache_root_path.resolve(strict=True)

    expected_bytes = 0
    expected_files = 0
    token = os.environ.get("HF_TOKEN") or False
    api = HfApi()
    for revision in unique_revisions:
        info = api.model_info(repo_id=repo_id, revision=revision, files_metadata=True, token=token)
        if not isinstance(info.sha, str) or info.sha.lower() != revision.lower():
            fail(f"artifact metadata did not resolve the pinned revision: {revision}")
        for sibling in info.siblings:
            if not isinstance(sibling.size, int) or sibling.size < 0:
                fail(f"artifact metadata has no valid size: {sibling.rfilename}")
            expected_bytes += sibling.size
            expected_files += 1
    if expected_bytes > max_artifact_bytes or expected_files > max_artifact_files:
        fail(
            f"artifact metadata exceeds tuple limit: {expected_bytes} bytes/"
            f"{expected_files} files"
        )

    initial_allocated, _ = allocated_tree(cache_root)
    def enforce_storage_budget() -> None:
        allocated, files = allocated_tree(cache_root)
        free = shutil.disk_usage(cache_root).free
        if allocated > max_cache_bytes or files > max_cache_files or free < min_free_bytes:
            fail(
                f"cache storage budget exceeded: {allocated} allocated bytes/"
                f"{files} files/{free} free bytes"
            )
        if allocated - initial_allocated > max_artifact_bytes:
            fail(
                f"artifact download storage budget exceeded: "
                f"{allocated - initial_allocated} allocated bytes"
            )

    enforce_storage_budget()
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=download_snapshots,
        args=(str(cache_root), repo_id, unique_revisions, max_artifact_bytes),
    )
    process.start()
    try:
        while process.is_alive():
            process.join(CHECK_INTERVAL_SECONDS)
            enforce_storage_budget()
    except BaseException:
        if process.is_alive():
            process.terminate()
        process.join()
        raise
    if process.exitcode != 0:
        fail(f"bounded snapshot download failed with exit status {process.exitcode}")
    enforce_storage_budget()


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()

def read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            fail("accepted manifest must be a regular non-symlink file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            fail("accepted manifest mutated while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create", "fetch", "verify"))
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision", action="append", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--max-artifact-bytes", type=int)
    parser.add_argument("--max-artifact-files", type=int)
    parser.add_argument("--max-cache-bytes", type=int)
    parser.add_argument("--max-cache-files", type=int)
    parser.add_argument("--min-free-bytes", type=int)
    arguments = parser.parse_args()
    if arguments.command == "fetch":
        if arguments.manifest or any(
            limit is None
            for limit in (
                arguments.max_artifact_bytes,
                arguments.max_artifact_files,
                arguments.max_cache_bytes,
                arguments.max_cache_files,
                arguments.min_free_bytes,
            )
        ):
            fail("fetch requires every storage limit and does not accept --manifest")
        bounded_fetch(
            arguments.cache_root,
            arguments.repo_id,
            arguments.revision,
            arguments.max_artifact_bytes,
            arguments.max_artifact_files,
            arguments.max_cache_bytes,
            arguments.max_cache_files,
            arguments.min_free_bytes,
        )
        return
    actual = build_manifest(arguments.cache_root, arguments.repo_id, arguments.revision)
    if arguments.command == "create":
        if arguments.manifest:
            fail("create writes the canonical manifest to stdout")
        sys.stdout.buffer.write(canonical(actual))
        return
    if not arguments.manifest:
        fail("verify requires --manifest")
    manifest_path = Path(arguments.manifest)
    try:
        accepted_bytes = read_regular_bytes(manifest_path)
        accepted = json.loads(accepted_bytes)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"accepted manifest is unreadable: {error}")
    if accepted_bytes != canonical(accepted):
        fail("accepted manifest is not canonical")
    if accepted_bytes != canonical(actual):
        fail("model cache differs from the accepted immutable manifest")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"model-cache-integrity: {error}", file=sys.stderr)
        raise SystemExit(1)
