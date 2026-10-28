#!/usr/bin/env python3
"""Exercise content stripping and calendar aggregation against PostgreSQL."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("token_usage", ROOT / "scripts/token-usage.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class ArgumentTests(unittest.TestCase):
    def test_rejects_untrusted_sql_period(self):
        with self.assertRaises(ValueError):
            usage.usage_query("day'); DROP TABLE test; --", False)

    def test_dashboard_publication_excludes_extra_fields_and_is_atomic(self):
        row = dict(period_start="2026-10-21", model="automation", requests=2,
                   input_tokens=190, output_tokens=64, total_tokens=254,
                   messages="PRIVATE_INPUT", metadata={"key": "PRIVATE_KEY"})
        report = dict(rows=[row], tracking_since="2026-10-21T07:00:00Z",
                      timezone="Europe/Copenhagen", response="PRIVATE_OUTPUT")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "usage.json"
            usage.publish_report(report, destination)
            original = destination.read_text()
            self.assertNotIn("PRIVATE_", original)
            self.assertEqual(254, json.loads(original)["rows"][0]["total_tokens"])
            row["total_tokens"] = "invalid"
            with self.assertRaises(ValueError):
                usage.publish_report(report, destination)
            self.assertEqual(original, destination.read_text())
            self.assertEqual([destination], list(Path(directory).iterdir()))


@unittest.skipUnless(shutil.which("docker"), "Docker required for PostgreSQL integration")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = subprocess.run(["docker", "image", "inspect", "postgres:16-alpine"], capture_output=True)
        if image.returncode:
            raise unittest.SkipTest("cached postgres:16-alpine required; never pulls images")
        cls.container = "homecompute-token-usage-test-" + uuid.uuid4().hex[:12]
        subprocess.run(["docker", "run", "--rm", "-d", "--network", "none", "--name", cls.container,
                        "--tmpfs", "/var/lib/postgresql/data", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
                        "-e", "POSTGRES_DB=litellm", "postgres:16-alpine"], check=True, capture_output=True)
        cls.addClassCleanup(subprocess.run, ["docker", "rm", "-f", cls.container], capture_output=True)
        for _ in range(120):
            # The init server listens only on the Unix socket; TCP readiness
            # means database creation and the final restart have completed.
            result = subprocess.run(["docker", "exec", cls.container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "litellm"], capture_output=True)
            if not result.returncode:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("PostgreSQL did not become ready")
        # The first application also works before LiteLLM's initial migration.
        cls.sql((ROOT / "deploy/control-plane/token-usage.sql").read_text())
        cls.sql('''CREATE TABLE "LiteLLM_SpendLogs" (
            request_id text PRIMARY KEY, call_type text, api_key text, model text,
            model_group text, "startTime" timestamp, prompt_tokens integer,
            completion_tokens integer, total_tokens integer,
            messages jsonb, response jsonb, proxy_server_request jsonb, metadata jsonb,
            request_tags jsonb, cache_key text, api_base text, "user" text,
            end_user text, requester_ip_address text, session_id text, team_id text,
            organization_id text, agent_id text, mcp_namespaced_tool_name text, status text
        );''')
        cls.sql((ROOT / "deploy/control-plane/token-usage.sql").read_text())
        cls.sql((ROOT / "deploy/control-plane/token-usage.sql").read_text())

    @classmethod
    def sql(cls, text, *variables):
        result = subprocess.run(["docker", "exec", "-i", cls.container, "psql", "-X", "-qAt",
                                 "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "litellm", *variables],
                                input=text, text=True, capture_output=True, timeout=40)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.sql('TRUNCATE "LiteLLM_SpendLogs";')

    def report(self, period="day", since="2026-10-01", until="2026-11-01", by_model=False):
        return json.loads(self.sql(usage.usage_query(period, by_model),
                                   "-v", f"since={since}", "-v", f"until={until}",
                                   "-v", "timezone=Europe/Copenhagen"))

    def test_strips_content_on_insert_and_update_but_preserves_token_counts(self):
        self.sql('''INSERT INTO "LiteLLM_SpendLogs"
            (request_id, call_type, api_key, model_group, "startTime", prompt_tokens,
             completion_tokens, total_tokens, messages, response, proxy_server_request,
             metadata, request_tags, cache_key, "user", session_id)
            VALUES ('synthetic', 'completion', 'sk-synthetic-secret', 'automation',
                    '2026-10-21 12:00', 11, 7, 18, '{"content":"PRIVATE_INPUT"}',
                    '{"content":"PRIVATE_OUTPUT"}', '{"body":"PRIVATE_INPUT"}',
                    '{"tool_arguments":"PRIVATE_INPUT"}', '["PRIVATE_INPUT"]',
                    'PRIVATE_INPUT', 'PRIVATE_INPUT', 'PRIVATE_INPUT');''')
        self.sql('''UPDATE "LiteLLM_SpendLogs" SET response='{"content":"PRIVATE_OUTPUT"}',
                    metadata='{"custom":"PRIVATE_INPUT"}' WHERE request_id='synthetic';''')
        stored = self.sql('SELECT row_to_json(s) FROM "LiteLLM_SpendLogs" s;')
        self.assertNotIn("PRIVATE_", stored)
        self.assertNotIn("sk-synthetic", stored)
        row = json.loads(stored)
        for key in ("messages", "response", "proxy_server_request", "metadata"):
            self.assertEqual({}, row[key])
        self.assertEqual(18, row["total_tokens"])
        self.assertEqual(18, self.report()["rows"][0]["total_tokens"])

    def test_daily_range_handles_25_hour_day_and_excludes_end_boundary(self):
        self.sql('''INSERT INTO "LiteLLM_SpendLogs"
            (request_id, model_group, "startTime", prompt_tokens, completion_tokens, total_tokens)
            VALUES ('before', 'automation', '2026-10-24 21:59:59', 1, 2, 3),
                   ('start', 'automation', '2026-10-24 22:00:00', 10, 20, 30),
                   ('last', 'automation', '2026-10-25 22:59:59', 100, 200, 300),
                   ('after', 'automation', '2026-10-25 23:00:00', 1000, 2000, 3000);''')
        rows = self.report(since="2026-10-25", until="2026-10-26")["rows"]
        self.assertEqual([dict(period_start="2026-10-25", model="all", requests=2,
                               input_tokens=110, output_tokens=220, total_tokens=330,
                               cached_input_tokens=0, cache_tracked_input_tokens=0, cache_reported_requests=0)], rows)

    def test_week_starts_monday_and_monthly_models_sum_independently(self):
        self.sql('''INSERT INTO "LiteLLM_SpendLogs"
            (request_id, model_group, "startTime", prompt_tokens, completion_tokens, total_tokens)
            VALUES ('sunday', 'home', '2026-10-18 12:00', 1, 2, 3),
                   ('monday', 'home', '2026-10-19 12:00', 10, 20, 30),
                   ('other', 'automation', '2026-10-19 12:00', 100, 200, 300);''')
        weekly = self.report(period="week")["rows"]
        self.assertEqual(["2026-10-12", "2026-10-19"], [row["period_start"] for row in weekly])
        self.assertEqual([3, 330], [row["total_tokens"] for row in weekly])
        monthly = self.report(period="month", by_model=True)["rows"]
        self.assertEqual([("automation", 300), ("home", 33)],
                         [(row["model"], row["total_tokens"]) for row in monthly])

    def test_empty_range_is_valid(self):
        self.assertEqual([], self.report()["rows"])

    def test_cache_allowlist_keeps_only_valid_counts_and_distinguishes_unknown_from_zero(self):
        values = [
            ({"usage_object": {"prompt_tokens_details": {"cached_tokens": 8}}}, 8),
            ({"additional_usage_values": {"prompt_tokens_details": {"cached_tokens": 0}}}, 0),
            ({"additional_usage_values": {"cache_read_input_tokens": 4}}, 4),
            ({"usage_object": {"prompt_tokens_details": {"cached_tokens": None}},
              "additional_usage_values": {"prompt_tokens_details": {"cached_tokens": 2}}}, 2),
            ({}, None),
        ]
        for invalid in [-1, 11, "8", True, 1.5, 1e30, {"private": "PRIVATE_CONTENT"}]:
            values.append(({"usage_object": {"prompt_tokens_details": {"cached_tokens": invalid}}}, None))
        for index, (metadata, expected) in enumerate(values):
            metadata["private"] = "PRIVATE_CONTENT"
            encoded = json.dumps(metadata).replace("'", "''")
            self.sql(f'''INSERT INTO "LiteLLM_SpendLogs"
                (request_id, model_group, "startTime", prompt_tokens, completion_tokens, total_tokens, metadata)
                VALUES ('cache-{index}', 'home', '2026-10-21 12:00', 10, 2, 12, '{encoded}');''')
            stored = json.loads(self.sql(f'''SELECT metadata FROM "LiteLLM_SpendLogs" WHERE request_id='cache-{index}';'''))
            self.assertEqual({} if expected is None else {"homecompute_cached_input_tokens": expected}, stored)
        self.sql('''UPDATE "LiteLLM_SpendLogs" SET response='{"private":"PRIVATE_CONTENT"}' WHERE request_id='cache-0';''')
        result = self.report()["rows"][0]
        self.assertEqual(14, result["cached_input_tokens"])
        self.assertEqual(40, result["cache_tracked_input_tokens"])
        self.assertEqual(4, result["cache_reported_requests"])
        self.assertNotIn("PRIVATE_", self.sql('SELECT json_agg(s) FROM "LiteLLM_SpendLogs" s;'))


if __name__ == "__main__":
    unittest.main()
