#!/usr/bin/env python3
"""Initialize compute secrets without following destination or parent symlinks."""

from __future__ import annotations

import argparse
import errno
import os
import secrets
import stat
import sys

DESTINATIONS = ("hf_token", "vllm_api_key")


def fail(message: str) -> None:
    raise RuntimeError(message)


def inspect_destinations(directory_fd: int) -> set[str]:
    existing: set[str] = set()
    for name in DESTINATIONS:
        try:
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            fail(f"secret destination must be absent or a single-link regular non-symlink file: {name}")
        existing.add(name)
    return existing


def secure_existing(directory_fd: int, name: str, uid: int, gid: int) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            fail(f"secret destination changed type or link count during initialization: {name}")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o440)
    finally:
        os.close(descriptor)


def create_atomic(directory_fd: int, name: str, content: bytes, uid: int, gid: int) -> None:
    temporary = f".secret-{name}-{secrets.token_hex(16)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o440)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        fail(f"secret destination appeared during initialization: {name}")
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--gid", type=int, required=True)
    arguments = parser.parse_args()
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(arguments.directory, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            fail(f"secret parent must not be a symlink: {arguments.directory}")
        raise
    try:
        existing = inspect_destinations(directory_fd)
        for name in sorted(existing):
            secure_existing(directory_fd, name, arguments.uid, arguments.gid)
        if "hf_token" not in existing:
            create_atomic(directory_fd, "hf_token", b"", arguments.uid, arguments.gid)
        if "vllm_api_key" not in existing:
            create_atomic(directory_fd, "vllm_api_key", (secrets.token_hex(32) + "\n").encode(), arguments.uid, arguments.gid)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"initialize-compute-secrets: {error}", file=sys.stderr)
        raise SystemExit(1)
