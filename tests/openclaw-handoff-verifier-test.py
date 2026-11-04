"""A queued completion is a typed control result; real tool errors still fail."""
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('handoff',ROOT/'scripts/verify-openclaw-atlas-handoff.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class VerifierTests(unittest.TestCase):
 def test_exact_queued_yield_needs_verified_child_and_final_answer(self):
  queued={'toolName':'sessions_yield','isError':True,'details':{'status':'skipped','deniedReason':'steering'}}
  self.assertEqual(m.classify_tool_results([queued],True,'13 + 29 = 42'),(['sessions_yield'],[],True))
  for child,answer in [(False,'42'),(True,'')]:
   self.assertEqual(m.classify_tool_results([queued],child,answer)[1],['sessions_yield'])
  failure={'toolName':'sessions_yield','isError':True,'details':{'status':'error'}}
  self.assertEqual(m.classify_tool_results([queued,failure],True,'42')[1],['sessions_yield'])
  self.assertEqual(m.classify_tool_results([failure],True,'42')[1],['sessions_yield'])

if __name__=='__main__':unittest.main()
