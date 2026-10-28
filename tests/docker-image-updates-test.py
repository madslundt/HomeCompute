"""Manifest/platform correctness and metadata-only registry trust boundaries."""
from __future__ import annotations
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import docker_image_updates as updates


def manifest(config):
    return {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:" + config * 64}, "layers": []}


def encoded(value):
    body = json.dumps(value, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest(), body


def index(amd, arm):
    return {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [
        {"digest": amd, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": arm, "platform": {"os": "linux", "architecture": "arm64"}}]}


class RegistryFixture:
    def __init__(self):
        self.old, old = encoded(manifest("a")); self.new, new = encoded(manifest("b")); self.arm, arm = encoded(manifest("c"))
        self.old_index, old_index = encoded(index(self.old, self.arm))
        self.new_index, new_index = encoded(index(self.new, self.arm))
        self.values = {self.old: old, self.new: new, self.arm: arm, self.old_index: old_index, self.new_index: new_index, "stable": new_index}
        self.calls = []
        self.client = updates.Registry(self.request)

    def request(self, host, path, headers, maximum):
        self.calls.append((host, path, headers))
        if host != "registry-1.docker.io" or not path.startswith("/v2/library/demo/manifests/"):
            raise AssertionError("unexpected registry/blob/auth request")
        key = path.rsplit("/", 1)[1]
        body = self.values[key]
        return 200, {"docker-content-digest": "sha256:" + hashlib.sha256(body).hexdigest()}, body

    def row(self, *, classic=False):
        return {"container": "demo", "image": "demo:stable@" + self.old_index,
                "image_id": "sha256:" + "a" * 64 if classic else self.old,
                "repo_digests": ["demo@" + self.old_index], "os": "linux", "architecture": "amd64"}


class ImageTests(unittest.TestCase):
    def test_reference_canonicalization_and_injection_denials(self):
        self.assertEqual(updates.reference("caddy:2-alpine")["repository"], "library/caddy")
        self.assertEqual(updates.reference("index.docker.io/library/python:3.12")["registry"], "docker.io")
        self.assertEqual(updates.reference("ghcr.io/org/image@sha256:" + "a" * 64)["tag"], None)
        for value in ("http://192.168.1.1/image", "localhost/image", "evil.example/image", "ghcr.io:443/org/image",
                      "ghcr.io/Org/image", "ghcr.io/org/../image", "ghcr.io/org/image?token=secret",
                      "ghcr.io/org/image%2fsecret", "ghcr.io/org/image:tag\n", "secret@ghcr.io/org/image", "sha256:" + "a" * 64):
            with self.subTest(value=value), self.assertRaises(ValueError): updates.reference(value)

    def test_changed_platform_is_update_for_classic_and_containerd_identity(self):
        for classic in (False, True):
            fixture = RegistryFixture()
            report = updates.check_image("home-core", fixture.row(classic=classic), fixture.client)
            self.assertEqual(report["state"], "update_available")
            self.assertEqual(report["local_manifest_digest"], fixture.old)
            self.assertEqual(report["remote_platform_digest"], fixture.new)
            self.assertEqual(report["remote_index_digest"], fixture.new_index)
            self.assertTrue(all("/manifests/" in path for _, path, _ in fixture.calls))

    def test_index_change_for_other_platform_is_current_here(self):
        fixture = RegistryFixture()
        other_arm, other_body = encoded(manifest("d"))
        fixture.values[other_arm] = other_body
        _, fixture.values["stable"] = encoded(index(fixture.old, other_arm))
        report = updates.check_image("home-core", fixture.row(), fixture.client)
        self.assertEqual(report["state"], "current")
        self.assertEqual(report["remote_platform_digest"], fixture.old)
        self.assertFalse(any(path.endswith(other_arm) for _, path, _ in fixture.calls))

    def test_mutable_tag_without_pin_uses_pulled_digest_provenance(self):
        fixture = RegistryFixture(); row=fixture.row(); row["image"]="demo:stable"
        self.assertEqual(updates.check_image("home-core", row, fixture.client)["state"], "update_available")

    def test_digest_only_and_local_build_need_reviewed_tracking_lane(self):
        fixture = RegistryFixture(); row=fixture.row(); row["image"]="demo@" + fixture.old_index
        report=updates.check_image("home-core", row, fixture.client)
        self.assertEqual((report["state"],report["reason"]), ("untracked", "digest_pin_without_tracked_tag"))
        self.assertEqual(fixture.calls, [])
        self.assertEqual(updates.check_image("home-core", row, fixture.client, tracked_tag="stable")["state"], "update_available")
        fixture = RegistryFixture(); row=fixture.row(); row["repo_digests"]=[]
        self.assertEqual(updates.check_image("home-core", row, fixture.client)["state"], "untracked")
        self.assertEqual(fixture.calls, [])

    def test_pinned_digest_must_match_deployed_provenance_and_identity(self):
        fixture=RegistryFixture(); row=fixture.row(); row["repo_digests"]=["demo@" + fixture.new_index]
        self.assertEqual(updates.check_image("home-core",row,fixture.client)["state"], "unknown")
        fixture=RegistryFixture(); row=fixture.row(); row["image_id"]="sha256:" + "f" * 64
        self.assertEqual(updates.check_image("home-core",row,fixture.client)["state"], "unknown")

    def test_manifest_digest_and_ambiguous_platform_fail_closed(self):
        client=updates.Registry(lambda *_:(200,{"docker-content-digest":"sha256:"+"f"*64},encoded(manifest("a"))[1]))
        with self.assertRaises(updates.RegistryError): client.manifest("docker.io","library/demo","stable")
        fixture=RegistryFixture(); body=index(fixture.old,fixture.arm); body["manifests"].append(body["manifests"][0])
        _,fixture.values["stable"]=encoded(body)
        self.assertEqual(updates.check_image("home-core",fixture.row(),fixture.client)["state"], "unknown")

    def test_anonymous_auth_realm_scope_and_no_redirect_rules(self):
        body=encoded(manifest("a"))[1]; calls=[]
        def request(host,path,headers,maximum):
            calls.append((host,path,headers))
            if host == "auth.docker.io":
                self.assertNotIn("Authorization",headers)
                self.assertIn("scope=repository%3Alibrary%2Fdemo%3Apull",path)
                return 200,{},b'{"token":"anonymous-public-read-token"}'
            if "Authorization" not in headers:
                return 401,{"www-authenticate":'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/demo:pull"'},b''
            self.assertEqual(headers["Authorization"], "Bearer anonymous-public-read-token")
            return 200,{},body
        updates.Registry(request).manifest("docker.io","library/demo","stable")
        self.assertEqual(len(calls),3)
        for challenge in ('Bearer realm="http://127.0.0.1/token",service="registry.docker.io"',
                          'Bearer realm="https://auth.docker.io/token?redirect=private",service="registry.docker.io"',
                          'Bearer realm="https://auth.docker.io/token",service="wrong"',
                          'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:private/repo:push"'):
            client=updates.Registry(lambda *_:(401,{"www-authenticate":challenge},b''))
            with self.assertRaises(updates.RegistryError): client.manifest("docker.io","library/demo","stable")
            self.assertEqual(client.calls,1)
        client=updates.Registry(lambda *_:(302,{"location":"http://169.254.169.254/"},b''))
        with self.assertRaises(updates.RegistryError): client.manifest("docker.io","library/demo","stable")
        self.assertEqual(client.calls,1)

    def test_dns_special_use_addresses_never_reach_socket(self):
        for address in ("127.0.0.1","10.77.21.1","100.64.0.1","169.254.169.254","::1","fd00::1"):
            answers=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(address,443))]
            with patch.object(updates.socket,"getaddrinfo",return_value=answers), patch.object(updates.socket,"create_connection") as connect:
                with self.assertRaises(updates.RegistryError): updates.PublicHTTPS("ghcr.io").connect()
                connect.assert_not_called()

    def test_public_socket_is_pinned_to_checked_address_with_hostname_tls(self):
        answers=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))]
        connection=updates.PublicHTTPS("ghcr.io",timeout=3)
        with (patch.object(updates.socket,"getaddrinfo",return_value=answers),
              patch.object(updates.socket,"create_connection") as connect,
              patch.object(connection._context,"wrap_socket") as wrap):
            connection.connect()
            connect.assert_called_once_with(("8.8.8.8",443),3)
            wrap.assert_called_once_with(connect.return_value,server_hostname="ghcr.io")

    def test_collection_covers_new_deployments_and_omits_secret_fields(self):
        fixture=RegistryFixture(); row=fixture.row(); row["environment"]="SECRET"
        policy={"schema_version":1,"containers":{"demo":{"tracked_tag":"stable","source_lane":"registry"},
                                                  "missing":{"tracked_tag":None,"source_lane":"registry"}}}
        report=updates.collect("home-core",policy,{"schema_version":1,"containers":[row,{"container":"new-deployment","image":"homecompute/new-service:local","repo_digests":[]}]},fixture.client)
        self.assertEqual(report["summary"],{"update_available":1,"current":0,"unknown":1,"untracked":1})
        self.assertNotIn("SECRET",json.dumps(report))
        self.assertIn("new-deployment",json.dumps(report))
        self.assertFalse(report["automatic_actions"])

    def test_inventory_commands_and_targets_are_finite_metadata_only(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs));return subprocess.CompletedProcess(argv,0,'{"schema_version":1,"containers":[]}',"SECRET")
        updates.collect_remote("home-core",runner)
        self.assertEqual(calls[0][0][-2:],["home-core","sudo -n python3 -"])
        self.assertIn("StrictHostKeyChecking=yes",calls[0][0])
        for forbidden in (".Env", "pull", "login", "restart", "blobs"):
            self.assertNotIn(forbidden,updates.REMOTE)
        with self.assertRaises(ValueError): updates.collect_remote("evil",runner)


if __name__ == "__main__": unittest.main()
