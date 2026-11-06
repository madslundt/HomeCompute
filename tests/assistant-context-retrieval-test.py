"""Hybrid assistant context retrieval remains scope-bound and degradable."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from assistant_context_store import ContextStore, Scope
from assistant_context_retrieval import AssistantContextRetrieval, AuthenticatedHarrierClient
from embedding_client import DIMENSIONS


OWNER = Scope("owner", "personal", "private")
FAMILY = Scope("family", "household", "shared")


def event(object_id, *, source="daythread", revision=1, text="Pick up Sam at 15:00"):
    return {"schema_version": 1, "source": source, "object_id": object_id, "revision": revision,
            "event_type": "daythread.commitment", "operation": "upsert", "occurred_at": "2026-11-06T09:00:00Z",
            "source_ref": f"daythread:decision/{object_id}", "payload": {"text": text, "day": "2026-11-06"}}


class FakeEmbeddings:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise TimeoutError("synthetic outage")
        vectors = []
        for index, text in enumerate(texts):
            values = [0.0] * DIMENSIONS
            values[0 if "pick up" in text.lower() else (index % (DIMENSIONS - 1)) + 1] = 1.0
            vectors.append(values)
        return vectors


class Response:
    status = 200
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, _): return self.body


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        root.chmod(0o700)
        self.store = ContextStore(root / "context.db")
        self.cache = root / "retrieval.sqlite3"
        self.store.ingest(OWNER, event("diary:2026-11-06"))
        self.store.ingest(FAMILY, event("shared-1", text="Family picnic Saturday"))

    def tearDown(self):
        self.temp.cleanup()

    def test_scope_and_grant_filtering_precede_embedding_and_exact_ids_dates_survive(self):
        model = FakeEmbeddings()
        retrieval = AssistantContextRetrieval(self.store, self.cache, embedding_client=model)
        result = retrieval.search(OWNER, "diary:2026-11-06", source_grants=["daythread"])
        self.assertEqual([row["object_id"] for row in result["results"]], ["diary:2026-11-06"])
        self.assertNotIn("shared-1", str(model.calls))
        self.assertFalse(result["degraded"])

    def test_cache_is_scope_partitioned_and_invalidation_removes_vectors(self):
        model = FakeEmbeddings()
        retrieval = AssistantContextRetrieval(self.store, self.cache, embedding_client=model)
        retrieval.search(OWNER, "pickup", source_grants=["daythread"])
        retrieval.search(OWNER, "pickup", source_grants=["daythread"])
        self.assertEqual(len(model.calls), 2)  # Query only on warm cache.
        retrieval.search(FAMILY, "picnic", source_grants=["daythread"])
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(retrieval.invalidate(OWNER, "daythread", "diary:2026-11-06"), 1)

    def test_embedding_outage_returns_lexical_hits_with_explicit_degradation(self):
        retrieval = AssistantContextRetrieval(self.store, self.cache, embedding_client=FakeEmbeddings(fail=True))
        result = retrieval.search(OWNER, "2026-11-06", source_grants=["daythread"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation"], "lexical_only_embedding_unavailable")
        self.assertEqual(result["results"][0]["object_id"], "diary:2026-11-06")
        self.assertIsNone(result["results"][0]["semantic_score"])

    def test_source_revision_change_uses_new_cache_key_and_scope_purge_is_bounded(self):
        model = FakeEmbeddings()
        retrieval = AssistantContextRetrieval(self.store, self.cache, embedding_client=model)
        retrieval.search(OWNER, "pickup", source_grants=["daythread"])
        before = len(model.calls)
        self.store.ingest(OWNER, event("diary:2026-11-06", revision=2, text="Pickup changed to 16:00"))
        retrieval.search(OWNER, "pickup", source_grants=["daythread"])
        self.assertEqual(len(model.calls), before + 1)
        self.assertGreaterEqual(retrieval.purge_scope(OWNER), 1)
        self.assertEqual(retrieval.purge_scope(FAMILY), 0)

    def test_content_free_tombstone_ledger_fences_an_older_restored_store(self):
        self.store.ingest(OWNER, event("restore-id", text="private synthetic content"))
        self.store.ingest(OWNER, {**event("restore-id", revision=2), "operation": "delete", "payload": {}})
        ledger = self.store.export_tombstones(OWNER)
        self.assertEqual(ledger["source_tombstones"], [
            {"source": "daythread", "object_id": "restore-id", "min_revision": 2}])
        self.assertNotIn("private synthetic content", str(ledger))
        ledger = self.store.export_tombstones(OWNER)
        self.assertEqual(len(ledger["memory_source_ref_hashes"]), 1)
        backup = ContextStore(Path(self.temp.name) / "restored.db")
        backup.ingest(OWNER, event("restore-id", text="old archived content"))
        merged = backup.replay_tombstones(OWNER, ledger)
        self.assertEqual(merged, {"source_tombstones": 1, "memory_source_ref_hashes": 1})
        self.assertIsNone(backup.get(OWNER, "daythread", "restore-id"))
        replay = backup.ingest(OWNER, event("restore-id", text="old archived content"))
        self.assertIn(replay["status"], {"stale", "duplicate"})
        same_revision = backup.ingest(OWNER, event("restore-id", revision=2, text="same tombstone revision"))
        self.assertEqual(same_revision["status"], "stale")
        newer = backup.ingest(OWNER, event("restore-id", revision=3, text="newer source version"))
        self.assertEqual(newer["status"], "accepted")

    def test_authenticated_transport_uses_fixed_gateway_and_independent_bearer(self):
        class Opener:
            def __init__(self): self.request = None
            def open(self, request, timeout):
                self.request = request
                import json
                vector = [0.0] * DIMENSIONS
                vector[0] = 1.0
                return Response(json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode())
        token_file = Path(self.temp.name) / "harrier-token"
        token_file.write_text("synthetic-harrier-token-value-1234567890\n")
        token_file.chmod(0o400)  # systemd LoadCredential projection
        opener = Opener()
        client = AuthenticatedHarrierClient(token_file, opener=opener)
        client.embed(["synthetic query"])
        self.assertEqual(opener.request.full_url, "https://ai.home.arpa/v1/embeddings")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer synthetic-harrier-token-value-1234567890")
        self.assertEqual(opener.request.get_method(), "POST")
        token_file.chmod(0o640)
        with self.assertRaises(ValueError):
            AuthenticatedHarrierClient(token_file, opener=opener)


if __name__ == "__main__":
    unittest.main()
