#!/usr/bin/env python3
"""Bank Norwegian Open Banking MCP server (read-only account information).

A dependency-free streamable-HTTP MCP server that talks to the Bank Norwegian
Berlin Group PSD2 API. It exposes account, balance, and transaction reads for
one PSU consent plus the OAuth consent lifecycle tools needed to establish and
inspect that consent. Payment initiation is intentionally not implemented; the
API is read-only by design and grants are scoped per LiteLLM access group.

Configuration is environment-only; secrets never appear in tool output. The
OAuth token and consent state persist in BN_STATE_FILE (0600, state volume).
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import http.server
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import formatdate

SERVER_NAME = "banknorwegian"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"
SCOPES = "offline_access readaccounts"
USER_AGENT = "homecompute-banknorwegian-mcp/1.0"

VALID_REGIONS = ("NO", "SE", "DK", "FI")


class BankError(Exception):
    """Upstream or configuration failure with a sanitized public message."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Config:
    def __init__(self) -> None:
        self.sandbox = env("BN_SANDBOX", "true").lower() in {"1", "true", "yes"}
        default_base = (
            "https://api-sandbox.banknorwegian.com"
            if self.sandbox
            else "https://api.banknorwegian.no"
        )
        self.base_url = env("BN_BASE_URL", default_base).rstrip("/")
        self.auth_base_url = env("BN_AUTH_BASE_URL", "https://identity.banknorwegian.no").rstrip("/")
        self.client_id = env("BN_CLIENT_ID")
        self.client_secret = env("BN_CLIENT_SECRET")
        self.redirect_uri = env("BN_REDIRECT_URI")
        self.region = env("BN_REGION", "NO").upper()
        if self.region not in VALID_REGIONS:
            raise RuntimeError(f"BN_REGION must be one of {VALID_REGIONS}")
        self.psu_id = env("BN_PSU_ID", "homecompute-owner")
        self.consent_days = int(env("BN_CONSENT_DAYS", "85"))
        self.frequency_per_day = int(env("BN_FREQUENCY_PER_DAY", "10"))
        self.state_file = env("BN_STATE_FILE", "/data/state.json")
        self.host = env("BANKNORWEGIAN_MCP_HOST", "0.0.0.0")
        self.port = int(env("BANKNORWEGIAN_MCP_PORT", "8090"))
        self.timeout = float(env("BN_HTTP_TIMEOUT", "20"))
        self.ca_file = env("BN_CA_FILE")  # Buypass PSD2 root CAs are not in distros.
        # Sandbox-only: skip QCSeal signature / QWAC certificate validation.
        self.skip_validation = env("BN_SKIP_VALIDATION", "true" if self.sandbox else "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.qceseal_key_file = env("BN_QCESEAL_KEY_FILE")
        self.qceseal_cert_file = env("BN_QCESEAL_CERT_FILE")
        self.qwac_cert_file = env("BN_QWAC_CERT_FILE")
        self.qwac_key_file = env("BN_QWAC_KEY_FILE")

    def sanity(self) -> str | None:
        if not self.client_id or not self.client_secret:
            return "BN_CLIENT_ID/BN_CLIENT_SECRET are not set. Register an app in the Bank Norwegian developer portal."
        return None

    def http_origin(self) -> str:
        return urllib.parse.urlparse(self.base_url).netloc


class State:
    """Persisted consent/token state with a lock and atomic 0600 writes."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"consent": None, "tokens": None, "pending": None}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.data.update({k: loaded.get(k) for k in ("consent", "tokens", "pending")})
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            sys.stderr.write("state: unreadable file, starting empty\n")

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def mutate(self, fn) -> None:
        with self.lock:
            fn(self.data)
            self.save()


class BankNorwegian:
    """Minimal Berlin Group PSD2 client for this deployment's single PSU."""

    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state

    # ---- transport ---------------------------------------------------------

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=self.config.ca_file or None)
        if self.config.qwac_cert_file and self.config.qwac_key_file:
            context.load_cert_chain(self.config.qwac_cert_file, self.config.qwac_key_file)
        return context

    def _request(self, method: str, url: str, *, headers=None, body: bytes | None = None,
                 form: dict | None = None, no_auth: bool = False):
        headers = dict(headers or {})
        if form is not None:
            body = urllib.parse.urlencode(form).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        headers.setdefault("Request-ID", str(uuid.uuid4()))
        headers.setdefault("Date", formatdate(usegmt=True))
        headers.setdefault("User-Agent", USER_AGENT)
        token = None if no_auth else self._access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.config.skip_validation:
            headers["Signature-Validation"] = "false"
            headers["Certificate-Validation"] = "false"
        if self.config.qceseal_key_file and body is not None:
            self._sign(method, url, headers, body)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout,
                                        context=self._ssl_context()) as response:
                return response.status, _read_json(response.read())
        except urllib.error.HTTPError as error:
            payload = _read_json(error.read() or b"")
            detail = _sanitize_error(payload, error.code)
            raise BankError(detail, status=error.code) from error
        except urllib.error.URLError as error:
            raise BankError(f"Upstream request failed: {error.reason}") from error

    def _sign(self, method: str, url: str, headers: dict, body: bytes) -> None:
        """STET HTTP-signature with an RSA-SHA256 QCSeal (production mode)."""
        parsed = urllib.parse.urlparse(url)
        digest = "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()
        headers["Digest"] = digest
        headers["Host"] = parsed.netloc
        signed_names = ["(request-target)", "date", "digest", "request-id", "host"]
        signed_names += [k.lower() for k in headers if k.lower().startswith(("psu-", "tpp-"))]
        lines = []
        for name in signed_names:
            if name == "(request-target)":
                value = f"{method.lower()} {parsed.path}" + (f"?{parsed.query}" if parsed.query else "")
            else:
                value = next((headers[k] for k in headers if k.lower() == name), None)
                if value is None:
                    continue
            lines.append(f"{name}: {value}" if name != "(request-target)"
                         else f"(request-target): {value}")
        signing_string = "\n".join(lines)
        key_id = self._qceseal_key_id()
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", self.config.qceseal_key_file],
            input=signing_string.encode(), capture_output=True, check=True,
        ).stdout
        header = (f'keyId="{key_id}",algorithm="rsa-sha256",'
                  f'headers="{" ".join(n for n in signed_names if n != "(request-target)") or signed_names[0]}",'
                  f'signature="{base64.b64encode(signature).decode()}"')
        headers["Signature"] = header

    def _qceseal_key_id(self) -> str:
        cached = getattr(self, "_key_id", None)
        if cached:
            return cached
        if not self.config.qceseal_cert_file:
            raise BankError("BN_QCESEAL_CERT_FILE is required for request signing.")
        out = subprocess.run(
            ["openssl", "x509", "-in", self.config.qceseal_cert_file,
             "-fingerprint", "-sha256", "-noout"],
            capture_output=True, check=True,
        ).stdout.decode()
        self._key_id = out.split("=", 1)[1].strip().replace(":", "").lower()
        return self._key_id

    # ---- tokens ------------------------------------------------------------

    def _tokens(self):
        return (self.state.data.get("tokens") or None)

    def _access_token(self):
        tokens = self._tokens()
        if not tokens:
            return None
        if tokens.get("expires_at", 0) - 60 > time.time():
            return tokens["access_token"]
        if tokens.get("refresh_token"):
            try:
                self._refresh()
                return (self.state.data.get("tokens") or {}).get("access_token")
            except BankError:
                return None
        return None

    def _refresh(self) -> None:
        tokens = self._tokens() or {}
        status, payload = self._request(
            "POST", f"{self.config.auth_base_url}/connect/token", no_auth=True,
            form={"grant_type": "refresh_token",
                  "client_id": self.config.client_id,
                  "client_secret": self.config.client_secret,
                  "refresh_token": tokens.get("refresh_token", "")},
            headers={"Origin": self.config.base_url},
        )
        if status != 200 or "access_token" not in payload:
            raise BankError("Token refresh was refused.", status=status)
        self._store_tokens(payload)

    def _store_tokens(self, payload: dict) -> None:
        tokens = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token")
            or (self._tokens() or {}).get("refresh_token"),
            "expires_at": time.time() + int(payload.get("expires_in", 300)),
            "scope": payload.get("scope", SCOPES),
        }
        self.state.mutate(lambda data: data.__setitem__("tokens", tokens))

    # ---- consent + SCA -------------------------------------------------------

    def create_consent(self) -> dict:
        config = self.config
        valid_until = time.strftime(
            "%Y-%m-%d", time.gmtime(time.time() + 86400 * max(1, min(config.consent_days, 89))))
        body = json.dumps({
            "ValidUntil": valid_until,
            "RecurringIndicator": True,
            "Access": {"Accounts": [], "Balances": [], "Transactions": [],
                       "allPsd2": "allAccounts"},
            "FrequencyPerDay": config.frequency_per_day,
            "CombinedServiceIndicator": False,
        }, separators=(",", ":")).encode()
        headers = {
            "PSU-ID": config.psu_id,
            "Region-ID": config.region,
            "TPP-Client-ID": config.client_id,
            "TPP-Redirect-Preferred": "true",
        }
        if config.redirect_uri:
            headers["TPP-Redirect-URI"] = config.redirect_uri
        status, payload = self._request("POST", f"{config.base_url}/openbanking/consents/",
                                        headers=headers, body=body, no_auth=True)
        consent_id = payload.get("consentId")
        auth_url = (payload.get("_links") or {}).get("scaRedirect", {}).get("href")
        if not consent_id or not auth_url:
            raise BankError("Consent initiation returned no consentId or scaRedirect.", status=status)
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if "code_challenge=" in auth_url:
            auth_url = re.sub(r"code_challenge=[^&]*", f"code_challenge={challenge}", auth_url)
        else:
            auth_url += f"&code_challenge={challenge}&code_challenge_method=S256"
        state_param = secrets.token_urlsafe(16)
        auth_url += f"&state={state_param}&scope={urllib.parse.quote(SCOPES)}"
        self.state.mutate(lambda data: data.__setitem__("pending", {
            "consent_id": consent_id, "verifier": verifier, "state": state_param,
            "created": time.time(),
        }))
        return {"consent_id": consent_id, "auth_url": auth_url, "state": state_param}

    def complete_auth(self, redirect_url: str) -> dict:
        pending = self.state.data.get("pending")
        if not pending:
            raise BankError("No consent initiation is pending; call banknorwegian_auth_start first.")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query
                                      if redirect_url.startswith(("http://", "https://"))
                                      else f"?{redirect_url}")
        if "code" not in query:
            error = query.get("error", ["no authorization code found in the returned URL"])
            raise BankError(f"Authorization callback did not include a code: {error[0]}")
        status, payload = self._request(
            "POST", f"{self.config.auth_base_url}/connect/token", no_auth=True,
            form={"grant_type": "authorization_code",
                  "client_id": self.config.client_id,
                  "client_secret": self.config.client_secret,
                  "code": query["code"][0],
                  "redirect_uri": self.config.redirect_uri,
                  "code_verifier": pending["verifier"],
                  "scope": SCOPES},
            headers={"Origin": self.config.base_url},
        )
        if status != 200 or "access_token" not in payload:
            raise BankError("Authorization code exchange was refused.", status=status)
        consent_id = pending["consent_id"]
        valid_until = time.strftime("%Y-%m-%d", time.gmtime(
            time.time() + 86400 * max(1, min(self.config.consent_days, 89))))

        def apply(data):
            data["consent"] = {"consent_id": consent_id, "valid_until": valid_until,
                               "region": self.config.region, "psu_id": self.config.psu_id}
            data["pending"] = None
        self.state.mutate(apply)
        self._store_tokens(payload)
        return {"consent_id": consent_id, "valid_until": valid_until}

    def auto_complete_from_callback(self, query: str) -> tuple[bool, str]:
        pending = self.state.data.get("pending")
        if not pending:
            return False, "no pending consent"
        parsed = urllib.parse.parse_qs(query)
        if "state" in parsed and parsed["state"][0] != pending.get("state"):
            return False, "state mismatch"
        if "code" not in parsed:
            return False, "no code"
        try:
            self.complete_auth(urllib.parse.unquote(query))
            return True, "authorized"
        except BankError as error:
            return False, str(error)

    # ---- AIS reads -----------------------------------------------------------

    def _ais_headers(self, consent_id: str) -> dict:
        return {"Consent-ID": consent_id, "PSU-ID": self.config.psu_id,
                "PSU-Device-ID": "homecompute", "PSU-User-Agent": USER_AGENT,
                "Region-ID": self.config.region}

    def _consent_id(self) -> str:
        consent = self.state.data.get("consent")
        if not consent or not self._tokens():
            raise BankError("Not authorized yet. Call banknorwegian_auth_start, complete the "
                            "Bank Norwegian login, then banknorwegian_auth_complete.")
        return consent["consent_id"]

    def accounts(self, with_balance: bool) -> dict:
        consent_id = self._consent_id()
        url = f"{self.config.base_url}/openbanking/accounts/?withBalance={'true' if with_balance else 'false'}"
        _, payload = self._request("GET", url, headers=self._ais_headers(consent_id))
        return payload

    def balances(self, account_reference: str) -> dict:
        consent_id = self._consent_id()
        url = (f"{self.config.base_url}/openbanking/accounts/"
               f"{urllib.parse.quote(account_reference)}/balances")
        _, payload = self._request("GET", url, headers=self._ais_headers(consent_id))
        return payload

    def transactions(self, account_reference: str, date_from: str, date_to: str,
                     booking_status: str, max_pages: int) -> dict:
        consent_id = self._consent_id()
        params = {"bookingStatus": booking_status}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        url = (f"{self.config.base_url}/openbanking/accounts/"
               f"{urllib.parse.quote(account_reference)}/transactions?"
               + urllib.parse.urlencode(params))
        pages, entries, links = 0, [], {}
        while url and pages < max_pages:
            _, payload = self._request("GET", url, headers=self._ais_headers(consent_id))
            batch = (payload.get("transactions") or {})
            entries += batch.get("booked") or []
            if booking_status == "pending":
                entries += batch.get("pending") or []
            links = payload.get("_links") or {}
            url = links.get("next", {}).get("href") if links.get("next") else None
            if url and url.startswith("/") and "://" not in url:
                url = self.config.base_url + url
            pages += 1
        return {"accountReference": account_reference, "pagesFetched": pages,
                "truncated": bool(url), "transactions": entries}

    def revoke_consent(self) -> dict:
        consent = self.state.data.get("consent")
        if not consent:
            return {"revoked": False, "detail": "no stored consent"}
        try:
            self._request("DELETE", f"{self.config.base_url}/openbanking/consents/{consent['consent_id']}",
                          headers=self._ais_headers(consent["consent_id"]))
        finally:
            def clear(data):
                data["consent"] = None
                data["tokens"] = None
                data["pending"] = None
            self.state.mutate(clear)
        return {"revoked": True, "consent_id": consent["consent_id"]}

    def status(self) -> dict:
        tokens = self._tokens() or {}
        consent = self.state.data.get("consent") or None
        authorized = bool(tokens.get("access_token"))
        return {
            "sandbox": self.config.sandbox,
            "base_url": self.config.base_url,
            "region": self.config.region,
            "authorized": authorized,
            "access_token_expires_at": tokens.get("expires_at"),
            "consent": consent,
            "pending_auth": bool(self.state.data.get("pending")),
            "configuration_error": self.config.sanity(),
        }


def _read_json(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode("utf-8", "replace")[:2000]
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _sanitize_error(payload, status) -> str:
    text = json.dumps(payload, default=str) if isinstance(payload, (dict, list)) else str(payload)
    text = re.sub(r"(refresh_token|access_token|client_secret)\"\s*:\s*\"[^\"]*\"", r"\1=***", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***", text)
    return f"Bank Norwegian API error (HTTP {status}): {text[:800]}"


# ---------------------------------------------------------------------------
# MCP streamable-HTTP surface
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {
        "name": "banknorwegian_status",
        "description": "Show Bank Norwegian authorization and consent state (no secrets). Start here.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "banknorwegian_auth_start",
        "description": "Create a 90-day AIS consent and return the Bank Norwegian login/consent URL. "
                       "The PSU opens the URL, signs in with BankID, and is redirected to the "
                       "redirect_uri; then call banknorwegian_auth_complete with the final URL.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "banknorwegian_auth_complete",
        "description": "Exchange the authorization code from the post-login redirect URL for tokens. "
                       "Pass the full redirected URL (its query may look like code=...&state=...).",
        "inputSchema": {"type": "object",
                        "properties": {"redirect_url": {"type": "string",
                                                       "description": "Full URL or query string after login"}},
                        "required": ["redirect_url"]},
    },
    {
        "name": "banknorwegian_accounts",
        "description": "List the PSU's Bank Norwegian savings and credit-card accounts.",
        "inputSchema": {"type": "object",
                        "properties": {"with_balance": {"type": "boolean",
                                                       "description": "Include current balances inline"}},
                        "required": []},
    },
    {
        "name": "banknorwegian_balances",
        "description": "Balances for one account (accounts list gives account references).",
        "inputSchema": {"type": "object",
                        "properties": {"account_reference": {"type": "string"}},
                        "required": ["account_reference"]},
    },
    {
        "name": "banknorwegian_transactions",
        "description": "Transactions for one account. Recent consents return full history; later "
                       "calls cover the last 90 days. Use dateFrom/dateTo (YYYY-MM-DD).",
        "inputSchema": {"type": "object",
                        "properties": {"account_reference": {"type": "string"},
                                       "date_from": {"type": "string"},
                                       "date_to": {"type": "string"},
                                       "booking_status": {"type": "string", "enum": ["booked", "pending"],
                                                         "description": "Default booked"}},
                        "required": ["account_reference"]},
    },
    {
        "name": "banknorwegian_consent_revoke",
        "description": "Revoke the stored consent and delete local tokens. Ends access.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = USER_AGENT
    bank: BankNorwegian = None  # type: ignore[assignment]
    sessions: set = set()
    sessions_lock = threading.Lock()

    def log_message(self, format, *args):  # noqa: A002 - no URLs with codes into logs
        sys.stderr.write("mcp: %s\n" % (format % args))

    # ---- endpoints ----------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            ok = self.bank.config.sanity() is None or self.bank._tokens()
            return self._send(200 if ok else 503, {"status": "ok" if ok else "unconfigured"})
        if path == "/callback":
            query = urllib.parse.urlparse(self.path).query
            ok, detail = self.bank.auto_complete_from_callback(query)
            body = (b"Bank Norwegian authorization complete. You can close this tab."
                    if ok else b"Bank Norwegian authorization did not complete: see server log.")
            if not ok:
                sys.stderr.write(f"callback: failed: {detail}\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        session = self.headers.get("Mcp-Session-Id")
        with Handler.sessions_lock:
            Handler.sessions.discard(session)
        self._send(204, None)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/mcp":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"jsonrpc": "2.0", "error": {"code": -32700,
                                                                "message": "parse error"}, "id": None})
        if isinstance(message, list):
            return self._send(400, {"jsonrpc": "2.0", "error": {"code": -32600,
                                                                "message": "batching not supported"}, "id": None})
        method = message.get("method")
        request_id = message.get("id")
        session = self.headers.get("Mcp-Session-Id")
        if method == "initialize":
            session = uuid.uuid4().hex
            with Handler.sessions_lock:
                Handler.sessions.add(session)
            result = {"protocolVersion": PROTOCOL_VERSION,
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result},
                              session=session)
        if str(method).startswith("notifications/"):
            return self._send(202, None)
        with Handler.sessions_lock:
            known = not Handler.sessions or (session in Handler.sessions)
        if not known:
            return self._send(404, {"jsonrpc": "2.0", "id": request_id,
                                    "error": {"code": -32001, "message": "unknown session"}})
        if method == "ping":
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": {}}, session=session)
        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": request_id,
                                   "result": {"tools": TOOL_DEFS}}, session=session)
        if method == "tools/call":
            result = self._call_tool(message.get("params") or {})
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result},
                              session=session)
        return self._send(200, {"jsonrpc": "2.0", "id": request_id,
                                "error": {"code": -32601, "message": "method not found"}},
                          session=session)

    # ---- tool dispatch --------------------------------------------------------

    def _call_tool(self, params: dict) -> dict:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            payload = self._dispatch(name, arguments)
            text = json.dumps(payload, indent=2, default=str)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except BankError as error:
            return {"content": [{"type": "text", "text": f"error: {error}"}], "isError": True}
        except (TypeError, KeyError, ValueError) as error:
            return {"content": [{"type": "text", "text": f"invalid request: {error}"}],
                    "isError": True}

    def _dispatch(self, name: str, args: dict):
        bank = self.bank
        if name == "banknorwegian_status":
            return bank.status()
        if name == "banknorwegian_auth_start":
            problem = bank.config.sanity()
            if problem:
                raise BankError(problem)
            return bank.create_consent()
        if name == "banknorwegian_auth_complete":
            return bank.complete_auth(str(args.get("redirect_url") or ""))
        if name == "banknorwegian_accounts":
            return bank.accounts(bool(args.get("with_balance", True)))
        if name == "banknorwegian_balances":
            return bank.balances(str(args["account_reference"]))
        if name == "banknorwegian_transactions":
            return bank.transactions(
                str(args["account_reference"]),
                str(args.get("date_from") or ""), str(args.get("date_to") or ""),
                str(args.get("booking_status") or "booked"),
                int(os.environ.get("BN_MAX_PAGES", "10")))
        if name == "banknorwegian_consent_revoke":
            return bank.revoke_consent()
        raise BankError(f"unknown tool: {name}")

    def _send(self, status: int, payload, session: str | None = None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.end_headers()
        if body:
            self.wfile.write(body)


def main() -> int:
    config = Config()
    state = State(config.state_file)
    Handler.bank = BankNorwegian(config, state)
    server = http.server.ThreadingHTTPServer((config.host, config.port), Handler)
    sys.stderr.write(f"banknorwegian-mcp listening on {config.host}:{config.port} "
                     f"({'sandbox' if config.sandbox else 'production'}, region {config.region})\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
