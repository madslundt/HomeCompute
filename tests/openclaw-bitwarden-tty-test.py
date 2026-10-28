#!/usr/bin/env python3
"""Drive the actual root provisioning CLI through a controlling pseudo-terminal.

Run as root on Linux home-core. Inputs and files are disposable synthetic values;
no real Bitwarden tokens, account credentials or custody paths are accessed.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import pty
import select
import signal
import stat
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/provision-openclaw-bitwarden.py'
EMAIL = 'terminal-fixture@example.invalid'
TOKEN = '0.00000000-0000-4000-8000-000000000004.' + 'a' * 44 + ':' + 'b' * 44
IDS = ['00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002',
       '00000000-0000-4000-8000-000000000003']


@unittest.skipUnless(os.geteuid() == 0, 'actual installer requires root; run the PTY test on home-core')
class TerminalIntakeTests(unittest.TestCase):
    def run_cli(self, directory: Path, replies: list[tuple[bytes, bytes]]):
        pid, master = pty.fork()
        if pid == 0:
            # A fresh interpreter avoids inherited Python stream buffering.
            program = '''import runpy,sys,termios
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
module=runpy.run_path(sys.argv[1],run_name="custody_terminal_fixture")
module["main"].__globals__["DESTINATION"]=Path(sys.argv[2])
sys.argv=[sys.argv[1],"--organization-id",sys.argv[3],"--project-id",sys.argv[4],"--password-secret-id",sys.argv[5]]
before=termios.tcgetattr(0)
code=0
try:
    module["main"]()
except BaseException:
    print("FIXTURE_CLI_REFUSED",flush=True)
    code=2
finally:
    print("FIXTURE_TERMINAL_RESTORED" if termios.tcgetattr(0)==before else "FIXTURE_TERMINAL_CHANGED",flush=True)
raise SystemExit(code)
'''
            os.execl(sys.executable, sys.executable, '-c', program, str(SCRIPT), str(directory), *IDS)
        transcript, next_reply, status = bytearray(), 0, None
        deadline = time.monotonic() + 8
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    transcript.extend(chunk)
                    if next_reply < len(replies) and replies[next_reply][0] in transcript:
                        os.write(master, replies[next_reply][1])
                        next_reply += 1
            # A timeout cannot leave a hidden-input process running.
            waited, status = os.waitpid(pid, os.WNOHANG)
            while waited == 0 and time.monotonic() < deadline:
                select.select([], [], [], 0.01)
                waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == 0:
                os.kill(pid, signal.SIGKILL)
                _, status = os.waitpid(pid, 0)
        finally:
            os.close(master)
            if status is None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), bytes(transcript), next_reply

    def replies(self, confirmation=TOKEN):
        return [(b'Hotels.com sign-in email (hidden): ', EMAIL.encode() + b'\n'),
                (b'Bitwarden MACHINE access token (hidden): ', TOKEN.encode() + b'\n'),
                (b'Paste the same token again (hidden): ', confirmation.encode() + b'\n')]

    def test_interactive_cli_prompts_and_stores_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve() / 'custody'
            code, transcript, count = self.run_cli(directory, self.replies())
            self.assertEqual(code, 0, 'CLI must accept a controlling terminal and complete intake')
            self.assertEqual(count, 3)
            self.assertNotIn(EMAIL.encode(), transcript)
            self.assertNotIn(TOKEN.encode(), transcript)
            self.assertIn(b'FIXTURE_TERMINAL_RESTORED', transcript)
            self.assertIn(b'"stored": true', transcript)
            self.assertEqual((directory / 'bitwarden-access-token').read_text().strip(), TOKEN)
            self.assertEqual(json.loads((directory / 'hotels.json').read_text())['username'], EMAIL)
            for path, mode in [(directory, 0o700), (directory / 'hotels.json', 0o600),
                               (directory / 'bitwarden-access-token', 0o600)]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_confirmation_mismatch_creates_no_credential_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve() / 'custody'
            code, transcript, count = self.run_cli(directory, self.replies(TOKEN.replace('a', 'x')))
            self.assertEqual(count, 3)
            self.assertNotEqual(code, 0)
            self.assertNotIn(TOKEN.encode(), transcript)
            self.assertIn(b'FIXTURE_TERMINAL_RESTORED', transcript)
            self.assertEqual(list(directory.iterdir()), [])

    def test_interrupted_input_restores_terminal_and_creates_no_credential_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve() / 'custody'
            code, transcript, count = self.run_cli(directory, [(b'Hotels.com sign-in email (hidden): ', b'\x03')])
            self.assertEqual(count, 1)
            self.assertNotEqual(code, 0)
            self.assertIn(b'FIXTURE_TERMINAL_RESTORED', transcript)
            self.assertEqual(list(directory.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
