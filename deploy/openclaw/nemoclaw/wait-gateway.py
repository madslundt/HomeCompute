#!/usr/bin/env python3
"""Wait for only the separate synthetic gateway; never invoke an agent turn."""
from pathlib import Path
import subprocess
import time

binary = Path.home() / '.local/bin/openshell'
end = time.monotonic() + 60
while time.monotonic() < end:
    try:
        result = subprocess.run([str(binary), 'inference', 'get', '--gateway', 'nemoclaw-9123'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        if result.returncode == 0:
            raise SystemExit(0)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(1)
raise SystemExit('Synthetic gateway did not become ready')
