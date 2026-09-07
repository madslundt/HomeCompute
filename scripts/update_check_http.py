"""Restricted standard-library HTTP adapter for the update monitor."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
REQUEST_HOSTS = {"huggingface.co", "api.github.com"}
PROJECT_HOSTS = {"huggingface.co", "github.com"}


class ValidationError(ValueError):
    """An input is invalid and must fail closed."""


class SourceError(RuntimeError):
    """A reportable failure for one upstream source."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolved_addresses(host: str) -> list[str]:
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise SourceError("dns_failed", "allow-listed upstream hostname did not resolve") from error
    return sorted({record[4][0] for record in records})


def validate_request_url(url: str, kind: str, resolver: Callable[[str], list[str]]) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ValidationError("request_url is malformed") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in REQUEST_HOSTS
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValidationError("request_url must use HTTPS on an allow-listed host and default port")
    decoded_path = urllib.parse.unquote(parsed.path)
    if ".." in decoded_path.split("/") or "\x00" in decoded_path or "//" in decoded_path:
        raise ValidationError("request_url path is unsafe")
    if kind == "huggingface_model":
        if parsed.hostname != "huggingface.co" or not re.fullmatch(r"/api/models/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", decoded_path) or query:
            raise ValidationError("huggingface_model request_url has an unsupported endpoint")
    elif kind == "huggingface_search":
        allowed = {"author", "search", "sort", "direction", "limit", "full"}
        if parsed.hostname != "huggingface.co" or decoded_path != "/api/models" or not query or set(query) - allowed:
            raise ValidationError("huggingface_search request_url has an unsupported endpoint")
        if any(len(values) != 1 for values in query.values()):
            raise ValidationError("huggingface_search query fields must be unique")
        try:
            limit = int(query.get("limit", ["100"])[0])
        except ValueError as error:
            raise ValidationError("huggingface_search limit must be an integer") from error
        if limit < 1 or limit > 100:
            raise ValidationError("huggingface_search limit must be between 1 and 100")
    elif kind == "github_release":
        if parsed.hostname != "api.github.com" or not re.fullmatch(r"/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/releases/latest", decoded_path) or query:
            raise ValidationError("github_release request_url has an unsupported endpoint")
    elif kind == "github_file":
        allowed = {"path", "sha", "per_page"}
        if parsed.hostname != "api.github.com" or not re.fullmatch(r"/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/commits", decoded_path) or set(query) - allowed:
            raise ValidationError("github_file request_url has an unsupported endpoint")
        if any(len(values) != 1 for values in query.values()) or query.get("per_page") != ["1"]:
            raise ValidationError("github_file request_url must request exactly one commit")
    else:
        raise ValidationError(f"unsupported source kind: {kind}")
    addresses = resolver(parsed.hostname)
    if not addresses:
        raise SourceError("dns_failed", "allow-listed upstream hostname did not resolve")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as error:
            raise SourceError("dns_failed", "allow-listed hostname returned an invalid address") from error
        if not ip.is_global:
            raise ValidationError("request_url resolved to a private, link-local, or non-public address")
    return urllib.parse.urlunsplit(parsed)


def validate_project_url(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValidationError("project_url is malformed") from error
    if parsed.scheme != "https" or parsed.hostname not in PROJECT_HOSTS or port not in (None, 443) or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValidationError("project_url must use HTTPS on an allow-listed project host")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, kind: str, resolver: Callable[[str], list[str]]) -> None:
        self.kind = kind
        self.resolver = resolver

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        safe_url = validate_request_url(newurl, self.kind, self.resolver)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def fetch_json(url: str, kind: str, resolver: Callable[[str], list[str]]) -> Any:
    safe_url = validate_request_url(url, kind, resolver)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SafeRedirectHandler(kind, resolver),
    )
    request = urllib.request.Request(safe_url, headers={"Accept": "application/json", "User-Agent": "HomeCompute-update-check/1"})
    try:
        with opener.open(request, timeout=20) as response:
            validate_request_url(response.geturl(), kind, resolver)
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_DOCUMENT_BYTES:
                raise SourceError("response_too_large", "upstream response exceeded the metadata limit")
            body = response.read(MAX_DOCUMENT_BYTES + 1)
            if len(body) > MAX_DOCUMENT_BYTES:
                raise SourceError("response_too_large", "upstream response exceeded the metadata limit")
    except urllib.error.HTTPError as error:
        raise SourceError("http_error", f"upstream returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise SourceError("network_error", f"upstream request failed ({type(error.reason).__name__})") from error
    except (OSError, TimeoutError) as error:
        raise SourceError("network_error", f"upstream request failed ({type(error).__name__})") from error
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SourceError("invalid_json", "upstream did not return valid JSON metadata") from error
