#!/usr/bin/env python3
"""Synthetic lifecycle cannot share ownership or accept unreviewed artifacts."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('native',Path(__file__).resolve().parents[1]/'scripts/qualify-openclaw-native-lifecycle.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class NativeQualification(unittest.TestCase):
 def test_ambiguous_production_refuses_before_reading_config(self):
  with patch.object(m,'execute',return_value=b'production-a\nproduction-b\n') as run:
   with self.assertRaisesRegex(ValueError,'exact production'):m.fence()
   self.assertEqual(run.call_count,1)

 def test_existing_fixture_is_preserved(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'STATE',Path(d)),patch.object(m,'execute') as run:
   with self.assertRaisesRegex(ValueError,'already exists'):m.prepare()
   run.assert_not_called()

 def test_artifact_replacement_refuses_before_user_creation(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d).resolve();build=base/'.local/share/homecompute/openclaw-cli-builds/fixture';build.mkdir(parents=True)
   (build/'receipt.json').write_text(json.dumps({'artifacts':{v:{'sha256':h} for v,h in m.ARCHIVES.items()}}))
   (build/'nemoclaw-0.0.129.tar.gz').write_bytes(b'changed archive')
   with patch.object(m,'STATE',base/'state'),patch.object(m,'BUILD',build),patch.object(m,'OPERATOR',base),patch.object(m,'execute',return_value=b''),patch.object(m.pwd,'getpwnam',side_effect=KeyError),patch.object(m.subprocess,'run') as run:
    with self.assertRaisesRegex(ValueError,'hash mismatch'):m.prepare()
    run.assert_not_called()
    self.assertFalse((base/'state').exists())

 def test_required_tool_probe_gets_structured_call(self):
  request=object.__new__(m.Provider)
  raw=json.dumps({'tools':[{'type':'function','function':{'name':'emit_ok'}}],'tool_choice':'required'}).encode()
  request.headers={'Content-Length':str(len(raw))};request.rfile=io.BytesIO(raw)
  with patch.object(request,'reply') as reply:request.do_POST()
  choice=reply.call_args.args[0]['choices'][0]
  self.assertEqual(choice['finish_reason'],'tool_calls')
  self.assertEqual(choice['message']['tool_calls'][0]['function'],{'name':'emit_ok','arguments':json.dumps({'value':'OK'})})

 def test_oversize_provider_body_is_not_read(self):
  request=object.__new__(m.Provider);request.headers={'Content-Length':'262145'};request.rfile=io.BytesIO(b'private body')
  with patch.object(request,'send_error') as error:request.do_POST();error.assert_called_once_with(413)
  self.assertEqual(request.rfile.tell(),0)


if __name__=='__main__':unittest.main()
