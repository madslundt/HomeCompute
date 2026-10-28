#!/usr/bin/env python3
"""Opt-in, isolated n8n engine test using the already-running image and fake delivery."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    flow = json.loads((ROOT / "automations/agent-investigation/n8n-notification-workflow.json").read_text())
    flow["id"] = "hc-synthetic-communication"
    flow["name"] = "Isolated synthetic communication qualification"
    for node in flow["nodes"]:
        if node["name"] == "Approved delivery configuration":
            node["parameters"]["jsCode"] = node["parameters"]["jsCode"].replace("configured:false", "configured:true").replace(
                "https://openclaw-communication.home.arpa:19443", "http://127.0.0.1:18888").replace(
                "replace-selected-authorized-destination", "synthetic-private").replace("replace-selected-private-chat-id", "1234")
        if node["type"] == "n8n-nodes-base.httpRequest":
            node["credentials"] = {"httpHeaderAuth": {"id": "synthetic-delivery", "name": "synthetic-delivery"}}
        if node["type"] == "n8n-nodes-base.telegram":
            # Only the external sender is a surrogate. Claim/validation/receipt/ack run unchanged in n8n.
            node["type"] = "n8n-nodes-base.code"; node["typeVersion"] = 2
            node.pop("credentials", None)
            node["parameters"] = {"jsCode": "if ($json.chat_id!=='1234') throw new Error('wrong destination'); return [{json:{message_id:42,chat:{id:1234}}}];"}
    credentials = [{"id": "synthetic-delivery", "name": "synthetic-delivery", "type": "httpHeaderAuth",
                    "data": {"name": "Authorization", "value": "Bearer synthetic-no-production-credential"}}]
    mock = """const http=require('http'),fs=require('fs');
const event={destination:'synthetic-private',delivery_key:'synthetic:incident:1',claim:'a'.repeat(32),text:'Synthetic incident only'};
const result={claims:0,acknowledged:false,external_messages:0};
http.createServer((req,res)=>{let raw='';req.on('data',x=>raw+=x);req.on('end',()=>{
if(req.headers.authorization!=='Bearer synthetic-no-production-credential'){res.writeHead(401);res.end('{}');return;}
let body=JSON.parse(raw||'{}');res.setHeader('content-type','application/json');
if(req.url==='/claim'){result.claims++;res.end(JSON.stringify({event}));}
else if(req.url==='/ack'){result.acknowledged=body.delivery_key===event.delivery_key&&body.claim===event.claim&&body.receipt==='telegram:42';fs.writeFileSync('/tmp/receipt.json',JSON.stringify(result));res.end(JSON.stringify({acknowledged:result.acknowledged}));}
else{res.writeHead(404);res.end('{}');}});}).listen(18888,'127.0.0.1');"""
    return {"workflow.json": json.dumps(flow), "credentials.json": json.dumps(credentials), "mock.cjs": mock}


def live():
    payload = base64.b64encode(json.dumps(fixture()).encode()).decode()
    source = '''import base64,json,pathlib,subprocess,tempfile
files=json.loads(base64.b64decode(PAYLOAD))
image=subprocess.check_output(['sudo','-n','docker','inspect','--format','{{.Image}}','homecompute-automation-n8n-1'],text=True).strip()
if not image.startswith('sha256:'):raise ValueError('requires immutable existing image')
with tempfile.TemporaryDirectory(prefix='hc-n8n-communication-') as raw:
 directory=pathlib.Path(raw);directory.chmod(0o755)
 for name,body in files.items():
  path=directory/name;path.write_text(body);path.chmod(0o444)
 command=['sudo','-n','docker','run','--pull=never','--rm','--network','none','--cpus','0.5','--memory','768m',
  '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true',
  '--tmpfs','/home/node/.n8n:uid=1000,gid=1000,mode=700','--tmpfs','/tmp:uid=1000,gid=1000,mode=700',
  '-e','N8N_DIAGNOSTICS_ENABLED=false','-e','N8N_VERSION_NOTIFICATIONS_ENABLED=false',
  '-v',str(directory)+':/fixture:ro','--entrypoint','/bin/sh',image,'-c',
  "node /fixture/mock.cjs & n8n import:credentials --input=/fixture/credentials.json >/tmp/import-credentials.log 2>&1 && n8n import:workflow --input=/fixture/workflow.json >/tmp/import-workflow.log 2>&1 && n8n execute --id=hc-synthetic-communication >/tmp/execute.log 2>&1; result=$?; if [ $result -eq 0 ] && [ -f /tmp/receipt.json ]; then cat /tmp/receipt.json; else tail -c 5000 /tmp/execute.log /tmp/import-workflow.log /tmp/import-credentials.log; exit 1; fi"]
 result=subprocess.run(command,text=True,capture_output=True,timeout=110)
 if result.returncode:
  print(json.dumps({'passed':False,'diagnostics':result.stdout[-6000:],'stderr':result.stderr[-1000:]}))
 else:
  receipt=json.loads(result.stdout)
  print(json.dumps({'passed':receipt['acknowledged'] and receipt['claims']==1,'n8n_image':image,'receipt':receipt,'scope':'isolated real n8n engine; mock external Telegram sender and mock adapter HTTP; no production credentials'}))
'''.replace("PAYLOAD", repr(payload))
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "home-core", "sudo -n python3 -"],
                            input=source, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise SystemExit("Isolated n8n qualification unavailable: " + result.stderr[-1000:])
    receipt = json.loads(result.stdout)
    print(json.dumps(receipt, indent=2))
    return receipt["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="use a disposable network-none n8n container on home-core")
    parser.parse_args()
    raise SystemExit(0 if live() else 1)
