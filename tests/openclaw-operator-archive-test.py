#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('archive',ROOT/'scripts/archive-openclaw-operator-artifacts.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CONTAINER='openshell-default--agent-openclaw-aabbccdd-0000-4000-8000-000000000001'

class OperatorArchive(unittest.TestCase):
 def test_explicit_native_identity_requires_complete_canonical_witness(self):
  identifier='aabbccdd-0000-4000-8000-000000000001';generation='aabbccdd-0000-4000-8000-000000000002';revision='a'*40
  self.assertIsNone(m.witness(None,None,None));self.assertEqual(m.witness(identifier,generation,revision)['OPENCLAW_EXPECTED_SANDBOX_ID'],identifier)
  for values in (('','',''),(identifier,None,revision),(identifier,generation,'bad'),(identifier.upper(),generation,revision)):
   with self.subTest(values=values),self.assertRaises(ValueError):m.witness(*values)

 def test_plan_never_copies_or_removes(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(m.os,'geteuid',return_value=0),patch.object(m,'container_inventory',return_value={'/sandbox/retired':{'.':{'kind':'file'}}}) as inventory,patch.object(m,'run') as run:
   result=m.archive_owned(CONTAINER,lambda:None,archive_root=Path(tmp))
   self.assertEqual(result['files'],1);self.assertFalse(result['apply']);run.assert_not_called();self.assertEqual(inventory.call_count,1)

 def exercise(self,mismatch=False,drift=False):
  with tempfile.TemporaryDirectory() as tmp:
   archive=Path(tmp)/'archives';source=Path(tmp)/'source';source.write_bytes(b'private before-image');source.chmod(0o600)
   rows=m.host_inventory(source);entries={'/sandbox/.openclaw/tool-install-backups':rows};calls=[];guards=[]
   def inventory(container,action='inventory',entries=None):
    calls.append(action)
    if drift and calls.count('inventory')>1:return {}
    if action=='remove':
     manifests=list(archive.glob('native-home-*/manifest.json'));self.assertEqual(len(manifests),1)
     receipt=json.loads(manifests[0].read_bytes());self.assertEqual(receipt['status'],'copied-verified')
     self.assertEqual(m.host_inventory(manifests[0].parent/'entry-0'),rows)
     return {'removed':1}
    return {'/sandbox/.openclaw/tool-install-backups':rows}
   def copy(argv,**kwargs):
    self.assertEqual(argv[:3],['docker','cp','--archive']);target=Path(argv[-1]);target.write_bytes(b'bad' if mismatch else source.read_bytes());target.chmod(0o600);return b''
   with patch.object(m.os,'geteuid',return_value=0),patch.object(m,'protected'),patch.object(m,'container_inventory',side_effect=inventory),patch.object(m,'run',side_effect=copy):
    if mismatch or drift:
     with self.assertRaisesRegex(ValueError,'verification-failed' if mismatch else 'changed-after-copy'):m.archive_owned(CONTAINER,lambda:guards.append(True),apply=True,archive_root=archive)
     self.assertNotIn('remove',calls);self.assertTrue((archive/'pending.json').exists())
     with self.assertRaisesRegex(ValueError,'reviewed-continuation'):m.archive_owned(CONTAINER,lambda:None,apply=True,archive_root=archive)
    else:
     result=m.archive_owned(CONTAINER,lambda:guards.append(True),apply=True,archive_root=archive)
     self.assertTrue(result['verified']);self.assertEqual(calls,['inventory','inventory','remove']);self.assertEqual(len(guards),2)
     self.assertFalse((archive/'pending.json').exists());self.assertEqual(json.loads((Path(result['private_archive'])/'manifest.json').read_bytes())['status'],'complete')

 def test_verified_private_copy_precedes_removal(self):self.exercise()
 def test_failed_copy_cannot_remove_or_replay(self):self.exercise(mismatch=True)
 def test_changed_live_material_cannot_remove_or_replay(self):self.exercise(drift=True)

 def test_root_copy_ownership_restored_after_content_check(self):
  expected={'.':{'uid':1000,'gid':1000,'kind':'file','mode':0o600,'sha256':'a'*64,'bytes':5}}
  copied={'.':{**expected['.'],'uid':0,'gid':0}}
  with patch.object(m,'host_inventory',side_effect=[copied,expected]),patch.object(m.os,'chown') as chown,patch.object(m.os,'chmod') as chmod:
   m.preserve_copy_ownership(Path('/private/copy'),expected)
   chown.assert_called_once_with(Path('/private/copy'),1000,1000,follow_symlinks=False);chmod.assert_called_once_with(Path('/private/copy'),0o600,follow_symlinks=False)
  for field,value in (('sha256','b'*64),('mode',0o644),('uid',998)):
   changed={'.':{**copied['.'],field:value}}
   with self.subTest(field=field),patch.object(m,'host_inventory',return_value=changed),patch.object(m.os,'chown') as chown:
    with self.assertRaises(ValueError):m.preserve_copy_ownership(Path('/private/copy'),expected)
    chown.assert_not_called()

 def test_pending_operation_cannot_be_bypassed_by_empty_inventory(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(m.os,'geteuid',return_value=0),patch.object(m,'container_inventory',return_value={}):
   root=Path(tmp);(root/'pending.json').write_text('{}')
   with self.assertRaisesRegex(ValueError,'reviewed-continuation'):m.archive_owned(CONTAINER,lambda:None,apply=True,archive_root=root)

 def test_archive_inventory_refuses_links(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data').write_text('private');(root/'link').symlink_to('data')
   with self.assertRaisesRegex(ValueError,'link-or-special'):m.host_inventory(root)

 def test_staging_requires_root_or_operator_private_ownership(self):
  namespace={};exec(m.CONTAINER_CODE.split('request=json.load(sys.stdin)')[0],namespace)
  for uid,gid,mode,allowed in ((0,0,0o700,True),(1000,1000,0o700,True),(998,998,0o700,False),(1000,1000,0o755,False),(1000,998,0o700,False)):
   with self.subTest(uid=uid,gid=gid,mode=mode):self.assertEqual(namespace['protected_stage'](SimpleNamespace(st_uid=uid,st_gid=gid,st_mode=stat.S_IFDIR|mode)),allowed)
  self.assertFalse(namespace['protected_stage'](SimpleNamespace(st_uid=1000,st_gid=1000,st_mode=stat.S_IFREG|0o700)))

 def test_container_inventory_bounds_before_hashing(self):
  namespace={};exec(m.CONTAINER_CODE.split('request=json.load(sys.stdin)')[0],namespace)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);large=root/'large'
   with large.open('wb') as stream:stream.truncate(64*1024**2+1)
   with patch.object(namespace['hashlib'],'file_digest',side_effect=AssertionError('oversized file was hashed')):
    with self.assertRaisesRegex(ValueError,'artifact-budget'):namespace['inventory'](root)
   large.unlink()
   for i in range(2048):(root/str(i)).touch()
   with self.assertRaisesRegex(ValueError,'artifact-budget'):namespace['inventory'](root)

 def test_container_reference_fences_active_inputs(self):
  definitions=m.CONTAINER_CODE.split('request=json.load(sys.stdin)')[0];namespace={};exec(definitions,namespace)
  root=Path('/sandbox/.openclaw/tool-install-backups')
  for value in (str(root)+'/private.json','~/.openclaw/tool-install-backups/private.json','tool-install-backups/private.json','file://'+str(root)+'/private.json'):
   with self.subTest(value=value):self.assertTrue(namespace['referenced'](root,{'nested':[value]}))
  self.assertFalse(namespace['referenced'](root,{'path':'/sandbox/.openclaw/openclaw.json'}))

if __name__=='__main__':unittest.main()
