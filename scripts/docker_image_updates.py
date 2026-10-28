#!/usr/bin/env python3
"""Read registry manifests only; never pull blobs, authenticate as an owner, or deploy."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
REGISTRIES = {"docker.io": ("registry-1.docker.io", "auth.docker.io", "registry.docker.io"),
              "ghcr.io": ("ghcr.io", "ghcr.io", "ghcr.io"),
              # n8n's official publisher mirror: https://hub.docker.com/r/n8nio/n8n/
              # Verify the deployed pin by digest before trusting mirror candidates.
              "docker.n8n.io": ("registry-1.docker.io", "auth.docker.io", "registry.docker.io")}
MEDIA = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
         "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}


def reference(value: str) -> dict[str, str | None]:
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() for c in value):
        raise ValueError("invalid image reference")
    image, separator, digest = value.partition("@")
    if separator and not DIGEST.fullmatch(digest):
        raise ValueError("invalid image digest")
    if image.startswith("sha256:") or re.fullmatch(r"[a-f0-9]{12,64}", image):
        raise ValueError("local image identifier")
    prefix = image.split("/", 1)[0]
    if "/" in image and ("." in prefix or ":" in prefix or prefix == "localhost"):
        registry, _, image = image.partition("/")
        registry = {"index.docker.io": "docker.io", "registry-1.docker.io": "docker.io"}.get(registry, registry)
    else:
        registry = "docker.io"
    if registry not in REGISTRIES:
        raise ValueError("registry outside public allowlist")
    repository, has_tag, tag = image.rpartition(":")
    if not has_tag:
        repository, tag = image, None
    if registry == "docker.io" and "/" not in repository:
        repository = "library/" + repository
    if not NAME.fullmatch(repository) or len(repository) > 255 or tag is not None and not TAG.fullmatch(tag):
        raise ValueError("invalid repository or tag")
    return {"registry": registry, "repository": repository, "tag": tag, "digest": digest if separator else None}


class RegistryError(ValueError):
    pass


class PublicHTTPS(http.client.HTTPSConnection):
    """Resolve once, deny all special-use addresses, then pin the TLS socket IP."""
    def connect(self) -> None:
        answers = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or len(addresses) > 16 or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise RegistryError("non-public registry destination")
        for address in sorted(addresses, key=lambda ip: ":" in ip)[:2]:
            try:
                self.sock = socket.create_connection((address, 443), self.timeout)
                self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
                return
            except OSError:
                if self.sock:
                    self.sock.close()
                self.sock = None
        raise RegistryError("registry connection failed")


class Registry:
    def __init__(self, transport: Callable[..., Any] | None = None, seconds: int = 120):
        self.transport = transport or self.http
        self.deadline = time.monotonic() + seconds
        self.calls = 0
        self.tokens: dict[tuple[str, str], str] = {}
        self.cache: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}

    def http(self, host: str, path: str, headers: dict[str, str], maximum: int) -> tuple[int, dict[str, str], bytes]:
        # Neither proxy environment nor Docker's credential/config files are read.
        connection = PublicHTTPS(host, timeout=min(8, max(0.1, self.deadline - time.monotonic())))
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            chunks, size = [], 0
            while size <= maximum:
                if time.monotonic() >= self.deadline:
                    raise RegistryError("registry collection budget exhausted")
                chunk = response.read1(min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk); size += len(chunk)
            if size > maximum:
                raise RegistryError("registry metadata exceeds budget")
            body = b"".join(chunks)
            return response.status, {key.lower(): value for key, value in response.getheaders()}, body
        finally:
            connection.close()

    def request(self, host: str, path: str, headers: dict[str, str], maximum: int) -> tuple[int, dict[str, str], bytes]:
        if host not in {host for values in REGISTRIES.values() for host in values}:
            raise RegistryError("unapproved registry endpoint")
        if self.calls >= 256 or time.monotonic() >= self.deadline:
            raise RegistryError("registry collection budget exhausted")
        self.calls += 1
        status, fields, body = self.transport(host, path, headers, maximum)
        if len(body) > maximum or 300 <= status < 400:
            raise RegistryError("oversized or redirected registry response")
        return status, fields, body

    def manifest(self, registry: str, repository: str, target: str) -> tuple[str, dict[str, Any]]:
        if registry not in REGISTRIES or not NAME.fullmatch(repository) or not (TAG.fullmatch(target) or DIGEST.fullmatch(target)):
            raise RegistryError("invalid manifest query")
        key = (registry, repository, target)
        if key in self.cache:
            return self.cache[key]
        host, auth_host, service = REGISTRIES[registry]
        token_key = (registry, repository)
        headers = {"Accept": ", ".join(sorted(MEDIA))}
        if token_key in self.tokens:
            headers["Authorization"] = "Bearer " + self.tokens[token_key]
        path = "/v2/" + repository + "/manifests/" + target
        status, fields, body = self.request(host, path, headers, 1048576)
        if status == 401 and token_key not in self.tokens:
            challenge = fields.get("www-authenticate", "")
            pairs = dict(re.findall(r'([a-z]+)="([^"\r\n]*)"', challenge))
            realm = "https://" + auth_host + "/token"
            scope = "repository:" + repository + ":pull"
            if (not challenge.startswith("Bearer ") or pairs.get("realm") != realm
                    or pairs.get("service") != service or pairs.get("scope", scope) != scope):
                raise RegistryError("unapproved registry authentication challenge")
            status, _, encoded = self.request(auth_host, "/token?" + urlencode({"service": service, "scope": scope}), {}, 65536)
            if status != 200:
                raise RegistryError("public registry authentication unavailable")
            value = json.loads(encoded)
            token = value.get("token", value.get("access_token")) if isinstance(value, dict) else None
            if not isinstance(token, str) or not 1 <= len(token) <= 16384 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise RegistryError("invalid anonymous registry token")
            self.tokens[token_key] = token
            status, fields, body = self.request(host, path, {**headers, "Authorization": "Bearer " + token}, 1048576)
        if status != 200:
            raise RegistryError("registry manifest unavailable")
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if fields.get("docker-content-digest", digest) != digest or DIGEST.fullmatch(target) and target != digest:
            raise RegistryError("registry manifest digest mismatch")
        value = json.loads(body)
        if not isinstance(value, dict) or value.get("schemaVersion") != 2 or value.get("mediaType") not in MEDIA:
            raise RegistryError("unsupported image manifest")
        self.cache[key] = digest, value
        return digest, value

    def platform(self, ref: dict[str, Any], target: str, os_name: str, architecture: str, variant: str = "") -> dict[str, str]:
        top_digest, manifest = self.manifest(ref["registry"], ref["repository"], target)
        platform_digest = top_digest
        if "manifests" in manifest:
            descriptors = manifest["manifests"]
            if not isinstance(descriptors, list) or len(descriptors) > 256:
                raise RegistryError("unbounded platform index")
            selected = [item for item in descriptors if isinstance(item, dict) and isinstance(item.get("platform"), dict)
                        and item["platform"].get("os") == os_name and item["platform"].get("architecture") == architecture
                        and (not variant or item["platform"].get("variant", "") == variant)]
            if len(selected) != 1 or not DIGEST.fullmatch(str(selected[0].get("digest", ""))):
                raise RegistryError("platform absent or ambiguous")
            platform_digest, manifest = self.manifest(ref["registry"], ref["repository"], selected[0]["digest"])
        config = manifest.get("config")
        if not isinstance(config, dict) or not DIGEST.fullmatch(str(config.get("digest", ""))) or "manifests" in manifest:
            raise RegistryError("invalid platform image manifest")
        return {"index_digest": top_digest, "platform_digest": platform_digest, "config_digest": config["digest"]}


def check_image(host: str, row: dict[str, Any], registry: Registry, tracked_tag: str | None = None,
                source_lane: str = "registry") -> dict[str, Any]:
    if not isinstance(row, dict) or not CONTAINER.fullmatch(str(row.get("container", ""))):
        raise ValueError("invalid deployed container metadata")
    image = row.get("image")
    if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9_./:@-]{1,512}", image):
        image = "invalid-reference"
    output = {"host": host, "container": row["container"], "image": image, "state": "unknown",
              "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "reason": "metadata_unavailable"}
    if source_lane not in {"registry", "source_pin"} or tracked_tag is not None and not TAG.fullmatch(tracked_tag):
        raise ValueError("invalid reviewed image tracking policy")
    try:
        ref = reference(image)
    except ValueError:
        output.update(state="untracked", reason="local_image_or_unsupported_registry")
        return output
    output.update(registry=ref["registry"], repository=ref["repository"], configured_tag=ref["tag"],
                  configured_digest=ref["digest"], tracked_tag=tracked_tag,
                  tracking_scope="selected_tag_only; digest differences require version and compatibility review")
    if row.get("repo_digests") is None:
        output.update(reason="deployed_image_inspection_unavailable")
        return output
    if source_lane == "source_pin" or not row.get("repo_digests"):
        output.update(state="untracked", reason="source_pin_lane_required")
        return output
    target = tracked_tag or (ref["tag"] if ref["digest"] else ref["tag"] or "latest")
    if target is None:
        output.update(state="untracked", reason="digest_pin_without_tracked_tag")
        return output
    output["tracked_tag"] = target
    os_name, architecture, variant = row.get("os"), row.get("architecture"), row.get("variant", "")
    if (os_name not in {"linux", "windows"} or architecture not in {"amd64", "arm64", "arm", "386", "ppc64le", "s390x", "riscv64"}
            or not isinstance(variant, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{0,32}", variant)):
        return output
    output["platform"] = "/".join(part for part in (os_name, architecture, variant) if part)
    try:
        current = registry.platform(ref, target, os_name, architecture, variant)
        local_digest = ref["digest"]
        repo_digests = row["repo_digests"]
        if not isinstance(repo_digests, list) or len(repo_digests) > 64:
            raise RegistryError("invalid local image provenance")
        known = set()
        for item in repo_digests:
            parsed = reference(item)
            if (parsed["registry"], parsed["repository"]) == (ref["registry"], ref["repository"]):
                known.add(parsed["digest"])
        if local_digest and local_digest not in known:
            raise RegistryError("configured pin absent from deployed image provenance")
        if not local_digest:
            if current["platform_digest"] in known or current["index_digest"] in known:
                local_digest = current["platform_digest"]
            elif len(known) == 1:
                local_digest = next(iter(known))
            else:
                raise RegistryError("local digest absent or ambiguous")
        local = registry.platform(ref, local_digest, os_name, architecture, variant)
        # Docker legacy engines identify configs; containerd-backed engines may identify manifests.
        if row.get("image_id") not in {local["config_digest"], local["platform_digest"], local["index_digest"]}:
            raise RegistryError("deployed image identity differs from registry provenance")
        output.update(local_manifest_digest=local["platform_digest"], remote_index_digest=current["index_digest"],
                      remote_platform_digest=current["platform_digest"], manifest_source="https://" + REGISTRIES[ref["registry"]][0] +
                      "/v2/" + ref["repository"] + "/manifests/" + target,
                      state="current" if local["platform_digest"] == current["platform_digest"] else "update_available",
                      reason="same_platform_manifest" if local["platform_digest"] == current["platform_digest"] else "platform_manifest_changed")
    except (ValueError, TypeError, KeyError, OSError, http.client.HTTPException):
        output.update(state="unknown", reason="registry_or_provenance_unavailable")
    return output


REMOTE = r'''import subprocess,json,re
def run(args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=20,check=True)
 if len(r.stdout)>131072: raise ValueError('metadata budget')
 return r.stdout
ids=run(['docker','ps','--all','--format','{{.ID}}']).splitlines()
if len(ids)>64 or any(not re.fullmatch('[a-f0-9]{12,64}',x) for x in ids): raise ValueError('container budget')
rows=[]
for cid in ids:
 c=json.loads(run(['docker','inspect','--format','{"container":{{json .Name}},"image":{{json .Config.Image}},"image_id":{{json .Image}}}',cid]))
 c['container']=c['container'].lstrip('/')
 if not re.fullmatch('sha256:[a-f0-9]{64}',c['image_id']): raise ValueError('image identity')
 try:
  i=json.loads(run(['docker','image','inspect','--format','{"repo_digests":{{json .RepoDigests}},"os":{{json .Os}},"architecture":{{json .Architecture}}}',c['image_id']]))
 except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
  i={'repo_digests':None,'os':None,'architecture':None}
 rows.append({**c,**i})
print(json.dumps({'schema_version':1,'containers':rows}))
'''


def collect_remote(host: str, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if host not in {"home-core", "home-spark", "agents-guest"}:
        raise ValueError("host outside reviewed inventory targets")
    args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
    if host == "agents-guest":
        args += ["-J", "home-core", "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_ai-services-01"), "hermes-operator@10.77.20.2"]
    else:
        args.append(host)
    args.append("python3 -" if host == "home-spark" else "sudo -n python3 -")
    result = runner(args, input=REMOTE, capture_output=True, text=True, timeout=180)
    if result.returncode or len(result.stdout) > 262144:
        raise ValueError("deployed image metadata unavailable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("containers"), list) or len(value["containers"]) > 64:
        raise ValueError("invalid deployed image snapshot")
    return value


def inventory_local(runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    result = runner([sys.executable, "-c", REMOTE], capture_output=True, text=True, timeout=180)
    if result.returncode or len(result.stdout) > 262144:
        raise ValueError("deployed image metadata unavailable")
    return json.loads(result.stdout)


def collect(host: str, policy: dict[str, Any], snapshot: dict[str, Any] | None = None,
            registry: Registry | None = None) -> dict[str, Any]:
    if (host not in {"home-core", "home-spark", "agents-guest"} or not isinstance(policy, dict)
            or set(policy) != {"schema_version", "containers"} or policy["schema_version"] != 1
            or not isinstance(policy["containers"], dict) or len(policy["containers"]) > 64):
        raise ValueError("invalid reviewed tracking registry")
    for name, rule in policy["containers"].items():
        if (not isinstance(name, str) or not CONTAINER.fullmatch(name) or not isinstance(rule, dict)
                or set(rule) != {"tracked_tag", "source_lane"} or rule["source_lane"] not in {"registry", "source_pin"}
                or rule["tracked_tag"] is not None and (not isinstance(rule["tracked_tag"], str) or not TAG.fullmatch(rule["tracked_tag"]))):
            raise ValueError("invalid reviewed container tracking policy")
    snapshot = snapshot if snapshot is not None else inventory_local()
    if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("containers"), list)
            or len(snapshot["containers"]) > 64):
        raise ValueError("invalid deployed image snapshot")
    deployed = {}
    for row in snapshot["containers"]:
        if not isinstance(row, dict) or row.get("container") in deployed:
            raise ValueError("invalid or duplicated container metadata")
        deployed[row.get("container")] = row
    registry = registry or Registry()
    rows = []
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rules = {name: {"tracked_tag": None, "source_lane": "registry"} for name in deployed}
    rules.update(policy["containers"])
    if len(rules) > 64:
        raise ValueError("deployed tracking union exceeds budget")
    for name, rule in rules.items():
        if name in deployed:
            rows.append(check_image(host, deployed[name], registry, **rule))
        else:
            rows.append({"host": host, "container": name, "state": "unknown", "reason": "container_not_deployed", "checked_at": generated})
    return {"schema_version": 1, "mode": "observe-only", "automatic_actions": False, "host": host,
            "generated_at": generated, "images": rows, "registry_requests": registry.calls,
            "summary": {state: sum(row["state"] == state for row in rows) for state in ("update_available", "current", "unknown", "untracked")}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, choices=["home-core", "home-spark", "agents-guest"])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--local", action="store_true", help="use local metadata, without any SSH credentials")
    parser.add_argument("--tracking", type=Path, required=True, help="reviewed finite per-container policy")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    tracking = json.loads(args.tracking.read_text())
    if set(tracking) == {"schema_version", "hosts"} and tracking["schema_version"] == 1:
        tracking = tracking["hosts"][args.host]
    snapshot = json.loads(args.snapshot.read_text()) if args.snapshot else inventory_local() if args.local else collect_remote(args.host)
    report = collect(args.host, tracking, snapshot=snapshot)
    encoded = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
