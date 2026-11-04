"""Private task audiences fail closed at delivery and household provisioning."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
def load(name,file):
 s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
worker=load('delivery_worker','openclaw-household-task-reminders.py')
provision=load('owner_destination','setup-openclaw-owner-task-delivery.py')
household=load('family_audience','setup-mattermost-household.py')
organize=load('retired_retarget','organize-mattermost.py')
OWNER,PARTNER,BOT,OTHER,TEAM,FAMILY,TOWN,SYSTEM,CLAW,PRIVATE,PLANNING=(c*26 for c in 'abcdnftsoxp')


class DeliveryTests(unittest.TestCase):
 def client(self,extra_humans=(),extra_bots=()):
  c=object.__new__(worker.Mattermost);c.scope='owner';c.config={'bot_id':BOT,'channel_id':PRIVATE,'allowed_human_ids':[OWNER],'allowed_bot_ids':[BOT]}
  humans={OWNER,*extra_humans};bots={BOT,*extra_bots}
  def request(path):
   if path=='/users/me':return {'id':BOT,'is_bot':True}
   if path=='/channels/'+PRIVATE:return {'id':PRIVATE,'type':'P','delete_at':0}
   if path.endswith('/members?page=0&per_page=100'):return [{'user_id':x} for x in humans|bots]
   identifier=path.rsplit('/',1)[-1];return {'id':identifier,'is_bot':identifier in bots}
  c.request=Mock(side_effect=request);return c

 def test_only_owner_and_admitted_bots_pass(self):
  self.client().verify()
  with self.assertRaisesRegex(ValueError,'audience changed'):self.client(extra_humans=(PARTNER,)).verify()
  with self.assertRaisesRegex(ValueError,'audience changed'):self.client(extra_bots=(OTHER,)).verify()
  client=self.client();del client.config['allowed_human_ids'];del client.config['allowed_bot_ids']
  with self.assertRaisesRegex(ValueError,'must be provisioned'):client.verify()

 def test_api_provisioner_refuses_shared_or_unknown_bot_audience(self):
  provision.audience({OWNER},{BOT},{OWNER},{BOT},BOT)
  with self.assertRaises(ValueError):provision.audience({OWNER,PARTNER},{BOT},{OWNER},{BOT},BOT)
  with self.assertRaises(ValueError):provision.audience({OWNER},{BOT,OTHER},{OWNER},{BOT},BOT)

 def test_config_replacement_preserves_service_owner_permissions(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'config.json';p.write_bytes(b'before');p.chmod(0o600);before=p.stat()
   provision.write_config(p,b'after');after=p.stat()
   self.assertEqual(p.read_bytes(),b'after');self.assertEqual((after.st_uid,after.st_gid,after.st_mode&0o777),(before.st_uid,before.st_gid,0o600))

 def test_all_except_systems_never_shares_private_owner_room(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory);mapping=p/'channels.json';mapping.write_text(json.dumps({'home-operations':SYSTEM,'openclaw':CLAW}));mapping.chmod(0o600)
   rows=[{'id':identifier,'name':name,'type':'P','team_id':TEAM,'delete_at':0} for identifier,name in [(FAMILY,'family'),(TOWN,'town-square'),(SYSTEM,'home-operations'),(CLAW,'openclaw'),(PRIVATE,'owner-tasks'),(PLANNING,'planning')]]
   access={'audience_policy':'all-except-systems','system_channel_ids':[SYSTEM,CLAW],'shared_channel_ids':[FAMILY,TOWN,PLANNING]}
   allowed,systems,shared=household.audience(rows,p,access,FAMILY,TOWN,TEAM,share_non_systems=False)
   self.assertEqual(allowed,{FAMILY,TOWN,PLANNING});self.assertEqual(systems,{SYSTEM,CLAW});self.assertTrue(shared)

 def test_historical_family_retarget_is_retired_without_writing(self):
  with self.assertRaisesRegex(organize.b.BootstrapError,'retired'):organize.reconcile_task_destination(apply=True)


if __name__=='__main__':unittest.main()
