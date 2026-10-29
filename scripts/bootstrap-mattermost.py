#!/usr/bin/env python3
"""Create the private HomeCompute workspace; credentials stay in root-only files."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import re
import stat
import subprocess
import tempfile
from typing import Any, Iterator
import urllib.error
import urllib.request

ORIGIN = "http://127.0.0.1:18065"
SITE = "https://mattermost.home.arpa"
TRANSPORT_SITE = "https://home-core.tail479ad.ts.net:9443"
PRIVATE = Path("/srv/state/mattermost/private")
CONTAINER = "homecompute-mattermost-mattermost-1"
CHANNEL_ALIASES = {'calendar': 'family', 'tasks': 'family', 'correspondence': 'family',
                   'personal': 'family', 'aula': 'family', 'storage': 'shopping', 'meals': 'shopping',
                   'energy': 'home', 'household-admin': 'finances', 'news': 'research'}
CHANNELS = {
    "inbox": ("Inbox", "Start a task here. Continue its discussion in the reply thread."),
    "personal": ("Family", "Family planning, school, calendars, tasks and reminders with Atlas and Luna. Private DMs remain separate."),
    "research": ("Research & news", "Questions, comparisons, and findings with sources."),
    "engineering": ("Engineering", "Coding requests and project work. One thread per task."),
    "home-operations": ("Infrastructure", "HomeCompute servers, services, incidents, and maintenance. Household devices belong in Home; energy plans belong in Home."),
    "openclaw": ("OpenClaw", "Manage Atlas and OpenClaw setup, routing, skills, and specialist requests for new projects."),
}
AGENT_ACCOUNTS = {
    "default": ("main", "atlas", "inbox"),
    "personal": ("personal", "luna", "family"),
    "researcher": ("researcher", "nova", "research"),
    "engineer": ("engineer", "apollo", "engineering"),
    "operator": ("operator", "orion", "home-operations"),
    "shopper": ("shopper", "vega", "shopping"),
    "keeper": ("keeper", "ceres", "shopping"),
    "travel": ("travel", "lyra", "travel"),
    "cook": ("cook", "hestia", "shopping"),
    "administrator": ("administrator", "themis", "finances"),
    "finance": ("finance", "plutus", "finances"),
    "family": ("family", "gaia", "family"),
}
AGENT_DISPLAY_NAMES = dict(zip(AGENT_ACCOUNTS, ["Atlas · Coordinator", "Luna · Personal", "Nova · Research",
    "Apollo · Engineering", "Orion · Home operations", "Vega · Shopping & discounts", "Ceres · Storage",
    "Lyra · Travel & adventures", "Hestia · Meals", "Themis · Household admin", "Plutus · Finances", "Gaia · Family"]))


class BootstrapError(Exception):
    """Bounded diagnostics; no HTTP bodies or credentials are printed."""


def save_private(path: Path, text: str) -> None:
    if path.is_symlink():
        raise BootstrapError("refusing symlink state")
    fd, name = tempfile.mkstemp(prefix=".mattermost-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(path: Path, value: Any) -> None:
    save_private(path, json.dumps(value, indent=2) + "\n")


class API:
    def __init__(self, token: str = ""):
        self.token = token

    def request(self, method: str, path: str, body: Any = None,
                *, missing: bool = False) -> tuple[Any, dict[str, str]]:
        if not path.startswith("/") or ".." in path or "\n" in path:
            raise BootstrapError("invalid API path")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(ORIGIN + "/api/v4" + path, data, headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=25) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise BootstrapError("API response budget exceeded")
                return json.loads(raw) if raw else None, dict(response.headers)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if missing and code == 404:
                return None, {}
            raise BootstrapError(f"API {method} failed with HTTP {code}; inspect privately") from None
        except (OSError, ValueError):
            raise BootstrapError("API outcome unavailable; inspect before retrying mutation") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise BootstrapError("API redirect refused")


def identity(value: Any, label: str) -> str:
    selected = value.get("id", "") if isinstance(value, dict) else ""
    if not isinstance(selected, str) or not re.fullmatch(r"[a-z0-9]{26}", selected):
        raise BootstrapError("invalid " + label + " identity")
    return selected


@contextmanager
def bootstrap_lock(directory: Path) -> Iterator[None]:
    if not directory.is_absolute() or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise BootstrapError("private state path must be absolute without symlinks")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise BootstrapError("private state must be owned by the caller with mode 0700")
    fd = os.open(directory / ".bootstrap.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise BootstrapError("private lock ownership or mode differs")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BootstrapError("another bootstrap is in progress") from None
        yield
    finally:
        os.close(fd)


def bootstrap(directory: Path = PRIVATE) -> dict[str, Any]:
    with bootstrap_lock(directory):
        return bootstrap_locked(directory)


def bootstrap_locked(directory: Path) -> dict[str, Any]:
    path = directory / "credentials.json"
    fresh = not path.exists()
    if fresh:
        credentials = {"site_url": SITE, "username": "mads", "email": "mads@home.arpa",
                       "password": secrets.token_urlsafe(24)}
        save(path, credentials)
    else:
        if (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()
                or stat.S_IMODE(path.stat().st_mode) != 0o600):
            raise BootstrapError("private credential permissions required")
        credentials = json.loads(path.read_text())
        if (not isinstance(credentials, dict) or credentials.get("site_url") not in {SITE, TRANSPORT_SITE}
                or credentials.get("username") != "mads" or credentials.get("email") != "mads@home.arpa"
                or not isinstance(credentials.get("password"), str) or len(credentials["password"]) < 24):
            raise BootstrapError("existing identity differs; refusing replacement")
    # Local admin socket is reachable only inside this dedicated app container.
    result = subprocess.run(["docker", "exec", "--user", "2000:2000", CONTAINER,
                             "/mattermost/bin/mmctl", "--local", "--json", "user", "list", "--all"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise BootstrapError("local admin lookup failed")
    listed = json.loads(result.stdout)
    if listed is None:
        listed = []  # mmctl prints its nil result slice as JSON null.
    if not isinstance(listed, list) or any(not isinstance(x, dict) for x in listed):
        raise BootstrapError("invalid local admin lookup response")
    found = [x for x in listed if x.get("username") == "mads"]
    if len(found) > 1:
        raise BootstrapError("ambiguous local operator identity")
    if not found:
        result = subprocess.run(["docker", "exec", "--user", "2000:2000", CONTAINER,
                                 "/mattermost/bin/mmctl", "--local", "user", "create",
                                 "--username", "mads", "--email", credentials["email"],
                                 "--password", credentials["password"], "--system-admin"],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise BootstrapError("local user creation failed; inspect before retry")
    elif fresh:
        raise BootstrapError("an existing mads account requires explicit reconciliation")
    api = API()
    user, headers = api.request("POST", "/users/login", {"login_id": "mads", "password": credentials["password"]})
    owner = identity(user, "operator")
    if (user.get("username") != "mads" or user.get("email") != credentials["email"]
            or user.get("is_bot") is True or user.get("delete_at", 0) != 0
            or found and identity(found[0], "operator") != owner
            or credentials.get("operator_id", owner) != owner):
        raise BootstrapError("operator identity differs from retained account")
    token = next((v for k, v in headers.items() if k.lower() == "token"), "")
    if not token or "system_admin" not in user.get("roles", "").split():
        raise BootstrapError("operator login did not prove admin identity")
    api.token = token
    credentials["operator_id"] = owner
    credentials["site_url"] = SITE
    # This session is used only for operator-side acceptance probes.
    credentials["operator_session"] = token
    save(path, credentials)
    team, _ = api.request("GET", "/teams/name/homecompute", missing=True)
    if team is None:
        team, _ = api.request("POST", "/teams", {"name": "homecompute", "display_name": "HomeCompute", "type": "I"})
    team_id = identity(team, "team")
    members, _ = api.request("GET", f"/teams/{team_id}/members/{owner}", missing=True)
    if members is None:
        api.request("POST", f"/teams/{team_id}/members", {"team_id": team_id, "user_id": owner})
    channels = {}
    household_access = directory / 'household-access.json'
    household_admitted = household_access.exists() or household_access.is_symlink()
    for name, (display, purpose) in CHANNELS.items():
        channel, _ = api.request("GET", f"/teams/{team_id}/channels/name/{CHANNEL_ALIASES.get(name, name)}", missing=True)
        created = channel is None
        if channel is None:
            channel, _ = api.request("POST", "/channels", {"team_id": team_id, "name": CHANNEL_ALIASES.get(name, name),
                                                           "display_name": display, "type": "P", "header": purpose})
        if (channel.get("team_id") != team_id or channel.get("delete_at", 0) != 0
                or (created or household_admitted or name == 'openclaw') and channel.get("type") != "P"):
            raise BootstrapError("managed owner channel privacy or identity differs")
        channel_id = identity(channel, "channel")
        channels[name] = channel_id
        member, _ = api.request("GET", f"/channels/{channel_id}/members/{owner}", missing=True)
        if member is None:
            api.request("POST", f"/channels/{channel_id}/members", {"user_id": owner})
    bot_path = "/users/" + credentials["bot_id"] if credentials.get("bot_id") else "/users/username/" + AGENT_ACCOUNTS["default"][1]
    bot_user, _ = api.request("GET", bot_path, missing=not credentials.get("bot_id"))
    if bot_user is None:
        bot, _ = api.request("POST", "/bots", {"username": AGENT_ACCOUNTS["default"][1], "display_name": AGENT_DISPLAY_NAMES["default"],
                                               "description": "Coordinator and focused OpenClaw specialists."})
        bot_id = identity({"id": bot.get("user_id")}, "bot")
    else:
        if not bot_user.get("is_bot"):
            raise BootstrapError("coordinator username is occupied by a human account")
        bot_id = identity(bot_user, "bot")
        bot, _ = api.request("GET", f"/bots/{bot_id}")
    if not isinstance(bot, dict) or bot.get("owner_id") != owner or bot.get("user_id") != bot_id:
        raise BootstrapError("OpenClaw bot ownership differs from the operator")
    member, _ = api.request("GET", f"/teams/{team_id}/members/{bot_id}", missing=True)
    if member is None:
        api.request("POST", f"/teams/{team_id}/members", {"team_id": team_id, "user_id": bot_id})
    for name, channel_id in channels.items():
        if (directory / "agent-accounts.json").exists() and name != "inbox":
            continue
        member, _ = api.request("GET", f"/channels/{channel_id}/members/{bot_id}", missing=True)
        if member is None:
            api.request("POST", f"/channels/{channel_id}/members", {"user_id": bot_id})
    pending = directory / "bot-token-pending.json"
    if "bot_token" not in credentials:
        if pending.exists():
            raise BootstrapError("prior token creation needs reconciliation; refusing duplicate")
        save(pending, {"bot_id": bot_id})
        created, _ = api.request("POST", f"/users/{bot_id}/tokens", {"description": "HomeCompute OpenClaw channel"})
        token_id = identity(created, "bot token")
        bot_token = created.get("token")
        if (not isinstance(bot_token, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{20,200}", bot_token)
                or created.get("user_id") != bot_id):
            raise BootstrapError("bot token receipt is invalid; reconcile pending creation")
        credentials.update(bot_token=bot_token, bot_token_id=token_id, bot_id=bot_id)
        save(path, credentials)
        pending.unlink()
    elif credentials.get("bot_id") != bot_id:
        raise BootstrapError("retained bot token identity mismatch")
    save(directory / "channels.json", channels)
    save(directory / "operator-login.json", {k: credentials[k] for k in ("site_url", "username", "password")})
    save(directory / "openclaw-channel.json", {"owner_id": owner, "bot_id": bot_id,
                                               "base_url": TRANSPORT_SITE, "channels": channels})
    save_private(directory / "openclaw-bot-token", credentials["bot_token"] + "\n")
    return {"configured": True, "site_url": SITE, "username": "mads", "team_id": team_id,
            "channels": channels, "operator_login_file": str(directory / "operator-login.json")}


def read_private(path: Path) -> Any:
    if (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()
            or stat.S_IMODE(path.stat().st_mode) != 0o600):
        raise BootstrapError("private account file ownership or mode differs")
    return json.loads(path.read_text())


def provision_accounts(directory: Path = PRIVATE, *, activate: bool = False) -> dict[str, Any]:
    """Keep identity reconciliation separate from initial server bootstrap."""
    import importlib.util
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('mattermost_agent_accounts', Path(__file__).with_name('mattermost_agent_accounts.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.provision(SimpleNamespace(**globals()), directory, activate=activate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provision-roles', action='store_true')
    parser.add_argument('--activate-roles', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root on home-core.")
    os.umask(0o077)
    try:
        result = provision_accounts(activate=args.activate_roles) if args.provision_roles or args.activate_roles else bootstrap()
        print(json.dumps(result, indent=2))
    except (BootstrapError, subprocess.SubprocessError, KeyError, ValueError, TypeError, OSError) as error:
        raise SystemExit(str(error) if isinstance(error, BootstrapError) else "Bootstrap failed; inspect private state.") from None


if __name__ == "__main__":
    main()
