#!/usr/bin/env python3
"""Apply HomeCompute's security-sensitive MCPHub system settings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:3000"


def request(path: str, *, method: str = "GET", body: dict | None = None,
            token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        # HTTP response bodies may contain runtime diagnostics; never echo them.
        raise RuntimeError(f"MCPHub {method} {path} failed ({type(error).__name__})") from None
    if not isinstance(result, dict) or result.get("success") is False:
        raise RuntimeError(f"MCPHub {method} {path} returned an unsuccessful response")
    return result


def configure(password_file: Path) -> None:
    password = password_file.read_text().strip()
    if not password:
        raise RuntimeError("MCPHub administrator password file is empty")
    login = request("/api/auth/login", method="POST",
                    body={"username": "admin", "password": password})
    token = login.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("MCPHub login response did not include a session token")

    settings = request("/api/settings", token=token).get("data")
    current = settings.get("systemConfig", {}) if isinstance(settings, dict) else {}
    if not isinstance(current, dict):
        raise RuntimeError("MCPHub system settings response is invalid")
    routing = current.get("routing", {})
    if not isinstance(routing, dict):
        routing = {}
    desired_routing = {
        **routing,
        "enableBearerAuth": True,
        "enableGlobalRoute": False,
        "skipAuth": False,
        "allowAuthorizationPassthrough": False,
    }
    request("/api/system-config", method="PUT", token=token, body={
        "routing": desired_routing,
        "activityLog": {"storeToolPayload": False},
    })
    verified = request("/api/settings", token=token).get("data", {}).get("systemConfig", {})
    actual_routing = verified.get("routing", {}) if isinstance(verified, dict) else {}
    activity_log = verified.get("activityLog", {}) if isinstance(verified, dict) else {}
    if (actual_routing.get("enableBearerAuth") is not True
            or actual_routing.get("enableGlobalRoute") is not False
            or actual_routing.get("skipAuth") is not False
            or actual_routing.get("allowAuthorizationPassthrough") is not False
            or activity_log.get("storeToolPayload") is not False):
        raise RuntimeError("MCPHub security settings did not persist as requested")
    print("MCPHub bearer authentication, dashboard authentication, disabled upstream Authorization passthrough, and metadata-only activity logging are verified.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        configure(args.password_file)
    except (OSError, RuntimeError) as error:
        print(f"MCPHub setup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
