#!/usr/bin/env python3
from __future__ import annotations

import http.client


connection = http.client.HTTPConnection("127.0.0.1", 8004, timeout=5)
try:
    connection.request("GET", "/health")
    response = connection.getresponse()
    body = response.read(1_024)
    if response.status != 200 or body != b'{"status":"ok"}':
        raise SystemExit(1)
finally:
    connection.close()
