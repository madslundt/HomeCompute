import json
import sys
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as response:
        healthy = response.status == 200 and json.load(response) == {"status": "ok"}
except Exception:
    healthy = False
sys.exit(0 if healthy else 1)
