"""Private setup and encrypted CLI imports using only a synthetic n8n database."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
SPEC=importlib.util.spec_from_file_location('calendar_setup',ROOT/'scripts/setup-openclaw-calendar.py')
setup=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
ALIASES=sorted(setup.ALIASES)
MAPPING={alias:alias+'@synthetic.invalid' for alias in ALIASES}
NODES=[{'name':'Prepare incoming calendar event','parameters':{'jsCode':
        'const calendars = {'+','.join(k+':'+json.dumps(v) for k,v in MAPPING.items())+'};'}}]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        self.directory=self.root/'calendar-bridge'
        self.database=self.root/'database.sqlite'
        db=sqlite3.connect(self.database)
        db.executescript('''CREATE TABLE project(id,type); CREATE TABLE project_relation(projectId,userId,role);
        CREATE TABLE user(id,roleSlug);CREATE TABLE credentials_entity(id,name,type,data);
        CREATE TABLE shared_credentials(credentialsId,projectId,role);CREATE TABLE workflow_entity(id,nodes,isArchived);''')
        db.execute('INSERT INTO project VALUES(?,?)',('project','personal'))
        db.execute('INSERT INTO user VALUES(?,?)',('owner','global:owner'))
        db.execute('INSERT INTO project_relation VALUES(?,?,?)',('project','owner','project:personalOwner'))
        db.execute('INSERT INTO credentials_entity VALUES(?,?,?,?)',(setup.GOOGLE_CREDENTIAL['id'],setup.GOOGLE_CREDENTIAL['name'],'googleCalendarOAuth2Api','ENCRYPTED-OAUTH'))
        db.execute('INSERT INTO workflow_entity VALUES(?,?,0)',(setup.MAP_WORKFLOW,json.dumps(NODES)))
        db.commit();db.close()
        for name,value in [('CONFIG',self.root),('TOKEN',self.root/'calendar-route-token'),('DIRECTORY',self.directory),
                           ('DATABASE',str(self.database)),('SERVICE_UID',os.geteuid()),('ADMIN_UID',os.geteuid())]:
            p=patch.object(setup,name,value);p.start();self.addCleanup(p.stop)
        self.payload=None;self.commands=[]
        ownership=patch.object(setup.os,'fchown',lambda *_:None)
        ownership.start();self.addCleanup(ownership.stop)

    def command(self,argv,content=None):
        self.commands.append(argv)
        if content and content.startswith('[{'):
            self.payload=json.loads(content)
        if 'import:credentials' in argv:
            row=self.payload[0]
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute('INSERT INTO credentials_entity VALUES(?,?,?,?)',(row['id'],row['name'],row['type'],'ENCRYPTED-ROUTE'))
                db.execute('INSERT INTO shared_credentials VALUES(?,?,?)',(row['id'],'project','credential:owner'))
        if any('process.stdout.write' in a for a in argv):
            return json.dumps(self.payload)
        return ''

    def test_exact_five_map_no_work_or_alias_fallback(self):
        self.assertEqual(setup.read_mapping(NODES),MAPPING)
        for nodes in [[],[NODES[0],NODES[0]],[{'name':NODES[0]['name'],'parameters':{'jsCode':NODES[0]['parameters']['jsCode'].replace('family:','work:')}}]]:
            with self.assertRaises(ValueError):
                setup.read_mapping(nodes)

    def test_instantiation_is_inactive_and_keeps_generic_source_unconfigured(self):
        workflow,code=setup.instantiate(ROOT/'automations/calendar',MAPPING)
        self.assertFalse(workflow['active'])
        self.assertIn('const configured = true;',code)
        self.assertNotIn('__CALENDAR_IDS__',code)
        self.assertIn('const configured = false;',(ROOT/'automations/calendar/validate-calendar-request.js').read_text())

    def test_first_import_and_rerun_preserve_route_token_and_never_export_google(self):
        with patch.object(setup,'run',side_effect=self.command):
            first=setup.prepare(ROOT/'automations/calendar')
            token=setup.private(setup.TOKEN)
            second=setup.prepare(ROOT/'automations/calendar')
        self.assertEqual(first,second)
        self.assertEqual(setup.private(setup.TOKEN),token)
        self.assertEqual(sum('import:credentials' in cmd for cmd in self.commands),1)
        exports=[cmd for cmd in self.commands if 'export:credentials' in cmd]
        self.assertEqual(len(exports),2)
        self.assertTrue(all('--id='+setup.ROUTE_CREDENTIAL['id'] in cmd for cmd in exports))
        self.assertTrue(all(setup.GOOGLE_CREDENTIAL['id'] not in ' '.join(cmd) for cmd in exports))
        self.assertNotIn(token.decode().strip(),json.dumps(first))
        self.assertEqual((self.root/'calendar.json').stat().st_mode&0o777,0o600)
        receipt=json.loads(setup.private(self.directory/'provision-receipt.json'))
        self.assertFalse(receipt['oauth_exported']);self.assertEqual(receipt['google_writes'],0)
        self.assertEqual(receipt['required_settings']['saveDataErrorExecution'],'none')

    def test_existing_encrypted_credential_without_original_token_fails_closed(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO credentials_entity VALUES(?,?,?,?)',(setup.ROUTE_CREDENTIAL['id'],setup.ROUTE_CREDENTIAL['name'],'httpHeaderAuth','ENCRYPTED'))
        with patch.object(setup,'run') as command,self.assertRaises(ValueError):
            setup.prepare(ROOT/'automations/calendar')
        command.assert_not_called()
        self.assertFalse(setup.TOKEN.exists())

    def test_existing_private_map_drift_refused_before_credential_use(self):
        with patch.object(setup,'run',side_effect=self.command):
            setup.prepare(ROOT/'automations/calendar')
        changed={**MAPPING,'family':'other@synthetic.invalid'}
        setup.json_save(self.directory/'calendar-map.json',changed)
        with patch.object(setup,'run') as command,self.assertRaises(ValueError):
            setup.prepare(ROOT/'automations/calendar')
        command.assert_not_called()

    def test_wrong_retained_credential_secret_is_never_rotated_and_plaintext_temp_is_cleaned(self):
        self.directory.mkdir(mode=0o700)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO credentials_entity VALUES(?,?,?,?)',(setup.ROUTE_CREDENTIAL['id'],setup.ROUTE_CREDENTIAL['name'],'httpHeaderAuth','ENCRYPTED'))
        self.payload=[{'id':setup.ROUTE_CREDENTIAL['id'],'data':{'name':'Authorization','value':'Bearer other'}}]
        db=sqlite3.connect('file:'+str(self.database)+'?mode=ro',uri=True)
        try:
            with patch.object(setup,'run',side_effect=self.command),self.assertRaises(ValueError):
                setup.import_credential(db,'a'*64,'project')
        finally:
            db.close()
        self.assertFalse(any('import:credentials' in cmd for cmd in self.commands))
        self.assertIn('unlinkSync',' '.join(self.commands[-1]))


if __name__=='__main__':
    unittest.main()
