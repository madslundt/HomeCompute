"""Restricted, source-only improvement controller for assistant briefing policy.

Only config/assistant-briefings.json may change. Candidate author reports are
never evaluator evidence: this controller runs a fixed independent replay and
validation suite, persists its own receipt, and checks a separately provisioned
operator grant before publication.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import difflib
import grp
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from typing import Any, Callable, Mapping
import uuid

from assistant_attention import AttentionLedger, Scope as AttentionScope
from assistant_context_briefings import BriefingBuilder, load_config as load_briefing_config
from assistant_context_store import ContextStore, Scope as ContextScope

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = "config/assistant-briefings.json"
FIXED_REMOTE = "git@github.com:madslundt/HomeCompute.git"
BASE_REF = "origin/master"
TARGET_PATH = Path("/var/lib/homecompute-assistant-policy/assistant-briefings.json")
STATUS_PATH = Path("/var/lib/homecompute-assistant-policy/improvements-status.json")
CONTROLLER_ROOT = Path("/var/lib/homecompute-assistant-policy-controller")
MAX_PATCH_BYTES = 12000
CLASSES = {"briefing-format", "attention-threshold"}
PATCH_FIELDS = {
    "briefing-format": {"max_bullets", "max_chars", "include_source_links"},
    "attention-threshold": {"minimum_relevance", "minimum_repeat_evidence"},
}
FULL_SHA = re.compile(r"[0-9a-f]{40,64}")
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


class ImprovementError(ValueError):
    """A candidate or controller transition failed a fixed safety gate."""


class AttentionReadSnapshot:
    """Copy a consistent WAL snapshot without letting root touch the source DB."""
    def __init__(self, source: Path, scratch_root: Path): self.source,self.scratch_root=Path(source),Path(scratch_root); self.temp=None; self.ledger=None

    @staticmethod
    def _sqlite_snapshot_bytes(source:Path,expected:os.stat_result)->bytes:
        current=source.lstat()
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode) or (current.st_dev,current.st_ino,current.st_uid)!=(expected.st_dev,expected.st_ino,expected.st_uid):
            raise ImprovementError("attention source changed during snapshot")
        uri=source.resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=5)) as source_db,closing(sqlite3.connect(":memory:")) as snapshot:
            source_db.backup(snapshot)
            raw=snapshot.serialize()
        if len(raw)>64*1024*1024: raise ImprovementError("attention snapshot exceeds 64 MiB bound")
        return raw

    def _source_snapshot_bytes(self,info:os.stat_result)->bytes:
        euid=os.geteuid()
        if euid==0 and info.st_uid==0:
            raise ImprovementError("root-owned attention database refused; context service must own its ledger")
        if euid==info.st_uid:
            return self._sqlite_snapshot_bytes(self.source,info)
        if euid!=0:
            raise ImprovementError("controller must read the attention database as its owning service UID")
        read_fd,write_fd=os.pipe()
        child=os.fork()
        if child==0:
            os.close(read_fd)
            try:
                os.setgroups([info.st_gid]); os.setgid(info.st_gid); os.setuid(info.st_uid)
                payload=self._sqlite_snapshot_bytes(self.source,info)
                frame=b"OK"+len(payload).to_bytes(8,"big")+payload
                while frame:
                    written=os.write(write_fd,frame); frame=frame[written:]
                os._exit(0)
            except BaseException as error:
                frame=b"ER"+len(type(error).__name__).to_bytes(8,"big")+type(error).__name__.encode()
                try:
                    while frame:
                        written=os.write(write_fd,frame); frame=frame[written:]
                except OSError: pass
                os._exit(1)
        os.close(write_fd)
        chunks=[]; total=0; limit=64*1024*1024+10; too_large=False
        try:
            while True:
                chunk=os.read(read_fd,min(65536,limit-total+1))
                if not chunk: break
                chunks.append(chunk); total+=len(chunk)
                if total>limit:
                    os.kill(child,9)
                    too_large=True; break
        finally: os.close(read_fd)
        _,status=os.waitpid(child,0)
        if too_large: raise ImprovementError("attention snapshot exceeds 64 MiB bound")
        framed=b"".join(chunks)
        if len(framed)<10: raise ImprovementError("owner-scoped attention snapshot failed")
        kind,length=framed[:2],int.from_bytes(framed[2:10],"big"); payload=framed[10:]
        if kind!=b"OK" or not os.WIFEXITED(status) or os.WEXITSTATUS(status)!=0 or length!=len(payload):
            raise ImprovementError("owner-scoped attention snapshot failed")
        return payload

    def __enter__(self):
        if self.source.is_symlink() or not self.source.is_file(): raise ImprovementError("attention source database unavailable")
        info=self.source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode) not in {0o600,0o640}:
            raise ImprovementError("attention source must be a regular private database")
        self.scratch_root.mkdir(mode=0o700,parents=True,exist_ok=True)
        scratch_info=self.scratch_root.lstat()
        if (stat.S_ISLNK(scratch_info.st_mode) or not stat.S_ISDIR(scratch_info.st_mode)
                or scratch_info.st_uid!=os.geteuid() or stat.S_IMODE(scratch_info.st_mode)!=0o700):
            raise ImprovementError("controller scratch root must be private to its owner")
        self.temp=tempfile.TemporaryDirectory(prefix=".candidate-snapshot-",dir=self.scratch_root)
        target=Path(self.temp.name)/"attention.snapshot.sqlite3"
        raw=self._source_snapshot_bytes(info)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            view=memoryview(raw)
            while view:
                written=os.write(fd,view); view=view[written:]
            os.fsync(fd)
        finally: os.close(fd)
        os.chmod(target,0o600)
        self.ledger=AttentionLedger(target,dry_run=True)
        return self.ledger
    def __exit__(self,*exc):
        if self.temp: self.temp.cleanup()


def now_utc(value: datetime | str | None = None) -> datetime:
    if value is None: return datetime.now(timezone.utc)
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None: raise ImprovementError("timezone-aware instant required")
    return result.astimezone(timezone.utc)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def parse_json(data: bytes | str) -> Any:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise ImprovementError("duplicate JSON field")
            out[key] = value
        return out
    def nonfinite(_): raise ImprovementError("non-finite JSON refused")
    try: return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as exc: raise ImprovementError("valid JSON required") from exc


def _load_policy(raw: bytes | str) -> dict[str, Any]:
    value = parse_json(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("timezone") != "Europe/Copenhagen":
        raise ImprovementError("known briefing policy schema required")
    formatting = value.get("formatting")
    thresholds = value.get("trusted_adaptation")
    if (not isinstance(formatting, dict) or set(formatting) != {"max_bullets", "max_chars", "include_source_links"}
            or type(formatting["max_bullets"]) is not int or not 1 <= formatting["max_bullets"] <= 8
            or type(formatting["max_chars"]) is not int or not 100 <= formatting["max_chars"] <= 1000
            or type(formatting["include_source_links"]) is not bool
            or not isinstance(thresholds, dict) or set(thresholds) != {"minimum_relevance", "minimum_repeat_evidence"}
            or type(thresholds["minimum_relevance"]) not in (int, float) or not 0 <= thresholds["minimum_relevance"] <= 1
            or type(thresholds["minimum_repeat_evidence"]) is not int or not 2 <= thresholds["minimum_repeat_evidence"] <= 10):
        raise ImprovementError("bounded policy fields required")
    return value


def apply_candidate_diff(raw: bytes | str, allowed_class: str, proposed_diff: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Apply exactly the class-specific subfields while preserving all other JSON values."""
    if allowed_class not in PATCH_FIELDS or not isinstance(proposed_diff, Mapping) or not proposed_diff:
        raise ImprovementError("one allowlisted candidate class required")
    if set(proposed_diff) - PATCH_FIELDS[allowed_class]: raise ImprovementError("extra patch field refused")
    before = _load_policy(raw)
    after = json.loads(json.dumps(before))
    target = after["formatting" if allowed_class == "briefing-format" else "trusted_adaptation"]
    baseline = before["formatting" if allowed_class == "briefing-format" else "trusted_adaptation"]
    for key, value in proposed_diff.items():
        if key == "max_bullets" and (type(value) is not int or not 1 <= value <= 8): raise ImprovementError("max_bullets out of range")
        if key == "max_chars" and (type(value) is not int or not 100 <= value <= 1000): raise ImprovementError("max_chars out of range")
        if key == "include_source_links" and (type(value) is not bool or value is not True): raise ImprovementError("automatic format changes must keep source links enabled")
        if key == "minimum_relevance" and (type(value) not in (int, float) or not 0 <= value <= 1 or value < baseline[key]): raise ImprovementError("automatic relevance changes may only tighten")
        if key == "minimum_repeat_evidence" and (type(value) is not int or not 2 <= value <= 10 or value < baseline[key]): raise ImprovementError("automatic repetition changes may only tighten")
        target[key] = value
    after_raw = (json.dumps(after, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _load_policy(after_raw)
    return after_raw, after


def source_diff(before: bytes, after: bytes) -> str:
    diff = "".join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
        fromfile="a/" + SOURCE_FILE, tofile="b/" + SOURCE_FILE, n=3))
    if len(diff.encode()) > MAX_PATCH_BYTES: raise ImprovementError("review patch exceeds budget")
    return diff


def _heldout_briefing(policy:dict[str,Any])->dict[str,Any]:
    """Run the real source builder over a fixed, controller-owned four-source fixture."""
    fixed_now=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
    records=[
        ("home-assistant","heat","Family arrival preparation: check heating systems and review required preparations before the visit. "+"details "*12,{}),
        ("daythread","promise","Family arrival preparation reminder: follow up and confirm required preparations before the visit. "+"details "*12,{}),
        ("aula","class","Family arrival preparation notice: school timing affects required preparations before the visit. "+"details "*12,{}),
        ("calendar","event-a","Family arrival preparation event: reserve the preparation window before the visit. "+"details "*12,
            {"start":"2026-11-06T10:00:00+01:00","end":"2026-11-06T11:00:00+01:00"}),
        ("calendar","event-b","Family arrival preparation event: visit timing overlaps the reserved preparation window. "+"details "*12,
            {"start":"2026-11-06T10:30:00+01:00","end":"2026-11-06T11:30:00+01:00"}),
    ]
    scope=ContextScope("owner","personal","private")
    with tempfile.TemporaryDirectory(prefix="assistant-policy-heldout-") as directory:
        store=ContextStore(Path(directory)/"heldout.sqlite3")
        for source,object_id,summary,interval in records:
            payload={"summary":summary,**interval}
            store.ingest(scope,{"schema_version":1,"source":source,"object_id":object_id,"revision":1,
                "event_type":"daythread.summary" if source=="daythread" else "snapshot","operation":"upsert",
                "occurred_at":"2026-11-06T09:00:00Z","source_ref":f"fixture:{source}:{object_id}","payload":payload})
        coverage={source:{"state":"fresh","last_success_at":fixed_now.isoformat()}
            for source in ("home-assistant","daythread","aula","calendar")}
        return BriefingBuilder(store,scope=scope,coverage=coverage,clock=lambda:fixed_now,
            formatting=policy["formatting"],adaptation=policy["trusted_adaptation"]).build("today")


def _citation_quality(brief:dict[str,Any],*,minimum_text:int)->tuple[set[str],bool]:
    known={json.dumps(row["ref"],sort_keys=True) for row in brief["source_items"] if row["freshness"]=="fresh"}
    cited=set(); valid=True
    for item in brief["cross_source_summary"]["items"]:
        refs=item.get("source_refs",[])
        if not refs or len(item.get("text",""))<minimum_text: valid=False
        for ref in refs:
            if not isinstance(ref,dict) or set(ref)!={"source","object_id","revision"} or json.dumps(ref,sort_keys=True) not in known:
                valid=False
            else: cited.add(ref["source"])
    return cited,valid


def heldout_replay(before_raw: bytes, after_raw: bytes, allowed_class: str, *, round_number: int = 0) -> dict[str, Any]:
    """Run deterministic real-builder output over synthetic sources; consume no candidate report or private data."""
    before, after = _load_policy(before_raw), _load_policy(after_raw)
    if allowed_class not in PATCH_FIELDS: raise ImprovementError("unknown replay class")
    before_values = before["formatting" if allowed_class == "briefing-format" else "trusted_adaptation"]
    after_values = after["formatting" if allowed_class == "briefing-format" else "trusted_adaptation"]
    fixture_sources = ["home-assistant", "daythread", "aula", "calendar"]
    baseline_brief=_heldout_briefing(before)
    candidate_brief=_heldout_briefing(after)
    base_sources,base_citations_valid=_citation_quality(baseline_brief,minimum_text=120)
    after_sources,after_citations_valid=_citation_quality(candidate_brief,minimum_text=120)
    source_citations_valid=base_citations_valid and after_citations_valid
    preserved_context=(len(candidate_brief["calendar_collisions"])>=len(baseline_brief["calendar_collisions"])
        and len(candidate_brief["daythread_followups"])>=len(baseline_brief["daythread_followups"])
        and candidate_brief["action_authority"]=="none" and candidate_brief["interpretation_only"] is True)
    if allowed_class == "briefing-format":
        max_bullets=after_values["max_bullets"]
        bounded=(len(candidate_brief["cross_source_summary"]["items"])<=max_bullets
            and all(len(item["text"])<=after_values["max_chars"] for item in candidate_brief["cross_source_summary"]["items"]))
        passed=(after_sources==base_sources==set(fixture_sources) and source_citations_valid and bounded and preserved_context
            and after_values["include_source_links"] is True)
        metric={"fixture_source_count":len(fixture_sources),"baseline_cited_sources":sorted(base_sources),
            "candidate_cited_sources":sorted(after_sources),"candidate_summary_bullets":len(candidate_brief["cross_source_summary"]["items"]),
            "candidate_max_bullet_chars":max((len(item["text"]) for item in candidate_brief["cross_source_summary"]["items"]),default=0),
            "candidate_collisions":len(candidate_brief["calendar_collisions"]),"candidate_followups":len(candidate_brief["daythread_followups"]),
            "citation_revisions_valid":source_citations_valid,"interpretation_only":candidate_brief["interpretation_only"],
            "action_authority":candidate_brief["action_authority"]}
    else:
        base_out={json.dumps([item["summary"],item["source_refs"]],sort_keys=True) for item in baseline_brief["cross_source_correlations"]}
        after_out={json.dumps([item["summary"],item["source_refs"]],sort_keys=True) for item in candidate_brief["cross_source_correlations"]}
        correlations_valid=all(item.get("uncertainty")=="suggestion_only" and len({ref["source"] for ref in item.get("source_refs",[])})>=2
            and all(json.dumps(ref,sort_keys=True) in {json.dumps(row["ref"],sort_keys=True) for row in candidate_brief["source_items"] if row["freshness"]=="fresh"}
                for ref in item["source_refs"]) for item in candidate_brief["cross_source_correlations"])
        passed=(bool(after_out) and after_out<=base_out and correlations_valid and after_sources==base_sources==set(fixture_sources)
            and source_citations_valid and preserved_context and after_values["minimum_relevance"]>=before_values["minimum_relevance"]
            and after_values["minimum_repeat_evidence"]>=before_values["minimum_repeat_evidence"])
        metric={"fixture_source_count":len(fixture_sources),"baseline_correlations":len(base_out),"candidate_correlations":len(after_out),
            "baseline_cited_sources":sorted(base_sources),"candidate_cited_sources":sorted(after_sources),
            "candidate_collisions":len(candidate_brief["calendar_collisions"]),"candidate_followups":len(candidate_brief["daythread_followups"]),
            "citation_revisions_valid":source_citations_valid,"correlations_valid":correlations_valid,
            "interpretation_only":candidate_brief["interpretation_only"],"action_authority":candidate_brief["action_authority"]}
    receipt = {"evaluator": "homecompute-improvement-replay-v1", "round": round_number,
               "allowed_class": allowed_class, "passed": bool(passed), "metrics": metric,
               "authority_expansion": False, "source_expansion": False}
    receipt["receipt_sha256"] = sha(canonical(receipt))
    return receipt


def _env(temp_home: str) -> dict[str, str]:
    allowed = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": temp_home,
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
               "PIP_CONFIG_FILE": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
               "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "*"}
    return allowed


class IndependentEvaluator:
    """Archive a pinned trusted commit, apply one config delta, and run fixed checks."""
    COMMANDS = (("python3", "tests/assistant-context-briefings-test.py"),
                ("bash", "./scripts/validate-repository.sh"))
    def __init__(self, repo: Path, *, runner: Callable[..., Any] = subprocess.run): self.repo, self.runner = repo, runner

    def evaluate(self, revision: str, before: bytes, after: bytes, allowed_class: str) -> dict[str, Any]:
        if not FULL_SHA.fullmatch(revision): raise ImprovementError("full trusted source revision required")
        replay = heldout_replay(before, after, allowed_class)
        with tempfile.TemporaryDirectory(prefix="assistant-improve-eval-") as directory:
            home = Path(directory) / "home"; home.mkdir(mode=0o700)
            env = _env(str(home))
            verify = self.runner(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", revision, BASE_REF],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False, timeout=15)
            if verify.returncode != 0: raise ImprovementError("evaluation source must be on origin/master")
            archive = self.runner(["git", "-C", str(self.repo), "archive", "--format=tar", revision],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False, timeout=30)
            if archive.returncode != 0: raise ImprovementError("trusted source archive failed")
            import tarfile, io
            with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
                for member in tar.getmembers():
                    destination = (Path(directory) / member.name).resolve()
                    if Path(directory).resolve() not in destination.parents: raise ImprovementError("archive path traversal refused")
                tar.extractall(directory, filter="data")
            root = Path(directory)
            base = self.runner(["git", "-C", str(self.repo), "show", revision + ":" + SOURCE_FILE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False, timeout=15)
            if base.returncode != 0 or sha(base.stdout) != sha(before): raise ImprovementError("trusted config preimage changed")
            policy_path = root / SOURCE_FILE
            policy_path.write_bytes(after)
            run_results = []
            for command in self.COMMANDS:
                result = self.runner(list(command), cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, check=False, timeout=600)
                run_results.append({"command": list(command), "returncode": int(result.returncode),
                                    "stdout_sha256": sha(result.stdout or b""), "stderr_sha256": sha(result.stderr or b"")})
                if result.returncode != 0: break
            passed = replay["passed"] and len(run_results) == len(self.COMMANDS) and all(row["returncode"] == 0 for row in run_results)
            receipt = {"evaluator": "homecompute-improvement-suite-v1", "trusted_revision": revision,
                       "passed": passed, "replay": replay, "commands": run_results,
                       "changed_file": SOURCE_FILE, "private_data_used": False}
            receipt["receipt_sha256"] = sha(canonical(receipt))
            return receipt

    def canary(self, revision: str, before: bytes, after: bytes, allowed_class: str, rounds: int) -> dict[str, Any]:
        receipts = [heldout_replay(before, after, allowed_class, round_number=index + 1) for index in range(rounds)]
        passed = rounds == 3 and all(item["passed"] for item in receipts)
        return {"evaluator": "homecompute-improvement-canary-v1", "trusted_revision": revision,
                "passed": passed, "rounds": receipts, "authority_expansion": False,
                "source_expansion": False, "receipt_sha256": sha(canonical(receipts))}


def _git(repo: Path, args: list[str], *, timeout: int = 30) -> str:
    env = _env(str(repo))
    result = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, check=False, timeout=timeout)
    if result.returncode: raise ImprovementError("git operation failed: " + " ".join(args[:2]))
    return result.stdout.decode().strip()


class GitPublisher:
    """Publish only a config-only candidate branch; production auth is pinned and external."""
    SSH_KEY=CONTROLLER_ROOT/"github-deploy-key"
    KNOWN_HOSTS=CONTROLLER_ROOT/"github-known-hosts"
    GH_TOKEN=Path("/run/credentials/homecompute-assistant-context-improvements.service/github-token")
    def __init__(self, repo: Path, *, remote: str = FIXED_REMOTE, test_remote: bool = False,
                 runner: Callable[..., Any] = subprocess.run,
                 ssh_key: Path = SSH_KEY, known_hosts: Path = KNOWN_HOSTS, gh_token: Path = GH_TOKEN):
        if not test_remote and remote != FIXED_REMOTE: raise ImprovementError("production Git remote is fixed")
        self.repo, self.remote, self.test_remote, self.runner = Path(repo), remote, test_remote, runner
        self.ssh_key,self.known_hosts,self.gh_token=Path(ssh_key),Path(known_hosts),Path(gh_token)

    def _git_env(self, home: Path) -> dict[str,str]:
        env=_env(str(home))
        if self.test_remote: return env
        for path in (self.ssh_key,self.known_hosts):
            if path.is_symlink() or not path.is_file(): raise ImprovementError("pinned controller SSH material unavailable")
        key_info=self.ssh_key.stat(); hosts_info=self.known_hosts.stat()
        if key_info.st_uid!=os.geteuid() or stat.S_IMODE(key_info.st_mode)!=0o600 or hosts_info.st_uid not in {0,os.geteuid()} or stat.S_IMODE(hosts_info.st_mode) not in {0o600,0o644}:
            raise ImprovementError("controller SSH key and pinned known_hosts permissions refused")
        ssh=("ssh -F /dev/null -i "+str(self.ssh_key)+" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes"
            " -o UserKnownHostsFile="+str(self.known_hosts)+" -o GlobalKnownHostsFile=/dev/null")
        env["GIT_SSH_COMMAND"]=ssh
        return env

    def publish(self, source_revision: str, candidate_id: str, before: bytes, after: bytes) -> dict[str, Any]:
        if not FULL_SHA.fullmatch(source_revision) or not re.fullmatch(r"[a-f0-9]{32}", candidate_id):
            raise ImprovementError("full source revision and stable candidate ID required")
        branch = "codex/assistant-improvement-" + candidate_id
        with tempfile.TemporaryDirectory(prefix="assistant-improve-publish-") as directory:
            clone = Path(directory) / "repo"
            env = self._git_env(Path(directory) / "home"); Path(env["HOME"]).mkdir(mode=0o700)
            clone_result = self.runner(["git", "clone", "--no-checkout", self.remote, str(clone)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, check=False, timeout=60)
            if clone_result.returncode: raise ImprovementError("isolated publication checkout failed")
            for args in (["-c", "core.hooksPath=/dev/null", "fetch", "origin", "master", branch],):
                result = self.runner(["git", "-C", str(clone), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, check=False, timeout=60)
                if result.returncode and branch not in result.stderr.decode(errors="replace"):
                    raise ImprovementError("isolated publication git step failed")
            existing=self.runner(["git","-C",str(clone),"rev-parse","--verify","refs/remotes/origin/"+branch],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,check=False,timeout=15)
            if existing.returncode==0:
                head=existing.stdout.decode().strip()
                parents=self.runner(["git","-C",str(clone),"rev-list","--parents","-n","1",head],stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,env=env,check=False,timeout=15).stdout.decode().strip().split()
                changed=self.runner(["git","-C",str(clone),"diff-tree","--no-commit-id","--name-only","-r",head],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,check=False,timeout=15).stdout.decode().splitlines()
                actual=self.runner(["git","-C",str(clone),"show",head+":"+SOURCE_FILE],stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,env=env,check=False,timeout=15)
                if (parents!=[head,source_revision] or changed!=[SOURCE_FILE] or actual.returncode or
                        sha(actual.stdout)!=sha(after)):
                    raise ImprovementError("existing publication branch does not exactly match candidate patch")
                return self._ensure_pull_request(branch,head,directory,env)
            for args in (["-c", "core.hooksPath=/dev/null", "checkout", "--detach", source_revision],
                         ["-c", "core.hooksPath=/dev/null", "checkout", "-b", branch]):
                result = self.runner(["git", "-C", str(clone), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, check=False, timeout=60)
                if result.returncode: raise ImprovementError("isolated publication git step failed")
            ancestor = self.runner(["git", "-C", str(clone), "merge-base", "--is-ancestor", source_revision, "origin/master"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False, timeout=15)
            if ancestor.returncode: raise ImprovementError("source revision is no longer based on origin/master")
            path = clone / SOURCE_FILE
            if sha(path.read_bytes()) != sha(before): raise ImprovementError("published source preimage changed")
            path.write_bytes(after)
            changed = self.runner(["git", "-C", str(clone), "diff", "--name-only"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, check=False, timeout=15)
            if changed.returncode or changed.stdout.decode().splitlines() != [SOURCE_FILE]:
                raise ImprovementError("publication checkout contains unrelated changes")
            self.runner(["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null", "add", "--", SOURCE_FILE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=True, timeout=15)
            self.runner(["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null", "commit", "-m", "Improve assistant briefing policy"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**env, "GIT_AUTHOR_NAME": "HomeCompute Improvement Controller",
                "GIT_AUTHOR_EMAIL": "homecompute-controller@users.noreply.github.com", "GIT_COMMITTER_NAME": "HomeCompute Improvement Controller",
                "GIT_COMMITTER_EMAIL": "homecompute-controller@users.noreply.github.com"}, check=True, timeout=30)
            head = self.runner(["git", "-C", str(clone), "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, check=True, timeout=15).stdout.decode().strip()
            self.runner(["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null", "push", "origin", branch],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=True, timeout=60)
            if self.test_remote: return {"branch": branch, "head_sha": head, "status": "queued_for_review", "pull_request": None}
            return self._ensure_pull_request(branch,head,directory,env)

    def _ensure_pull_request(self,branch:str,head:str,directory:str,env:dict[str,str])->dict[str,Any]:
        if self.test_remote: return {"branch": branch, "head_sha": head, "status": "queued_for_review", "pull_request": None}
        # Only sanitized metadata is emitted in the PR body.
        candidate_id=branch.rsplit("-",1)[-1]
        body = ("Automated assistant policy candidate.\n\nCandidate: " + candidate_id + "\nSource file: " + SOURCE_FILE +
            "\nThis change is a proposal only; protected CI and human review are required.\n")
        body_path = Path(directory) / "pr-body.md"; body_path.write_text(body)
        if self.gh_token.is_symlink() or not self.gh_token.is_file(): raise ImprovementError("controller GitHub credential unavailable")
        token_info=self.gh_token.stat()
        if token_info.st_uid!=os.geteuid() or stat.S_IMODE(token_info.st_mode) not in {0o400,0o600} or token_info.st_size>4096:
            raise ImprovementError("controller GitHub credential permissions refused")
        gh_env={**env,"GH_TOKEN":self.gh_token.read_text().strip()}
        existing=self.runner(["gh","pr","list","--repo","madslundt/HomeCompute","--head",branch,
            "--json","url,headRefOid,baseRefName"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=gh_env,check=False,timeout=60)
        if existing.returncode: raise ImprovementError("trusted pull request lookup failed")
        rows=parse_json(existing.stdout)
        if isinstance(rows,list) and rows:
            if len(rows)!=1 or rows[0].get("headRefOid")!=head or rows[0].get("baseRefName")!="master":
                raise ImprovementError("existing pull request differs from exact candidate head")
            return {"branch":branch,"head_sha":head,"status":"queued_for_review","pull_request":rows[0].get("url")}
        pr = self.runner(["gh", "pr", "create", "--repo", "madslundt/HomeCompute", "--base", "master", "--head", branch,
            "--title", "Assistant briefing policy candidate", "--body-file", str(body_path)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=gh_env, check=False, timeout=60)
        if pr.returncode: raise ImprovementError("branch pushed, but pull request creation failed")
        return {"branch": branch, "head_sha": head, "status": "queued_for_review", "pull_request": pr.stdout.decode().strip()}


class GitHubReconciler:
    """Read PR state and protected check results through the fixed GitHub CLI."""
    REQUIRED_CHECK = "static-validation"
    def __init__(self, repo: Path, runner: Callable[..., Any] = subprocess.run,
                 token_file:Path=GitPublisher.GH_TOKEN): self.repo, self.runner, self.token_file = Path(repo), runner, Path(token_file)
    def reconcile(self, branch: str, expected_head: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="assistant-improve-reconcile-") as directory:
            home=Path(directory)/"home"; home.mkdir(mode=0o700)
            env=GitPublisher(self.repo)._git_env(home)
            if _git(self.repo,["remote","get-url","origin"])!=FIXED_REMOTE:
                raise ImprovementError("trusted origin remote mismatch")
            self.runner(["git", "-C", str(self.repo), "fetch", "origin", "master"], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env, check=True, timeout=60)
            if self.token_file.is_symlink() or not self.token_file.is_file(): raise ImprovementError("controller GitHub credential unavailable")
            info=self.token_file.stat()
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode) not in {0o400,0o600} or info.st_size>4096:
                raise ImprovementError("controller GitHub credential permissions refused")
            gh_env={**env,"GH_TOKEN":self.token_file.read_text().strip()}
            result = self.runner(["gh", "pr", "view", branch, "--repo", "madslundt/HomeCompute",
                "--json", "state,headRefName,baseRefName,headRefOid,mergedAt,mergeCommit,statusCheckRollup"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=gh_env, check=False, timeout=60)
        if result.returncode: raise ImprovementError("trusted GitHub PR state unavailable")
        data = parse_json(result.stdout)
        if (data.get("state") != "MERGED" or data.get("headRefOid") != expected_head or not data.get("mergedAt")
                or data.get("headRefName")!=branch or data.get("baseRefName")!="master"):
            raise ImprovementError("candidate remains queued or PR head changed")
        checks = data.get("statusCheckRollup")
        if not isinstance(checks, list): raise ImprovementError("protected CI evidence missing")
        required = next((c for c in checks if c.get("name") == self.REQUIRED_CHECK), None)
        if not required or required.get("conclusion") != "SUCCESS": raise ImprovementError("required protected validation did not pass")
        merge = data.get("mergeCommit", {}).get("oid")
        master = _git(self.repo, ["rev-parse", "origin/master"])
        ancestor = self.runner(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", merge, "origin/master"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False, timeout=15)
        if not merge or ancestor.returncode: raise ImprovementError("merge commit is not present on origin/master")
        return {"merged_revision": merge, "origin_master": master, "trusted_check": self.REQUIRED_CHECK,
                "check_conclusion": "SUCCESS", "status": "reconciled"}


def load_controller_config(path: Path = ROOT / "config/assistant-improvements.json") -> dict[str, Any]:
    value=parse_json(path.read_bytes())
    expected={"schema_version":1,"source_file":SOURCE_FILE,"default_mode":"dry-run",
        "fixed_validation":[["python3","tests/assistant-context-briefings-test.py"],["bash","./scripts/validate-repository.sh"]],
        "publisher":{"remote":FIXED_REMOTE,"base":"master","branch_prefix":"codex/assistant-improvement-"},
        "policy_target":str(TARGET_PATH),"controller_state":str(CONTROLLER_ROOT),
        "standing_grant":str(CONTROLLER_ROOT/"standing-grant.json"),
        "standing_grant_key":str(CONTROLLER_ROOT/"standing-grant.key"),
        "backup_receipt":"/var/lib/homecompute/nas-recovery-backup/last-backup.json",
        "restore_receipt":"/var/lib/homecompute/nas-recovery-backup/last-application-verification.json",
        "github_ssh_key":str(GitPublisher.SSH_KEY),"github_known_hosts":str(GitPublisher.KNOWN_HOSTS),
        "github_token_credential":str(GitPublisher.GH_TOKEN),
        "policy_health_credential":"/run/credentials/homecompute-assistant-context-improvements.service/policy-health-token"}
    fixed={"enabled":False,"automatic_classes":["briefing-format","attention-threshold"],
        "heldout_replay_rounds":3,"canary_rounds":3,"max_candidates_per_grant":10,
        "required_checks":[GitHubReconciler.REQUIRED_CHECK]}
    if not isinstance(value,dict) or set(value)!=set(expected)|set(fixed) or any(value.get(key)!=item for key,item in (expected|fixed).items()):
        raise ImprovementError("fixed controller policy configuration required")
    if type(value.get("enabled")) is not bool:
        raise ImprovementError("controller remains disabled and fixed to bounded gates")
    return value


class ImprovementController:
    def __init__(self, attention: AttentionLedger, *, source_repo: Path = ROOT,
                 state_dir: Path = CONTROLLER_ROOT, grant_file: Path | None = None,
                 grant_key_file: Path | None = None, evaluator: Any | None = None,
                 publisher: Any | None = None, reconciler: Any | None = None,
                 clock: Callable[[], datetime] | None = None, enabled: bool = False,
                 test_mode: bool = False, status_path: Path | None = None):
        self.attention, self.repo, self.state = attention, Path(source_repo), Path(state_dir)
        self.grant_file = grant_file or (self.state / "standing-grant.json")
        self.grant_key_file = grant_key_file or (self.state / "standing-grant.key")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if not test_mode and any(item is not None for item in (evaluator,publisher,reconciler)):
            raise ImprovementError("production controller dependencies are fixed")
        self.enabled = bool(enabled)
        self.test_mode=test_mode
        self.status_path=Path(status_path) if status_path else (self.state/"improvements-status.json" if test_mode else STATUS_PATH)
        if not test_mode and self.status_path!=STATUS_PATH: raise ImprovementError("status projection path is fixed")
        self.evaluator = evaluator or IndependentEvaluator(self.repo)
        self.publisher = publisher or GitPublisher(self.repo)
        self.reconciler = reconciler or GitHubReconciler(self.repo)
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.state.stat()
        if self.state.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ImprovementError("controller state must be a private directory outside agent state")
        self.db_path = self.state / "controller.sqlite3"
        self._init_db()
        self.receipt_key = self._private_key(self.state / "receipt.key")

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        return db

    def _private_key(self, path: Path) -> bytes:
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try: os.write(fd, os.urandom(32))
            finally: os.close(fd)
        info = path.stat()
        if path.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 32:
            raise ImprovementError("private controller receipt key required")
        return path.read_bytes()

    def _init_db(self):
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(self._db()) as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS candidates(candidate_id TEXT PRIMARY KEY,principal TEXT NOT NULL,domain TEXT NOT NULL,visibility TEXT NOT NULL,request_id TEXT NOT NULL,allowed_class TEXT NOT NULL,source_revision TEXT NOT NULL,before_sha TEXT NOT NULL,after_sha TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,diff TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,test_receipt_json TEXT,canary_receipt_json TEXT,grant_id TEXT,branch TEXT,head_sha TEXT,merged_revision TEXT,published INTEGER NOT NULL DEFAULT 0,applied INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS requests(principal TEXT,domain TEXT,visibility TEXT,request_id TEXT,request_hash TEXT,response_json TEXT,PRIMARY KEY(principal,domain,visibility,request_id));
              CREATE TABLE IF NOT EXISTS grant_usage(grant_id TEXT,candidate_id TEXT,created_at TEXT,PRIMARY KEY(grant_id,candidate_id));
            """)
        os.chmod(self.db_path, 0o600)

    def _identity(self, scope: AttentionScope):
        if not isinstance(scope, AttentionScope) or (scope.principal_scope, scope.data_domain, scope.visibility) != ("owner", "personal", "private"):
            raise ImprovementError("owner-private candidate scope required")
        return "owner", "personal", "private"

    def _base_revision(self) -> str:
        if not getattr(self,"test_mode",False) and _git(self.repo,["remote","get-url","origin"])!=FIXED_REMOTE:
            raise ImprovementError("trusted origin remote mismatch")
        revision = _git(self.repo, ["rev-parse", BASE_REF])
        if not FULL_SHA.fullmatch(revision): raise ImprovementError("full origin/master revision required")
        return revision

    def _source_bytes(self, revision: str) -> bytes:
        env = _env(str(self.repo))
        result = subprocess.run(["git", "-C", str(self.repo), "show", revision + ":" + SOURCE_FILE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False, timeout=15)
        if result.returncode: raise ImprovementError("public source config unavailable at trusted revision")
        _load_policy(result.stdout)
        return result.stdout

    def _request_replay(self, identity, request_id: str, fingerprint: str):
        replay = self._db().execute("SELECT request_hash,response_json FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity,request_id)).fetchone()
        if replay:
            if replay[0] != fingerprint: raise ImprovementError("request ID reused with different operation")
            return json.loads(replay[1])
        return None

    def _commit_request(self, db, identity, request_id, fingerprint, result):
        db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)", (*identity,request_id,fingerprint,json.dumps(result,sort_keys=True,separators=(",", ":"))))

    def _candidate(self, scope: AttentionScope, candidate_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", candidate_id): raise ImprovementError("stable candidate ID required")
        row = self.attention.get_candidate(scope=scope, candidate_id=candidate_id)
        if not row or row.get("state") != "proposed": raise ImprovementError("current proposed AttentionLedger candidate required")
        return row

    def prepare(self, scope: AttentionScope, candidate_id: str, expected_source_sha: str, request_id: str) -> dict[str, Any]:
        identity = self._identity(scope)
        if not UUID4.fullmatch(request_id) or not re.fullmatch(r"[a-f0-9]{64}", expected_source_sha): raise ImprovementError("UUID4 request and expected source SHA required")
        fingerprint = sha(canonical(["prepare",candidate_id,expected_source_sha]))
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute("SELECT request_hash,response_json FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity,request_id)).fetchone()
            if replay:
                if replay[0] != fingerprint: db.rollback(); raise ImprovementError("request ID reused")
                result=json.loads(replay[1]); db.commit(); return result
            staged=db.execute("SELECT candidate_id,allowed_class,state,revision,source_revision,before_sha,after_sha,diff FROM candidates WHERE candidate_id=? AND principal=? AND domain=? AND visibility=?",(candidate_id,*identity)).fetchone()
            if staged:
                db.rollback()
                if staged["state"]=="rejected": raise ImprovementError("candidate was rejected by independent gates")
                raise ImprovementError("candidate already has a controller record")
            candidate = self._candidate(scope,candidate_id)
            source_rev=self._base_revision(); before=self._source_bytes(source_rev)
            if sha(before) != expected_source_sha: db.rollback(); raise ImprovementError("optimistic source SHA changed")
            allowed_class=candidate.get("allowed_class")
            after, _=apply_candidate_diff(before,allowed_class,candidate.get("proposed_diff",{}))
            if len(after)>65536: db.rollback(); raise ImprovementError("source policy exceeds bounded size")
            after_value=_load_policy(after); before_value=_load_policy(before)
            diff=source_diff(before,after)
            result={"candidate_id":candidate_id,"allowed_class":allowed_class,"source_file":SOURCE_FILE,
                    "source_revision":source_rev,"before_sha256":sha(before),"after_sha256":sha(after),
                    "patch":diff,"status":"prepared"}
            db.execute("INSERT INTO candidates(candidate_id,principal,domain,visibility,request_id,allowed_class,source_revision,before_sha,after_sha,before_json,after_json,diff,state,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate_id,*identity,request_id,allowed_class,source_rev,sha(before),sha(after),before.decode(),after.decode(),diff,"prepared",1,now_utc(self.clock()).isoformat(),now_utc(self.clock()).isoformat()))
            self._commit_request(db,identity,request_id,fingerprint,result); db.commit()
        self._write_status_projection()
        return result

    def _get(self, scope: AttentionScope, candidate_id: str):
        identity=self._identity(scope)
        with closing(self._db()) as db: row=db.execute("SELECT * FROM candidates WHERE candidate_id=? AND principal=? AND domain=? AND visibility=?",(candidate_id,*identity)).fetchone()
        if not row: raise ImprovementError("controller candidate not found in scope")
        return dict(row)

    def _transition(self, scope, candidate_id, expected: str, new_state: str, request_id: str, data: Mapping[str, Any]):
        identity=self._identity(scope)
        if not UUID4.fullmatch(request_id): raise ImprovementError("UUID4 request ID required")
        fingerprint=sha(canonical([new_state,candidate_id,data]))
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            replay=db.execute("SELECT request_hash,response_json FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?",(*identity,request_id)).fetchone()
            if replay:
                if replay[0]!=fingerprint: db.rollback(); raise ImprovementError("request ID reused")
                result=json.loads(replay[1]); db.commit(); return result
            row=db.execute("SELECT * FROM candidates WHERE candidate_id=? AND principal=? AND domain=? AND visibility=?",(candidate_id,*identity)).fetchone()
            if not row or row["state"]!=expected: db.rollback(); raise ImprovementError("candidate stage conflict")
            updates={**data,"state":new_state,"revision":row["revision"]+1,"updated_at":now_utc(self.clock()).isoformat()}
            allowed={"test_receipt_json","canary_receipt_json","grant_id","branch","head_sha","merged_revision","published","applied"}
            columns=[key for key in updates if key in allowed or key in {"state","revision","updated_at"}]
            values=[json.dumps(updates[k],sort_keys=True,separators=(",",":")) if k.endswith("_json") and updates[k] is not None else updates[k] for k in columns]
            db.execute("UPDATE candidates SET "+",".join(k+"=?" for k in columns)+" WHERE candidate_id=?",(*values,candidate_id))
            fresh=db.execute("SELECT candidate_id,allowed_class,state,revision,source_revision,before_sha,after_sha,branch,head_sha,merged_revision,published,applied,grant_id,updated_at FROM candidates WHERE candidate_id=?",(candidate_id,)).fetchone()
            result=dict(fresh); self._commit_request(db,identity,request_id,fingerprint,result); db.commit()
        self._write_status_projection()
        return result

    def _reject_candidate(self, scope: AttentionScope, candidate_id: str) -> None:
        identity=self._identity(scope)
        with closing(self._db()) as db:
            db.execute("UPDATE candidates SET state='rejected',revision=revision+1,updated_at=? WHERE candidate_id=? AND principal=? AND domain=? AND visibility=? AND state IN ('prepared','tested','canaried')",
                (now_utc(self.clock()).isoformat(),candidate_id,*identity))


    def test(self, scope: AttentionScope, candidate_id: str, request_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        if row["state"]!="prepared": raise ImprovementError("prepared candidate required")
        before=row["before_json"].encode(); after=row["after_json"].encode()
        try: receipt=self.evaluator.evaluate(row["source_revision"],before,after,row["allowed_class"])
        except Exception:
            self._reject_candidate(scope,candidate_id)
            raise
        if not isinstance(receipt,Mapping) or receipt.get("passed") is not True or receipt.get("private_data_used") is not False:
            self._reject_candidate(scope,candidate_id)
            raise ImprovementError("independent replay or fixed validation failed")
        signature=hmac.new(self.receipt_key,canonical(receipt),hashlib.sha256).hexdigest()
        return self._transition(scope,candidate_id,"prepared","tested",request_id,{"test_receipt_json":{"receipt":dict(receipt),"controller_hmac":signature}})

    def canary(self, scope: AttentionScope, candidate_id: str, request_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        if row["state"]!="tested": raise ImprovementError("independently tested candidate required")
        receipt=json.loads(row["test_receipt_json"])
        expected=hmac.new(self.receipt_key,canonical(receipt["receipt"]),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(receipt.get("controller_hmac",""),expected) or receipt["receipt"].get("passed") is not True:
            raise ImprovementError("controller-signed test evidence required")
        try: canary=self.evaluator.canary(row["source_revision"],row["before_json"].encode(),row["after_json"].encode(),row["allowed_class"],3)
        except Exception:
            self._reject_candidate(scope,candidate_id)
            raise
        if not isinstance(canary,Mapping) or canary.get("passed") is not True or len(canary.get("rounds",[]))!=3:
            self._reject_candidate(scope,candidate_id)
            raise ImprovementError("controller canary quality guard failed")
        signed=hmac.new(self.receipt_key,canonical(canary),hashlib.sha256).hexdigest()
        return self._transition(scope,candidate_id,"tested","canaried",request_id,{"canary_receipt_json":{"receipt":dict(canary),"controller_hmac":signed}})

    def _grant(self, scope: AttentionScope) -> dict[str, Any]:
        identity=self._identity(scope)
        for path in (self.grant_file,self.grant_key_file):
            if path.is_symlink(): raise ImprovementError("grant symlink refused")
            info=path.stat()
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600: raise ImprovementError("standing grant files must be private to controller identity")
        raw=self.grant_file.read_bytes()
        if len(raw)>4096: raise ImprovementError("standing grant exceeds bound")
        value=parse_json(raw)
        if not isinstance(value,dict): raise ImprovementError("standing grant object required")
        signature=value.pop("signature",None)
        key=self.grant_key_file.read_bytes()
        if not 32<=len(key)<=128: raise ImprovementError("bounded external grant verification key required")
        expected=hmac.new(key,canonical(value),hashlib.sha256).hexdigest()
        if not isinstance(signature,str) or not hmac.compare_digest(signature,expected): raise ImprovementError("valid external operator standing grant required")
        exact={"schema_version","grant_id","principal_scope","data_domain","visibility","allowed_classes","issued_at","expires_at","max_candidates"}
        if set(value)!=exact or value["schema_version"]!=1 or not re.fullmatch(r"[a-f0-9]{32}",value["grant_id"]): raise ImprovementError("exact standing grant schema required")
        if (value["principal_scope"],value["data_domain"],value["visibility"])!=identity: raise ImprovementError("grant scope mismatch")
        if not isinstance(value["allowed_classes"],list) or not value["allowed_classes"] or set(value["allowed_classes"])-CLASSES: raise ImprovementError("grant class allowlist required")
        if type(value["max_candidates"]) is not int or not 1<=value["max_candidates"]<=10: raise ImprovementError("standing grant quota exceeds the controller cap")
        now=now_utc(self.clock()); issued=now_utc(value["issued_at"]); expires=now_utc(value["expires_at"])
        if issued>now+timedelta(minutes=5) or expires<=now or expires>issued+timedelta(days=90): raise ImprovementError("standing grant is expired or invalid")
        return value

    def promote(self, scope: AttentionScope, candidate_id: str, request_id: str) -> dict[str, Any]:
        if not UUID4.fullmatch(request_id): raise ImprovementError("UUID4 request ID required")
        row=self._get(scope,candidate_id)
        if row["state"]!="canaried": raise ImprovementError("passing canary required before approval")
        for field in ("test_receipt_json","canary_receipt_json"):
            signed=json.loads(row[field]); expected=hmac.new(self.receipt_key,canonical(signed["receipt"]),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signed.get("controller_hmac",""),expected) or signed["receipt"].get("passed") is not True: raise ImprovementError("controller evidence verification failed")
        grant=self._grant(scope)
        if row["allowed_class"] not in grant["allowed_classes"]: raise ImprovementError("candidate class is outside standing grant")
        identity=self._identity(scope)
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            replay=db.execute("SELECT request_hash,response_json FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?",(*identity,request_id)).fetchone()
            fingerprint=sha(canonical(["approved",candidate_id,{"grant_id":grant["grant_id"]}]))
            if replay:
                if replay[0]!=fingerprint: db.rollback(); raise ImprovementError("request ID reused")
                result=json.loads(replay[1]); db.commit(); return result
            fresh=db.execute("SELECT * FROM candidates WHERE candidate_id=? AND principal=? AND domain=? AND visibility=?",(candidate_id,*identity)).fetchone()
            if not fresh or fresh["state"]!="canaried": db.rollback(); raise ImprovementError("candidate stage conflict")
            count=db.execute("SELECT COUNT(DISTINCT candidate_id) FROM candidates WHERE grant_id=? AND state IN ('approved','publishing','published','reconciled','applied','rolled_back')",(grant["grant_id"],)).fetchone()[0]
            if count>=grant["max_candidates"]: db.rollback(); raise ImprovementError("standing grant candidate quota exhausted")
            updates={"grant_id":grant["grant_id"],"state":"approved","revision":fresh["revision"]+1,"updated_at":now_utc(self.clock()).isoformat()}
            db.execute("UPDATE candidates SET grant_id=?,state='approved',revision=?,updated_at=? WHERE candidate_id=?",(grant["grant_id"],updates["revision"],updates["updated_at"],candidate_id))
            data=db.execute("SELECT candidate_id,allowed_class,state,revision,source_revision,before_sha,after_sha,branch,head_sha,merged_revision,published,applied,grant_id,updated_at FROM candidates WHERE candidate_id=?",(candidate_id,)).fetchone()
            result=dict(data); self._commit_request(db,identity,request_id,fingerprint,result); db.commit()
        self._write_status_projection()
        return result

    def publish(self, scope: AttentionScope, candidate_id: str, request_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        if row["state"] not in {"approved","publishing"} or not row["grant_id"]: raise ImprovementError("approved candidate and standing grant required")
        # Recheck the source config is byte-identical at the selected trusted revision before creating a branch.
        before=self._source_bytes(row["source_revision"])
        latest_before=self._source_bytes(self._base_revision())
        allowed_latest={row["before_sha"]} if row["state"]=="approved" else {row["before_sha"],row["after_sha"]}
        if sha(before)!=row["before_sha"] or sha(latest_before) not in allowed_latest:
            raise ImprovementError("trusted source config changed after candidate preparation")
        if row["state"]=="approved":
            intent=self._transition(scope,candidate_id,"approved","publishing",str(uuid.uuid4()),{"branch":"codex/assistant-improvement-"+candidate_id})
        result=self.publisher.publish(row["source_revision"],candidate_id,before,row["after_json"].encode())
        return self._transition(scope,candidate_id,"publishing","published",request_id,{"branch":result["branch"],"head_sha":result["head_sha"],"published":1})

    def reconcile(self, scope: AttentionScope, candidate_id: str, request_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        if row["state"]!="published" or not row["branch"] or not row["head_sha"]: raise ImprovementError("published candidate required")
        evidence=self.reconciler.reconcile(row["branch"],row["head_sha"])
        if evidence.get("status")!="reconciled" or not FULL_SHA.fullmatch(evidence.get("merged_revision","")):
            raise ImprovementError("trusted merge reconciliation required")
        merged_source=self._source_bytes(evidence["merged_revision"])
        if sha(merged_source)!=row["after_sha"]: raise ImprovementError("merged source differs from the reviewed candidate patch")
        return self._transition(scope,candidate_id,"published","reconciled",request_id,{"merged_revision":evidence["merged_revision"]})

    def mark_applied(self, scope: AttentionScope, candidate_id: str, revision: str, request_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        if row["state"]!="reconciled" or row["merged_revision"]!=revision or not FULL_SHA.fullmatch(revision):
            raise ImprovementError("only the reconciled published full SHA may be marked applied")
        return self._transition(scope,candidate_id,"reconciled","applied",request_id,{"applied":1})

    def list_staged(self, scope: AttentionScope) -> list[dict[str, Any]]:
        identity=self._identity(scope)
        with closing(self._db()) as db:
            rows=db.execute("SELECT candidate_id,allowed_class,state,revision,source_revision,before_sha,after_sha,branch,head_sha,merged_revision,published,applied,grant_id FROM candidates WHERE principal=? AND domain=? AND visibility=? ORDER BY created_at",identity).fetchall()
        return [dict(row) for row in rows]

    def sweep(self, scope: AttentionScope, *, apply_reviewed: bool = False) -> list[dict[str, Any]]:
        """Advance eligible proposals, reconcile merged PRs, and optionally apply reviewed source."""
        self._identity(scope)
        if not self.enabled: return [{"status":"disabled","mode":"dry-run"}]
        results=[]
        for staged in self.list_staged(scope):
            if staged["state"] in {"prepared","tested","canaried","approved","publishing"}:
                try:
                    cid=staged["candidate_id"]
                    if staged["state"]=="prepared":
                        staged=self.test(scope,cid,str(uuid.uuid4()))
                    if staged["state"]=="tested":
                        staged=self.canary(scope,cid,str(uuid.uuid4()))
                    if staged["state"]=="canaried":
                        staged=self.promote(scope,cid,str(uuid.uuid4()))
                    staged=self.publish(scope,staged["candidate_id"],str(uuid.uuid4()))
                    results.append({"candidate_id":staged["candidate_id"],"state":staged["state"],"branch":staged["branch"],"head_sha":staged["head_sha"]})
                except Exception as error:
                    results.append({"candidate_id":staged["candidate_id"],"status":"publication_pending","reason":type(error).__name__})
                continue
            if staged["state"]!="published": continue
            try:
                result=self.reconcile(scope,staged["candidate_id"],str(uuid.uuid4()))
                if apply_reviewed:
                    installer=PolicyInstaller(self)
                    current=installer._target_bytes()
                    if current is None: raise ImprovementError("managed target is not initialized")
                    plan=installer.plan_candidate(result["merged_revision"],staged["candidate_id"],sha(current))
                    result=installer.apply(plan["transaction_id"],plan["plan_sha256"])
                results.append({"candidate_id":staged["candidate_id"],"state":result.get("state",result.get("status")),
                                "merged_revision":result.get("merged_revision"),"applied":result.get("status")=="applied"})
            except Exception as error:
                results.append({"candidate_id":staged["candidate_id"],"status":"queued_or_held","reason":type(error).__name__})
        try: source_sha=sha(self._source_bytes(self._base_revision()))
        except ImprovementError:
            return results+[{"status":"held","reason":"trusted_source_unavailable"}]
        existing={row["candidate_id"] for row in self.list_staged(scope)}
        for candidate in self.attention.list_candidates(scope=scope,limit=100):
            if candidate.get("state")!="proposed" or candidate.get("allowed_class") not in CLASSES: continue
            cid=candidate.get("candidate_id")
            if cid in existing: continue
            try:
                self.prepare(scope,cid,source_sha,str(uuid.uuid4()))
                self.test(scope,cid,str(uuid.uuid4()))
                self.canary(scope,cid,str(uuid.uuid4()))
                self.promote(scope,cid,str(uuid.uuid4()))
                result=self.publish(scope,cid,str(uuid.uuid4()))
                results.append({"candidate_id":cid,"allowed_class":candidate["allowed_class"],"state":result["state"],"branch":result["branch"],"head_sha":result["head_sha"]})
            except Exception as error:
                results.append({"candidate_id":cid,"allowed_class":candidate.get("allowed_class"),"status":"held","reason":type(error).__name__})
        return results

    def get(self, scope: AttentionScope, candidate_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        return {key:row[key] for key in ("candidate_id","allowed_class","state","revision","source_revision","before_sha","after_sha","branch","head_sha","merged_revision","published","applied","grant_id","updated_at")}

    def _write_status_projection(self)->None:
        identity=("owner","personal","private")
        with closing(self._db()) as db:
            rows=db.execute("SELECT candidate_id,allowed_class,state,revision,source_revision,before_sha,after_sha,branch,head_sha,merged_revision,published,applied,grant_id,updated_at FROM candidates WHERE principal=? AND domain=? AND visibility=? ORDER BY created_at",identity).fetchall()
        value={"schema_version":1,"updated_at":now_utc(self.clock()).isoformat(),"candidates":[dict(row) for row in rows]}
        raw=canonical(value)
        if len(raw)>32768: raise ImprovementError("sanitized controller status projection exceeds bound")
        parent=self.status_path.parent
        if self.test_mode: parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        elif not parent.exists(): return
        if parent.is_symlink(): raise ImprovementError("status projection directory symlink refused")
        if not self.test_mode:
            group=grp.getgrnam("homecompute-context").gr_gid
            info=parent.stat()
            if info.st_uid!=0 or info.st_gid!=group or stat.S_IMODE(info.st_mode)!=0o750:
                raise ImprovementError("status projection directory must be root:homecompute-context 0750")
        tmp=parent/(".improvements-status."+uuid.uuid4().hex)
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try: os.write(fd,raw); os.fsync(fd)
        finally: os.close(fd)
        os.replace(tmp,self.status_path)
        if not self.test_mode:
            os.chown(self.status_path,0,grp.getgrnam("homecompute-context").gr_gid); os.chmod(self.status_path,0o640)
        else: os.chmod(self.status_path,0o600)

    @staticmethod
    def read_status_projection(path:Path=STATUS_PATH,*,strict:bool=True)->dict[str,Any]:
        path=Path(path)
        if strict and path!=STATUS_PATH: raise ImprovementError("status projection path is fixed")
        if path.is_symlink(): raise ImprovementError("status projection symlink refused")
        try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except OSError as error: raise ImprovementError("improvement status is unavailable") from error
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>32768 or info.st_nlink!=1:
                raise ImprovementError("invalid bounded status projection")
            if strict:
                group=grp.getgrnam("homecompute-context").gr_gid
                if info.st_uid!=0 or info.st_gid!=group or stat.S_IMODE(info.st_mode)!=0o640:
                    raise ImprovementError("status projection owner/mode mismatch")
            raw=os.read(fd,32769)
        finally: os.close(fd)
        if len(raw)>32768: raise ImprovementError("status projection exceeds bound")
        value=parse_json(raw)
        fields={"candidate_id","allowed_class","state","revision","source_revision","before_sha","after_sha","branch","head_sha","merged_revision","published","applied","grant_id","updated_at"}
        if set(value)!={"schema_version","updated_at","candidates"} or value["schema_version"]!=1 or not isinstance(value["candidates"],list):
            raise ImprovementError("status projection schema mismatch")
        for row in value["candidates"]:
            if not isinstance(row,dict) or set(row)!=fields or row["allowed_class"] not in CLASSES:
                raise ImprovementError("status projection contains unapproved fields")
        return value

    def mark_rolled_back(self, scope: AttentionScope, candidate_id: str, request_id: str) -> dict[str, Any]:
        row=self._get(scope,candidate_id)
        if row["state"]!="applied": raise ImprovementError("only an applied candidate may be rolled back")
        return self._transition(scope,candidate_id,"applied","rolled_back",request_id,{"applied":0})


class SystemdPolicyOps:
    UNIT="homecompute-assistant-context.service"
    HEALTH="http://127.0.0.1:18816/v1/health"
    HEALTH_TOKEN=Path("/run/credentials/homecompute-assistant-context-improvements.service/policy-health-token")
    EXTERNAL_HEALTH_TOKEN=Path("/etc/homecompute/assistant-context-policy-health-token")
    def __init__(self, runner: Callable[..., Any] = subprocess.run, *, token_file: Path | None = None, test_mode: bool = False):
        self.runner=runner; self.test_mode=test_mode
        if token_file is not None: self.token_file=Path(token_file)
        else:
            credentials=Path(os.environ.get("CREDENTIALS_DIRECTORY","")) if os.environ.get("CREDENTIALS_DIRECTORY") else None
            projected=credentials/"policy-health-token" if credentials else self.HEALTH_TOKEN
            self.token_file=projected if projected.is_file() else self.EXTERNAL_HEALTH_TOKEN
    def _run(self,args):
        result=self.runner(["systemctl",*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=30,
            env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"LANG":"C.UTF-8","LC_ALL":"C.UTF-8"})
        if result.returncode: raise ImprovementError("systemd operation failed: "+args[0])
        return result.stdout.decode().strip()
    def exec_start(self): return self._run(["show",self.UNIT,"--property=ExecStart","--value"])
    def active(self): return self._run(["is-active",self.UNIT])=="active"
    def stop(self): self._run(["stop",self.UNIT])
    def start(self): self._run(["start",self.UNIT])
    def health(self):
        from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler
        if self.token_file.is_symlink() or not self.token_file.is_file():
            raise ImprovementError("separate operator health credential unavailable")
        if not self.test_mode and self.token_file not in {self.HEALTH_TOKEN,self.EXTERNAL_HEALTH_TOKEN}:
            raise ImprovementError("health credential path is not a configured operator secret")
        info=self.token_file.stat()
        if (not self.test_mode and info.st_uid!=0) or stat.S_IMODE(info.st_mode) not in ({0o400,0o600} if self.test_mode else {0o400}) or info.st_size>4096:
            raise ImprovementError("health credential must be a root-only systemd credential")
        token=self.token_file.read_text(encoding="utf-8").strip()
        if not token or "\n" in token or "\r" in token: raise ImprovementError("health credential malformed")
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=build_opener(ProxyHandler({}),NoRedirect())
        from urllib.request import Request
        request=Request(self.HEALTH,headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
        with opener.open(request,timeout=3) as response:
            raw=response.read(4097)
        value=parse_json(raw)
        return isinstance(value,dict) and value.get("ready") is True


class PolicyInstaller:
    """Hash-bound initializer/apply/rollback for only the managed briefing JSON."""
    def __init__(self, controller: ImprovementController, *, repo: Path = ROOT,
                 state_dir: Path = CONTROLLER_ROOT, target: Path = TARGET_PATH,
                 ops: Any | None = None, test_mode: bool = False,
                 system: str | None = None, euid: int | None = None,
                 backup_receipt: Path = Path("/var/lib/homecompute/nas-recovery-backup/last-backup.json"),
                 restore_receipt: Path = Path("/var/lib/homecompute/nas-recovery-backup/last-application-verification.json")):
        self.controller,self.repo,self.state=controller,Path(repo),Path(state_dir)
        if not test_mode and Path(target)!=TARGET_PATH: raise ImprovementError("managed policy target is fixed")
        self.target=Path(target); self.ops=ops or SystemdPolicyOps(); self.test_mode=test_mode
        self.system=system or platform.system(); self.euid=os.geteuid() if euid is None else euid
        self.backup_receipt,self.restore_receipt=Path(backup_receipt),Path(restore_receipt)
        if not test_mode and (self.backup_receipt!=Path("/var/lib/homecompute/nas-recovery-backup/last-backup.json")
                or self.restore_receipt!=Path("/var/lib/homecompute/nas-recovery-backup/last-application-verification.json")):
            raise ImprovementError("recovery receipt sources are fixed")
        self.transactions=self.state/"policy-transactions"
        self.transactions.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.transactions.stat()
        if self.transactions.is_symlink() or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ImprovementError("private policy transaction state required")

    def _host_guard(self):
        if not self.test_mode and (self.system!="Linux" or self.euid!=0): raise ImprovementError("policy apply requires root on Linux home-core")

    def _read_revision(self,revision:str)->bytes:
        if not FULL_SHA.fullmatch(revision): raise ImprovementError("published full SHA required")
        env=_env(str(self.repo))
        ancestor=subprocess.run(["git","-C",str(self.repo),"merge-base","--is-ancestor",revision,BASE_REF],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,check=False,timeout=15)
        if ancestor.returncode: raise ImprovementError("policy source must be on origin/master")
        result=subprocess.run(["git","-C",str(self.repo),"show",revision+":"+SOURCE_FILE],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,check=False,timeout=15)
        if result.returncode: raise ImprovementError("published briefing policy unavailable")
        load_briefing_config_bytes(result.stdout)
        return result.stdout

    def _is_active_selector(self)->bool:
        command=self.ops.exec_start()
        # Parent Nix option explicitly adds this exact managed source argument.
        return "--briefings "+str(self.target) in command or "--briefings="+str(self.target) in command

    def _target_bytes(self)->bytes | None:
        if not self.target.exists(): return None
        if self.target.is_symlink(): raise ImprovementError("managed target symlink refused")
        info=self.target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise ImprovementError("managed target regular file required")
        if not self.test_mode:
            group=grp.getgrnam("homecompute-context").gr_gid
            if info.st_uid!=0 or info.st_gid!=group or stat.S_IMODE(info.st_mode)!=0o640:
                raise ImprovementError("managed target ownership/mode drift refused")
        raw=self.target.read_bytes()
        if len(raw)>65536: raise ImprovementError("managed policy size exceeds bound")
        load_briefing_config_bytes(raw)
        return raw

    def _save_plan(self,plan:dict[str,Any])->dict[str,Any]:
        txid=plan["transaction_id"]; directory=self.transactions/txid
        directory.mkdir(mode=0o700)
        path=directory/"plan.json"; raw=canonical(plan)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try: os.write(fd,raw); os.fsync(fd)
        finally: os.close(fd)
        return {"transaction_id":txid,"plan_sha256":sha(raw),"source_revision":plan["source_revision"],
                "candidate_id":plan.get("candidate_id"),"before_sha256":plan["before_sha256"],
                "after_sha256":plan["after_sha256"],"kind":plan["kind"],"status":"planned"}

    def plan_initialize(self,revision:str)->dict[str,Any]:
        self._host_guard()
        current=self._target_bytes()
        if current is not None: raise ImprovementError("initialization refuses an existing managed target")
        if self._is_active_selector(): raise ImprovementError("existing managed selector contradicts missing target")
        after=self._read_revision(revision)
        plan={"schema_version":1,"transaction_id":str(uuid.uuid4()),"kind":"initialize",
            "source_revision":revision,"target":str(self.target),"candidate_id":None,
            "before_sha256":"absent","after_sha256":sha(after),"before_b64":None,
            "after_b64":__import__("base64").b64encode(after).decode(),"activation_required":True,
            "created_at":now_utc().isoformat()}
        return self._save_plan(plan)

    def _verify_recovery_gate(self,revision:str,preimage_sha:str)->None:
        if not FULL_SHA.fullmatch(revision) or not re.fullmatch(r"[a-f0-9]{64}",preimage_sha):
            raise ImprovementError("source and target preimage binding required")
        if self.test_mode and not self.backup_receipt.exists() and not self.restore_receipt.exists(): return
        for path in (self.backup_receipt,self.restore_receipt):
            if path.is_symlink() or not path.is_file(): raise ImprovementError("trusted backup and restore receipts required")
            info=path.stat()
            if info.st_uid not in {0,os.geteuid()} or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>65536:
                raise ImprovementError("backup and restore receipts must be private root-owned files")
        backup=parse_json(self.backup_receipt.read_bytes())
        restored=parse_json(self.restore_receipt.read_bytes())
        proof=restored.get("verification") if isinstance(restored,dict) else None
        current=now_utc(self.controller.clock())
        archive_pattern=r"homecompute-applications-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z\.tar"
        if (not isinstance(backup,dict) or not re.fullmatch(r"[a-f0-9]{64}",str(backup.get("snapshot","")))
                or backup.get("scope")!="/srv/backups/agent-recovery" or not isinstance(restored,dict)
                or restored.get("snapshot")!=backup.get("snapshot") or not isinstance(proof,dict)
                or proof.get("snapshot")!=backup.get("snapshot") or proof.get("production_modified") is not False
                or proof.get("postgres_import_verified") is not True or proof.get("vm_boot_verified") is not True
                or proof.get("application_services_verified") is not False
                or not re.fullmatch(r"[0-9a-f]{64}",str(proof.get("manifest_sha256","")))
                or not re.fullmatch(archive_pattern,str(restored.get("archive","")))):
            raise ImprovementError("matching successful non-production recovery proof required")
        proof_revision=proof.get("source_revision")
        if (not FULL_SHA.fullmatch(str(proof_revision or ""))
                or self.backup_receipt.stat().st_mtime<current.timestamp()-timedelta(days=7).total_seconds()
                or self.restore_receipt.stat().st_mtime<current.timestamp()-timedelta(days=30).total_seconds()
                or self.backup_receipt.stat().st_mtime>current.timestamp()+300
                or self.restore_receipt.stat().st_mtime>current.timestamp()+300):
            raise ImprovementError("backup or isolated restore evidence is stale or mismatched")
        self._read_revision(proof_revision)
        ancestor=subprocess.run(["git","-C",str(self.repo),"merge-base","--is-ancestor",proof_revision,revision],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=_env(str(self.repo)),check=False,timeout=15)
        if ancestor.returncode: raise ImprovementError("restore qualification source must precede the selected reviewed revision")
        # The immutable transaction independently binds this selected source SHA and target preimage.

    def plan_candidate(self,revision:str,candidate_id:str,expected_installed_sha:str)->dict[str,Any]:
        self._host_guard()
        candidate=self.controller.get(AttentionScope("owner","personal","private"),candidate_id)
        if candidate["state"]!="reconciled" or candidate["merged_revision"]!=revision or candidate["after_sha"] is None:
            raise ImprovementError("candidate must be reconciled to the selected published SHA")
        if not self._is_active_selector(): raise ImprovementError("context ExecStart does not select the managed policy target")
        before=self._target_bytes()
        if before is None or sha(before)!=expected_installed_sha: raise ImprovementError("installed policy preimage mismatch")
        self._verify_recovery_gate(revision,sha(before))
        after=self._read_revision(revision)
        if sha(after)!=candidate["after_sha"]: raise ImprovementError("published file does not match reconciled candidate")
        plan={"schema_version":1,"transaction_id":str(uuid.uuid4()),"kind":"apply",
            "source_revision":revision,"target":str(self.target),"candidate_id":candidate_id,
            "before_sha256":sha(before),"after_sha256":sha(after),
            "before_b64":__import__("base64").b64encode(before).decode(),
            "after_b64":__import__("base64").b64encode(after).decode(),"activation_required":True,
            "created_at":now_utc().isoformat()}
        return self._save_plan(plan)

    def _plan(self,transaction_id:str,expected_plan_sha:str)->dict[str,Any]:
        if not re.fullmatch(r"[0-9a-f-]{36}",transaction_id) or not re.fullmatch(r"[a-f0-9]{64}",expected_plan_sha):
            raise ImprovementError("transaction ID and plan hash required")
        path=self.transactions/transaction_id/"plan.json"
        if path.is_symlink() or not path.exists(): raise ImprovementError("saved policy plan unavailable")
        raw=path.read_bytes()
        if sha(raw)!=expected_plan_sha: raise ImprovementError("policy plan hash mismatch")
        plan=parse_json(raw)
        if plan.get("schema_version")!=1 or plan.get("target")!=str(self.target): raise ImprovementError("plan target mismatch")
        return plan

    def _result(self,transaction_id:str)->dict[str,Any] | None:
        path=self.transactions/transaction_id/"result.json"
        if not path.exists(): return None
        if path.is_symlink(): raise ImprovementError("transaction result symlink refused")
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>8192:
            raise ImprovementError("invalid transaction result")
        value=parse_json(path.read_bytes())
        if not isinstance(value,dict) or value.get("transaction_id")!=transaction_id:
            raise ImprovementError("transaction result binding mismatch")
        return value

    def _atomic_write(self,raw:bytes | None):
        self.target.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
        if not self.test_mode:
            group=grp.getgrnam("homecompute-context").gr_gid
            directory=self.target.parent.stat()
            if directory.st_uid!=0 or directory.st_gid!=group or stat.S_IMODE(directory.st_mode)!=0o750:
                raise ImprovementError("managed target directory must be root:homecompute-context 0750")
        if raw is None:
            self.target.unlink(missing_ok=True); return
        load_briefing_config_bytes(raw)
        temp=self.target.parent/(".assistant-briefings."+uuid.uuid4().hex)
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            os.write(fd,raw); os.fsync(fd)
        finally: os.close(fd)
        os.replace(temp,self.target)
        if not self.test_mode:
            os.chown(self.target,0,grp.getgrnam("homecompute-context").gr_gid)
            os.chmod(self.target,0o640)
        dirfd=os.open(self.target.parent,os.O_RDONLY); os.fsync(dirfd); os.close(dirfd)

    def apply(self,transaction_id:str,expected_plan_sha:str)->dict[str,Any]:
        self._host_guard(); plan=self._plan(transaction_id,expected_plan_sha)
        if plan["kind"]=="initialize":
            if self._is_active_selector(): raise ImprovementError("safe initialization preconditions changed")
            after=__import__("base64").b64decode(plan["after_b64"],validate=True)
            if sha(after)!=plan["after_sha256"] or sha(self._read_revision(plan["source_revision"]))!=plan["after_sha256"]:
                raise ImprovementError("initialization source changed")
            existing=self._target_bytes()
            if existing is not None:
                if sha(existing)!=plan["after_sha256"]: raise ImprovementError("safe initialization target drifted")
                result={"transaction_id":transaction_id,"status":"initialized_not_active","target":str(self.target),"sha256":sha(after)}
                self._write_result(transaction_id,result); return result
            self._atomic_write(after)
            result={"transaction_id":transaction_id,"status":"initialized_not_active","target":str(self.target),"sha256":sha(after)}
            self._write_result(transaction_id,result); return result
        if plan["kind"]!="apply" or not self._is_active_selector(): raise ImprovementError("managed policy activation precondition failed")
        before=__import__("base64").b64decode(plan["before_b64"],validate=True)
        after=__import__("base64").b64decode(plan["after_b64"],validate=True)
        current=self._target_bytes()
        current_sha=sha(current) if current is not None else None
        if current_sha not in {plan["before_sha256"],plan["after_sha256"]}: raise ImprovementError("installed policy drift after planning")
        if sha(after)!=plan["after_sha256"] or sha(self._read_revision(plan["source_revision"]))!=plan["after_sha256"]:
            raise ImprovementError("published policy changed after planning")
        self._verify_recovery_gate(plan["source_revision"],plan["before_sha256"])
        prior=self._result(transaction_id)
        if prior and prior.get("status")=="applied":
            if current_sha!=plan["after_sha256"] or self.controller.get(AttentionScope("owner","personal","private"),plan["candidate_id"])["state"] not in {"applied","rolled_back"}:
                raise ImprovementError("completed apply transaction drifted")
            return prior
        if prior and prior.get("status") in {"rollback_pending","recovery_required"}:
            return self._restore_preimage(transaction_id,plan,before,after,prior)
        if prior and prior.get("status")=="rolled_back": return prior
        if prior is None:
            if current_sha!=plan["before_sha256"] or not self.ops.active():
                raise ImprovementError("context service must be active before a new guarded apply")
            prior={"transaction_id":transaction_id,"status":"applying","source_revision":plan["source_revision"],
                "candidate_id":plan["candidate_id"],"before_sha256":plan["before_sha256"],"after_sha256":plan["after_sha256"]}
            self._write_result(transaction_id,prior)
        elif prior.get("status") not in {"applying","stopping","stopped","installed","starting"}:
            raise ImprovementError("transaction is not resumable")
        try:
            if current_sha==plan["before_sha256"]:
                if self.ops.active():
                    prior={**prior,"status":"stopping"}; self._write_result(transaction_id,prior)
                    self.ops.stop()
                prior={**prior,"status":"stopped"}; self._write_result(transaction_id,prior)
                self._atomic_write(after)
                prior={**prior,"status":"installed"}; self._write_result(transaction_id,prior)
            if not self.ops.active():
                prior={**prior,"status":"starting"}; self._write_result(transaction_id,prior)
                self.ops.start()
            if not self.ops.health(): raise ImprovementError("context service health check failed")
        except Exception as error:
            pending={**prior,"status":"rollback_pending","error":type(error).__name__}; self._write_result(transaction_id,pending)
            return self._restore_preimage(transaction_id,plan,before,after,pending)
        candidate=self.controller.get(AttentionScope("owner","personal","private"),plan["candidate_id"])
        if candidate["state"]=="reconciled":
            self.controller.mark_applied(AttentionScope("owner","personal","private"),plan["candidate_id"],plan["source_revision"],str(uuid.uuid4()))
        elif candidate["state"]!="applied": raise ImprovementError("controller candidate state conflicts with installed policy")
        result={"transaction_id":transaction_id,"status":"applied","source_revision":plan["source_revision"],"sha256":sha(after)}
        self._write_result(transaction_id,result); return result

    def _restore_preimage(self,transaction_id:str,plan:dict[str,Any],before:bytes,after:bytes,prior:dict[str,Any])->dict[str,Any]:
        try:
            current=self._target_bytes()
            if current is None or sha(current) not in {plan["before_sha256"],plan["after_sha256"]}:
                raise ImprovementError("recovery target no longer matches transaction hashes")
            if sha(current)!=plan["before_sha256"]:
                if self.ops.active(): self.ops.stop()
                self._atomic_write(before)
            if not self.ops.active(): self.ops.start()
            if not self.ops.health(): raise ImprovementError("rollback health failed")
        except Exception as rollback_error:
            result={"transaction_id":transaction_id,"status":"recovery_required","error":prior.get("error","apply_failed"),
                "rollback_error":type(rollback_error).__name__}
            self._write_result(transaction_id,result); return result
        candidate=self.controller.get(AttentionScope("owner","personal","private"),plan["candidate_id"])
        if candidate["state"]=="applied":
            self.controller.mark_rolled_back(AttentionScope("owner","personal","private"),plan["candidate_id"],str(uuid.uuid4()))
        result={"transaction_id":transaction_id,"status":"rolled_back","error":prior.get("error")}
        self._write_result(transaction_id,result); return result

    def rollback(self,transaction_id:str,expected_installed_sha:str)->dict[str,Any]:
        self._host_guard()
        if not re.fullmatch(r"[0-9a-f-]{36}",transaction_id): raise ImprovementError("transaction UUID required")
        plan_path=self.transactions/transaction_id/"plan.json"
        if plan_path.is_symlink() or not plan_path.exists(): raise ImprovementError("transaction unavailable")
        plan=parse_json(plan_path.read_bytes())
        if (not isinstance(plan,dict) or plan.get("transaction_id")!=transaction_id or plan.get("target")!=str(self.target)
                or plan.get("kind")!="apply" or not self._is_active_selector()):
            raise ImprovementError("only an active managed policy transaction can roll back")
        prior=self._result(transaction_id)
        if prior and prior.get("status")=="rolled_back":
            current=self._target_bytes()
            if current is None or sha(current)!=plan.get("before_sha256"):
                raise ImprovementError("stale rollback refused; installed target diverged")
            return prior
        if prior and prior.get("status") in {"rollback_pending","recovery_required"}:
            before=__import__("base64").b64decode(plan["before_b64"],validate=True)
            after=__import__("base64").b64decode(plan["after_b64"],validate=True)
            current=self._target_bytes()
            if current is None or sha(current) not in {plan["before_sha256"],plan["after_sha256"]}:
                raise ImprovementError("rollback recovery target diverged")
            return self._restore_preimage(transaction_id,plan,before,after,prior)
        current=self._target_bytes()
        if current is None or sha(current)!=expected_installed_sha or expected_installed_sha!=plan["after_sha256"]:
            raise ImprovementError("stale rollback refused; installed target diverged")
        before=__import__("base64").b64decode(plan["before_b64"],validate=True)
        prior={"transaction_id":transaction_id,"status":"rollback_pending","source_revision":plan["source_revision"],
            "candidate_id":plan["candidate_id"],"before_sha256":plan["before_sha256"],"after_sha256":plan["after_sha256"]}
        self._write_result(transaction_id,prior)
        result=self._restore_preimage(transaction_id,plan,before,__import__("base64").b64decode(plan["after_b64"],validate=True),prior)
        if result["status"]=="rolled_back": result["sha256"]=sha(before); self._write_result(transaction_id,result)
        return result

    def _write_result(self,transaction_id:str,result:dict[str,Any]):
        path=self.transactions/transaction_id/"result.json"
        raw=canonical(result); temp=path.with_suffix(".tmp")
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        try: os.write(fd,raw); os.fsync(fd)
        finally: os.close(fd)
        os.replace(temp,path)


def load_briefing_config_bytes(raw:bytes)->dict[str,Any]:
    """Use the actual source validator while keeping filesystem selection fixed."""
    with tempfile.NamedTemporaryFile(prefix="assistant-policy-check-",suffix=".json") as handle:
        handle.write(raw); handle.flush()
        return load_briefing_config(Path(handle.name))
