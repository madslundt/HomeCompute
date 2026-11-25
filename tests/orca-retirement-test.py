#!/usr/bin/env python3
"""Exercise scoped, retriable revocation during deployment."""

from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, call


retire = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/retire-orca.py"))
KEY = {
    "key_alias": "orca-inference",
    "user_id": "orca-inference",
    "metadata": {"purpose": "Private Orca OpenCode inference relay"},
    "token": "a" * 64,
}


def response(*keys, count=None):
    return {"keys": list(keys), "total_count": len(keys) if count is None else count}


class OrcaRetirementTest(unittest.TestCase):
    def test_verify_litellm_revocation_and_allow_retry_after_success(self):
        request = Mock(side_effect=[response(KEY), {}, response(), response()])
        retire["revoke_keys"](request)
        retire["revoke_keys"](request)
        self.assertEqual([entry for entry in request.call_args_list if entry.args[0] == "/key/delete"],
                         [call("/key/delete", {"keys": [KEY["token"]]})])

    def test_refuse_incomplete_or_unrelated_key_results(self):
        for result in (response(KEY, count=2), response({**KEY, "user_id": "other"})):
            with self.subTest(result=result):
                request = Mock(return_value=result)
                with self.assertRaises(ValueError):
                    retire["revoke_keys"](request)
                request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
