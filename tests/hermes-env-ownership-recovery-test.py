#!/usr/bin/env python3
"""Focused tests for the guarded Hermes env ownership recovery."""
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hermes_env_recovery", ROOT / "scripts/recover-hermes-env-ownership.py")
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


def tar_member(name, data=b"synthetic-secret=not-a-real-credential\n", *, kind=tarfile.REGTYPE,
               uid=1000, gid=1000, mode=0o640):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.uid, member.gid, member.mode = uid, gid, mode
        if kind == tarfile.REGTYPE:
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        else:
            member.linkname = "outside"
            archive.addfile(member)
    return target.getvalue()


class HermesEnvOwnershipRecoveryTest(unittest.TestCase):
    def test_replacement_preserves_file_bytes_and_sets_image_owner(self):
        data = b"synthetic-secret=not-a-real-credential\n"
        cases = ((".env", 0o640), ("config.yaml", 0o600))
        for name, mode in cases:
            with self.subTest(name=name):
                original, metadata = RECOVERY.file_from_tar(
                    tar_member(name, data, mode=mode), name)
                target = (998, 999, mode)

                repaired, repaired_meta = RECOVERY.file_from_tar(
                    RECOVERY.replacement_tar(name, original, metadata, target), name)

                self.assertEqual(repaired, data)
                self.assertEqual(
                    (repaired_meta.uid, repaired_meta.gid, repaired_meta.mode), target)

    def test_accepts_directory_types_with_permission_bits_only(self):
        target = io.BytesIO()
        with tarfile.open(fileobj=target, mode="w") as archive:
            for name, uid, gid in (("sandbox", 998, 999),
                                   ("sandbox/.hermes", 998, 999)):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                member.uid, member.gid, member.mode = uid, gid, 0o755
                archive.addfile(member)
        with tarfile.open(fileobj=io.BytesIO(target.getvalue())) as archive:
            sandbox, hermes_dir = RECOVERY.validate_sandbox_directories(archive)
        self.assertTrue(sandbox.isdir())
        self.assertTrue(hermes_dir.isdir())

    def test_rejects_symlink_env_entry(self):
        with self.assertRaisesRegex(ValueError, "one regular Hermes file"):
            RECOVERY.file_from_tar(tar_member(".env", kind=tarfile.SYMTYPE), ".env")

    def test_rejects_oversized_env_entry(self):
        target = io.BytesIO()
        with tarfile.open(fileobj=target, mode="w") as archive:
            member = tarfile.TarInfo(".env")
            member.size = RECOVERY.MAX_ENV_BYTES + 1
            archive.addfile(member, io.BytesIO(b"x" * (RECOVERY.MAX_ENV_BYTES + 1)))

        with self.assertRaisesRegex(ValueError, "bounded Hermes file"):
            RECOVERY.file_from_tar(target.getvalue(), ".env")


if __name__ == "__main__":
    unittest.main()
