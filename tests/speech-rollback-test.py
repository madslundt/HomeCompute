#!/usr/bin/env python3
"""Selected legacy rollback rendering, provenance and actual-baseline guards."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('speech_rollback',ROOT/'scripts/setup-speech-boundary.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture(name=m.CORE_SERVICES[0], revision='1'*40, image='sha256:'+'a'*64):
    port,ip=(10201,'172.28.200.4') if name.startswith('plapre') else (10301,'172.28.200.5')
    source=f'/srv/homecompute/releases/{revision}'
    service={'image':image,'entrypoint':['python3','/opt/homecompute/tcp-edge-proxy.py'],'command':None,
        'environment':{'EDGE_LISTEN_PORT':str(port),'EDGE_UPSTREAM_HOST':'172.28.200.1','EDGE_UPSTREAM_PORT':str(port)},
        'user':'65532:65532','read_only':True,'profiles':['speech-proxies'],
        'networks':{'edge':{'ipv4_address':ip}},
        'volumes':[{'type':'bind','source':source+'/scripts/tcp-edge-proxy.py','target':'/opt/homecompute/tcp-edge-proxy.py',
                    'read_only':True,'bind':{'create_host_path':True}}],
        'ports':[{'target':port,'published':str(port),'host_ip':'192.168.30.122','protocol':'tcp'}]}
    document={'name':'homecompute-control-plane','services':{name:service},
              'networks':{'edge':{'name':'homecompute-control-plane_edge'}}}
    actual={'Id':'b'*64,'Image':image,'Config':{'Entrypoint':service['entrypoint'],'Cmd':None,'User':service['user'],
        'Env':['PATH=/usr/bin',*[k+'='+v for k,v in service['environment'].items()]]},
        'HostConfig':{'ReadonlyRootfs':True,'PortBindings':{str(port)+'/tcp':[{'HostIp':'192.168.30.122','HostPort':str(port)}]}},
        'Mounts':[{'Type':'bind','Source':service['volumes'][0]['source'],'Destination':'/opt/homecompute/tcp-edge-proxy.py','RW':False}],
        'NetworkSettings':{'Networks':{'homecompute-control-plane_edge':{'IPAddress':ip}}},
        'State':{'Running':True,'Health':{'Status':'healthy'}}}
    image_data={'Id':image,'Config':{'Env':['PATH=/usr/bin']}}
    return document,actual,image_data


class SpeechRollbackTest(unittest.TestCase):
    def test_real_compose_discards_retired_requirements_from_standalone_plan(self):
        name=m.CORE_SERVICES[0];document,_,image=fixture()
        document['services']['caddy']={'image':'synthetic/discarded',
            'environment':{'HERMES_FQDN':'${HERMES_FQDN:?retired address required}',
                           'HERMES_UPSTREAM':'${HERMES_UPSTREAM:?retired upstream required}'}}
        command=['docker','compose','--profile','speech-proxies','--env-file','/dev/null','-f','-', 'config','--format','json']
        def environment(variant=0):
            # Keep the workstation's Docker Desktop plugin discovery; only
            # the two public parser fields are varied by this synthetic test.
            value=dict(os.environ)
            for key in ('HERMES_FQDN','HERMES_UPSTREAM'):
                value.pop(key,None)
                if variant:value[key]=m.compose_environment(variant)[key]
            return value
        raw=json.dumps(document)
        failed=subprocess.run(command,input=raw,text=True,capture_output=True,env=environment())
        self.assertNotEqual(failed.returncode,0)
        selected=[]
        for variant in (1,2):
            result=subprocess.run(command,input=raw,text=True,capture_output=True,check=True,env=environment(variant))
            selected.append(m.selected_rollback(json.loads(result.stdout),name,image['Id']))
        self.assertEqual(selected[0],selected[1])
        self.assertEqual(set(selected[0]['services']),{name})
        self.assertEqual(selected[0]['networks']['edge'],{'name':'homecompute-control-plane_edge','external':True})
        self.assertNotIn('HERMES',json.dumps(selected[0]));self.assertNotIn('discarded',json.dumps(selected[0]))
        # No parser-only address, env snapshot or retired service is needed now.
        result=subprocess.run(command,input=json.dumps(selected[0]),text=True,capture_output=True,check=True,env=environment())
        self.assertEqual(set(json.loads(result.stdout)['services']),{name})

    def test_selected_plan_refuses_unreviewed_resources_or_parser_input_leak(self):
        name=m.CORE_SERVICES[0]
        for changes in [{'depends_on':{'caddy':{}}},{'secrets':['credential']},
                        {'environment':{'EDGE_LISTEN_PORT':'10201','EDGE_UPSTREAM_HOST':'discarded-1.invalid','EDGE_UPSTREAM_PORT':'1'}},
                        {'environment':{'HERMES_FQDN':'retired.invalid'}}]:
            document,_,image=fixture();document['services'][name].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):m.selected_rollback(document,name,image['Id'])

    def test_actual_baseline_matches_source_and_every_critical_drift_is_refused(self):
        name=m.CORE_SERVICES[0];document,actual,image=fixture()
        selected=m.selected_rollback(document,name,image['Id']);m.baseline_contract(selected,name,actual,image)
        def change(value,path,replacement):
            for key in path[:-1]:value=value[key]
            value[path[-1]]=replacement
        changes=[(('Image',),'sha256:'+'c'*64),(('Config','Entrypoint'),['unexpected']),
                 (('Config','Cmd'),['unexpected']), (('Config','Env'),['PATH=/wrong']),
                 (('Config','User'),'0'),(('HostConfig','ReadonlyRootfs'),False),
                 (('Mounts',0,'Source'),'/unreviewed'),(('Mounts',0,'RW'),True),
                 (('HostConfig','PortBindings'),{}),
                 (('NetworkSettings','Networks','homecompute-control-plane_edge','IPAddress'),'172.28.200.9'),
                 (('State','Running'),False),(('State','Health','Status'),'unhealthy')]
        for path,replacement in changes:
            bad=copy.deepcopy(actual);change(bad,path,replacement)
            with self.subTest(path=path),self.assertRaises(ValueError):m.baseline_contract(selected,name,bad,image)

    def test_helper_allows_compose_bind_defaults_but_refuses_changed_source_or_modes(self):
        name=m.CORE_SERVICES[0];document,_,_=fixture()
        source=Path('/srv/homecompute/releases/'+'1'*40+'/deploy/control-plane/compose.yaml')
        for bind in ({},{'create_host_path':True},{'create_host_path':False}):
            document['services'][name]['volumes'][0]['bind']=bind
            with patch.object(m,'trusted'),patch.object(Path,'read_text',return_value='published helper'), \
                 patch.object(m,'run',return_value='published helper'):
                m.previous_helper(source,document,name)
        for change in ({'read_only':False},{'source':'/unreviewed/helper.py'},{'bind':{'propagation':'shared'}}):
            bad=copy.deepcopy(document);bad['services'][name]['volumes'][0].update(change)
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'helper mount'):
                m.previous_helper(source,bad,name)

    def test_existing_legacy_receipt_is_upgraded_without_replacing_original_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary);before={'previous':{},'environment':str(state/'environment.before'),'revision':'0'*40}
            documents={};actuals={};images={}
            for i,name in enumerate(m.CORE_SERVICES):
                document,actual,image=fixture(name,str(i+1)*40,'sha256:'+str(i+3)*64)
                documents[name]=document;actuals[name]=actual;images[image['Id']]=image
                before['previous'][name]={'compose':f'/srv/homecompute/releases/{str(i+1)*40}/deploy/control-plane/compose.yaml','image_id':image['Id']}
            original=json.dumps(before).encode();(state/'before.json').write_bytes(original)
            def run(args,**kwargs):
                if args[:2]==['docker','compose']:
                    source=args[args.index('-f')+1]
                    name=next(n for n,r in before['previous'].items() if r['compose']==source)
                    return json.dumps(documents[name])
                if args[:2]==['docker','inspect']:
                    name=args[-1].removeprefix('homecompute-control-plane-').removesuffix('-1');return json.dumps([actuals[name]])
                if args[:3]==['docker','image','inspect']:return json.dumps([images[args[-1]]])
                self.fail('unexpected operation')
            with patch.object(m,'STATE',state),patch.object(m,'trusted'),patch.object(m,'previous_receipt',return_value=before), \
                 patch.object(m,'previous_source',return_value='f'*64),patch.object(m,'previous_helper'), \
                 patch.object(m,'run',side_effect=run),patch.object(m,'validate_rollback') as validate:
                m.stage_rollback();validate.assert_called_once_with(True)
            self.assertEqual((state/'before.json').read_bytes(),original)
            plan=json.loads((state/'rollback-plan.json').read_text())
            for name,row in before['previous'].items():
                self.assertEqual(plan['services'][name]['compose'],row['compose'])
                selected=json.loads((state/('rollback-'+name+'.json')).read_text())
                self.assertEqual(selected['services'][name]['image'],row['image_id'])

    def test_tampered_private_plan_fails_before_docker_or_tunnel_operations(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary);name=m.CORE_SERVICES[0];document,_,image=fixture()
            before={'previous':{n:{'compose':'/srv/homecompute/releases/'+'1'*40+'/deploy/control-plane/compose.yaml','image_id':image['Id']}
                                for n in m.CORE_SERVICES},'environment':str(state/'environment.before')}
            document=m.selected_rollback(document,name,image['Id']);body=json.dumps(document)
            path=state/('rollback-'+name+'.json');path.write_text(body+' ')
            rows={n:{'compose':r['compose'],'source_sha256':'f'*64,'document_sha256':hashlib.sha256(body.encode()).hexdigest()}
                  for n,r in before['previous'].items()}
            (state/'rollback-plan.json').write_text(json.dumps({'revision':m.SOURCE.name,'services':rows}))
            with patch.object(m,'STATE',state),patch.object(m,'trusted'),patch.object(m,'previous_receipt',return_value=before), \
                 patch.object(m,'previous_source',return_value='f'*64),patch.object(m,'run') as run:
                with self.assertRaisesRegex(ValueError,'provenance or staged'):m.validate_rollback(True)
                run.assert_not_called()

    def test_apply_rollback_guard_refuses_before_gate_or_active_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary);(state/'before.json').write_text('{}');(state/'tunnel.pending.conf').write_text('pending')
            (state/'runner.pending.py').write_bytes(Path(m.__file__).read_bytes())
            with patch.object(m,'STATE',state),patch.object(m,'DEPLOY_LOCK',state/'deploy.lock'), \
                 patch.object(m,'validate_rollback',side_effect=ValueError('rollback render failed')), \
                 patch.object(m,'core_maintenance') as maintenance,patch.object(m,'write') as write,patch.object(m,'run') as run:
                with self.assertRaisesRegex(ValueError,'rollback render failed'):m.core_apply(True)
                maintenance.assert_not_called();write.assert_not_called();run.assert_not_called()

    def test_replaced_actual_baseline_is_refused_after_standalone_render_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary);name=m.CORE_SERVICES[0];document,actual,image=fixture()
            document=m.selected_rollback(document,name,image['Id']);body=json.dumps(document)
            path=state/('rollback-'+name+'.json');path.write_text(body)
            before={'previous':{n:{'compose':'/srv/homecompute/releases/'+'1'*40+'/deploy/control-plane/compose.yaml','image_id':image['Id']}
                                for n in m.CORE_SERVICES},'environment':str(state/'environment.before')}
            records={n:{'compose':r['compose'],'source_sha256':'f'*64,'document_sha256':hashlib.sha256(body.encode()).hexdigest(),'baseline_id':'c'*64}
                     for n,r in before['previous'].items()}
            (state/'rollback-plan.json').write_text(json.dumps({'revision':m.SOURCE.name,'services':records}))
            def run(args,**kwargs):
                if args[:3]==['docker','image','inspect']:return json.dumps([image])
                if args[:3]==['docker','network','inspect']:return '[]'
                if args[:2]==['docker','compose']:return json.dumps(document)
                if args[:2]==['docker','inspect']:return json.dumps([actual])
                self.fail('unexpected operation')
            with patch.object(m,'STATE',state),patch.object(m,'trusted'),patch.object(m,'previous_receipt',return_value=before), \
                 patch.object(m,'previous_source',return_value='f'*64),patch.object(m,'previous_helper'),patch.object(m,'run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'replaced after staging'):m.validate_rollback(True)


if __name__=='__main__':unittest.main()
