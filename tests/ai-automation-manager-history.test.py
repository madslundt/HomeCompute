import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/ai-automation-manager/backend"))
import app as manager


ORIGIN = "https://automations.example.test"
WORKER = {"Authorization": "Bearer fixture-workflow-token"}
ITEM = {
    "name": "Synthetic briefing",
    "task_description": "Summarize the fixture",
    "status": "Ready",
    "cadence": "time_slot",
    "days_to_run": ["Friday"],
    "time_to_run": "Morning",
    "update_style": "briefing",
    "notify_when": "every_run",
    "completion_rule": "manual_archive",
    "recipients": ["fixture"],
}
SCHEDULE = {
    "local_date": "2026-11-20", "local_hour_key": "2026-11-20T07+0100",
    "weekday": "Friday", "slot": "Morning",
}
RESULT = {
    "updated_context": "Test context", "change_summary": "Test update",
    "new_status": "Ready", "changed": True, "action_needed": False,
}


class AutomationHistoryTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        token = root / "token"
        token.write_text("fixture-workflow-token", encoding="utf-8")
        self.enterContext(patch.multiple(
            manager, DATA_ROOT=root, DATABASE=root / "automations.sqlite3",
            TOKEN_FILE=token, PUBLIC_ORIGIN=ORIGIN,
        ))
        self.enterContext(patch.object(manager, "site_credentials", return_value=("owner", "fixture-password")))
        self.enterContext(patch.object(manager, "recipient_directory", return_value=[{"id": "fixture", "name": "Fixture"}]))
        self.client = self.enterContext(TestClient(manager.app, base_url=ORIGIN))
        login = self.client.post("/api/session", json={"username": "owner", "password": "fixture-password"})
        self.assertEqual(login.status_code, 200)
        self.site = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        self.id = self.create(ITEM)
        self.run_path = f"/api/automations/{self.id}/runs"

    def create(self, item):
        created = self.client.post("/api/automations", headers=self.site, json=item)
        self.assertEqual(created.status_code, 200)
        return created.json()["automation"]["id"]

    def worker(self, method, path, **kwargs):
        response = self.client.request(method, "/api/workflows/" + path, headers=WORKER, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def queue(self):
        queued = self.client.post(self.run_path, headers=self.site)
        self.assertEqual(queued.status_code, 202)
        return queued.json()["run"]

    def test_manual_claim_and_receipt_appear_in_run_history(self):
        run = self.queue()
        self.assertEqual(run["state"], "queued")
        self.assertEqual(run["source"], "manual")
        self.assertIsNone(run["started_at"])
        self.assertEqual(self.queue()["id"], run["id"])
        self.assertEqual(self.worker("GET", "manual-queue"), {"has_pending": True})
        self.assertEqual(self.worker("POST", "claim-manual")["items"][0]["run_id"], run["id"])
        self.assertEqual(self.worker("POST", "claim-manual"), {"items": []})
        self.assertEqual(self.worker("POST", "claim", json=SCHEDULE), {"items": []})
        self.assertEqual(self.client.post(self.run_path, headers=self.site).status_code, 409)
        self.worker("POST", run["id"] + "/result", json=RESULT)
        self.worker("POST", run["id"] + "/delivery", json={"state": "delivered", "receipt_id": "fixture"})
        recent = self.client.get(self.run_path).json()["runs"]
        self.assertEqual(recent[0]["delivery_receipt"], "fixture")
        self.assertEqual(recent[0]["automation_name"], ITEM["name"])
        self.assertEqual(recent[0]["state"], "delivered")
        self.assertNotIn("updated_context", recent[0])
        self.assertEqual(self.worker("POST", "claim", json=SCHEDULE)["items"][0]["id"], self.id)
        self.assertEqual(self.worker("POST", "claim", json=SCHEDULE), {"items": []})

    def test_requires_site_csrf_and_worker_token(self):
        self.assertEqual(self.client.post(self.run_path).status_code, 403)
        self.assertEqual(self.client.post("/api/workflows/claim-manual").status_code, 401)
        self.assertEqual(self.client.get("/api/workflows/manual-queue").status_code, 401)
        with TestClient(manager.app, base_url=ORIGIN) as unknown:
            self.assertEqual(unknown.get(self.run_path).status_code, 401)
            self.assertEqual(unknown.post(self.run_path).status_code, 401)
        draft = self.create(dict(ITEM, status="Draft"))
        self.assertEqual(self.client.post(f"/api/automations/{draft}/runs", headers=self.site).status_code, 409)

    def test_waits_for_scheduled_work_and_closes_unready_requests(self):
        queued = self.queue()
        scheduled = self.worker("POST", "claim", json=SCHEDULE)["items"][0]["run_id"]
        self.assertEqual(self.worker("POST", "claim-manual"), {"items": []})
        self.worker("POST", scheduled + "/failure", json={"error_code": "workflow_failed"})
        response = self.client.put(f"/api/automations/{self.id}", headers=self.site, json=dict(ITEM, status="Archived"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.worker("POST", "claim-manual"), {"items": []})
        recent = {run["id"]: run for run in self.client.get(self.run_path).json()["runs"]}
        self.assertEqual(recent[queued["id"]]["error_code"], "item_not_ready")
        self.assertEqual(recent[scheduled]["source"], "scheduled")

    def test_expired_run_cannot_overwrite_a_new_result(self):
        expired = self.worker("POST", "claim", json=SCHEDULE)["items"][0]["run_id"]
        with manager.connection() as database:
            database.execute("UPDATE automation_runs SET started_at='2026-01-01T00:00:00Z' WHERE id=?", (expired,))
        queued = self.queue()
        self.assertEqual(self.worker("POST", "claim-manual")["items"][0]["run_id"], queued["id"])
        rejected = self.client.post("/api/workflows/" + expired + "/result", headers=WORKER, json=RESULT)
        self.assertEqual(rejected.status_code, 409)
        rows = {run["id"]: run for run in self.client.get(self.run_path).json()["runs"]}
        self.assertEqual(rows[expired]["error_code"], "run_interrupted")


if __name__ == "__main__":
    unittest.main()
