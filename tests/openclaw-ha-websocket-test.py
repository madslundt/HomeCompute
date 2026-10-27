"""Synthetic RFC6455 and finite HA command fixtures; no network or device calls."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
import unittest
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import openclaw_ha_websocket as ha

TOKEN='synthetic-private-token-'+'x'*40


def frame(value, opcode=1, fin=True):
    data=json.dumps(value).encode() if isinstance(value,dict) else value
    size=len(data)
    head=bytes([(0x80 if fin else 0)|opcode])
    return head+(bytes([size]) if size<126 else b'\x7e'+struct.pack('!H',size) if size<65536 else b'\x7f'+struct.pack('!Q',size))+data


def client_frame(raw):
    op=raw[0]&15; length=raw[1]&127; pos=2
    if length==126: length=struct.unpack('!H',raw[pos:pos+2])[0];pos+=2
    elif length==127: length=struct.unpack('!Q',raw[pos:pos+8])[0];pos+=8
    assert raw[1]&128
    mask=raw[pos:pos+4];pos+=4
    return op,bytes(c^mask[i%4] for i,c in enumerate(raw[pos:pos+length]))


class Socket:
    def __init__(self, handler=None, headers=b'', auth='auth_ok'):
        self.incoming=b''; self.sent=[];self.closed=False;self.timeouts=[]
        self.handler=handler; self.headers=headers;self.auth=auth
    def settimeout(self,value): self.timeouts.append(value)
    def sendall(self,data):
        self.sent.append(data)
        if data.startswith(b'GET '):
            key=data.split(b'Sec-WebSocket-Key: ',1)[1].split(b'\r\n',1)[0]
            accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
            self.incoming+=b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n'+self.headers+b'\r\n'+frame({'type':'auth_required'})
            return
        opcode,payload=client_frame(data)
        if opcode!=1: return
        value=json.loads(payload)
        if value.get('type')=='auth': self.incoming+=frame({'type':self.auth});return
        if self.handler: self.incoming+=self.handler(value)
        else: self.incoming+=frame({'id':value['id'],'type':'result','success':True,'result':{'items':[]}})
    def recv(self,size):
        if not self.incoming: raise socket.timeout('private-token-detail')
        data=self.incoming[:min(size,37)];self.incoming=self.incoming[len(data):];return data
    def close(self): self.closed=True


class TransportTests(unittest.TestCase):
    def connection(self,sock): return patch.object(ha.socket,'create_connection',return_value=sock)

    def test_fixed_endpoint_upgrade_auth_masking_incremental_ids_and_close(self):
        sock=Socket()
        with self.connection(sock) as connect:
            with ha.HomeAssistantSocket(TOKEN) as client:
                self.assertEqual(client.call({'type':'todo/item/list','entity_id':'todo.indkob'}),{'items':[]})
                client.call({'type':'todo/item/list','entity_id':'todo.indkob'})
        self.assertEqual(connect.call_args.args[0],('192.168.30.30',80))
        self.assertIn(b'GET /api/websocket HTTP/1.1',sock.sent[0])
        values=[json.loads(client_frame(raw)[1]) for raw in sock.sent[1:] if raw[0]&15==1]
        self.assertEqual(values[0],{'type':'auth','access_token':TOKEN})
        self.assertEqual([v['id'] for v in values[1:]],[1,2])
        self.assertEqual(client_frame(sock.sent[-1])[0],8);self.assertTrue(sock.closed)
        self.assertTrue(all(0<t<=8 for t in sock.timeouts))

    def test_fragmented_message_ping_pong_and_utf8(self):
        def handler(v):
            raw=json.dumps({'id':v['id'],'type':'result','success':True,'result':{'text':'æøå'}},ensure_ascii=False).encode()
            return frame(raw[:21],fin=False)+frame(b'hello',opcode=9)+frame(raw[21:],opcode=0)
        sock=Socket(handler)
        with self.connection(sock),ha.HomeAssistantSocket(TOKEN) as client:
            self.assertEqual(client.call({'type':'todo/item/list','entity_id':'todo.indkob'}),{'text':'æøå'})
        self.assertIn((10,b'hello'),[client_frame(v) for v in sock.sent[1:]])

    def test_snapshot_event_before_or_after_ack_and_only_event_value_returned(self):
        for event_first in (True,False):
            def handler(v):
                ack=frame({'id':v['id'],'type':'result','success':True,'result':None})
                event=frame({'id':v['id'],'type':'event','event':{'revision':7,'items':[],'locations':[]}})
                return event+ack if event_first else ack+event
            with self.connection(Socket(handler)),ha.HomeAssistantSocket(TOKEN) as client:
                self.assertEqual(client.snapshot(),{'revision':7,'items':[],'locations':[]})

    def test_finite_commands_reject_before_send(self):
        sock=Socket()
        with self.connection(sock),ha.HomeAssistantSocket(TOKEN) as client:
            for value in [ {'type':'get_states'}, {'type':'call_service','domain':'light','service':'turn_on','service_data':{'entity_id':'light.x'}},
                           {'type':'todo/item/list','entity_id':'todo.other'}, {'type':'todo/item/list','entity_id':'todo.indkob','id':1},
                           {'type':'hygge_storage/command','command':{'action':'location_remove'}},
                           {'type':'call_service','domain':'todo','service':'add_item','service_data':{'entity_id':'todo.other','item':'X'}} ]:
                before=len(sock.sent)
                with self.assertRaises(ha.HomeAssistantError): client.call(value)
                self.assertEqual(len(sock.sent),before)
            client.call({'type':'call_service','domain':'todo','service':'add_item','service_data':{'entity_id':'todo.indkob','item':'Synthetic','description':'{}'}})
            client.call({'type':'hygge_storage/command','command':{'action':'set','request_id':'synthetic','item_id':'x','quantity':'1','expected_revision':1}})

    def test_upstream_refusal_sanitized_and_transport_write_uncertain(self):
        def denied(v): return frame({'id':v['id'],'type':'result','success':False,'error':{'message':TOKEN}})
        with self.connection(Socket(denied)),ha.HomeAssistantSocket(TOKEN) as client:
            with self.assertRaises(ha.HomeAssistantError) as caught: client.call({'type':'todo/item/list','entity_id':'todo.indkob'})
            self.assertFalse(caught.exception.uncertain);self.assertNotIn(TOKEN,str(caught.exception))
        with self.connection(Socket(lambda _:b'')),ha.HomeAssistantSocket(TOKEN) as client:
            with self.assertRaises(ha.HomeAssistantError) as caught:
                client.call({'type':'call_service','domain':'todo','service':'remove_item','service_data':{'entity_id':'todo.indkob','item':['synthetic']}})
            self.assertTrue(caught.exception.uncertain);self.assertNotIn('private-token',str(caught.exception))

    def test_auth_extensions_invalid_frames_and_message_cap(self):
        for sock in (Socket(auth='auth_invalid'),Socket(headers=b'Sec-WebSocket-Extensions: permessage-deflate\r\n')):
            with self.connection(sock),self.assertRaises(ha.HomeAssistantError):
                with ha.HomeAssistantSocket(TOKEN): pass
            self.assertTrue(sock.closed)
        malformed=[b'\x81\x80'+b'abcd',b'\xc1\x00',b'\x09\x00',frame(b'x',opcode=0),
                   b'\x81\x7f'+struct.pack('!Q',ha.MAX_BYTES+1),frame(b'\xff'),frame(b'',opcode=8)]
        for data in malformed:
            with self.subTest(data=data[:10]),self.connection(Socket(lambda _:data)),ha.HomeAssistantSocket(TOKEN) as client:
                with self.assertRaises(ha.HomeAssistantError): client.call({'type':'todo/item/list','entity_id':'todo.indkob'})

    def test_frame_length_encoding_and_aggregate_fragment_budget(self):
        client=ha.HomeAssistantSocket(TOKEN)
        client.sock=Mock();client.deadline=ha.time.monotonic()+8
        for size in (125,126,65535,65536):
            payload=b'x'*size
            client._send(payload)
            self.assertEqual(client_frame(client.sock.sendall.call_args.args[0]),(1,payload))
        oversized=frame(b'x'*(ha.MAX_BYTES//2),fin=False)+frame(b'x'*(ha.MAX_BYTES//2+1),opcode=0)
        with self.connection(Socket(lambda _:oversized)),ha.HomeAssistantSocket(TOKEN) as client:
            with self.assertRaises(ha.HomeAssistantError): client.call({'type':'todo/item/list','entity_id':'todo.indkob'})

    def test_one_total_deadline_and_rest_size_or_identity_errors_are_sanitized(self):
        with self.connection(Socket()),ha.HomeAssistantSocket(TOKEN) as client:
            client.deadline=ha.time.monotonic()-1
            with self.assertRaises(ha.HomeAssistantError) as caught: client.call({'type':'todo/item/list','entity_id':'todo.indkob'})
            self.assertFalse(caught.exception.uncertain)
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,size):return b'x'*(ha.MAX_BYTES+1)
        client=ha.HomeAssistantSocket(TOKEN)
        with patch.object(client.opener,'open',return_value=Response()):
            with self.assertRaises(ha.HomeAssistantError):client.discounts()
        with patch.object(client.opener,'open',side_effect=OSError(TOKEN)):
            with self.assertRaises(ha.HomeAssistantError) as caught:client.discounts()
            self.assertNotIn(TOKEN,str(caught.exception))

    def test_discounts_fixed_rest_endpoint_bearer_no_redirect_and_budget(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,size):return b'{"entity_id":"sensor.shopping_discounts","state":"0","attributes":{}}'
        client=ha.HomeAssistantSocket(TOKEN)
        with patch.object(client.opener,'open',return_value=Response()) as opened:
            self.assertEqual(client.discounts()['entity_id'],'sensor.shopping_discounts')
        req=opened.call_args.args[0]
        self.assertEqual(req.full_url,'http://192.168.30.30/api/states/sensor.shopping_discounts')
        self.assertEqual(req.get_header('Authorization'),'Bearer '+TOKEN)
        self.assertIsNone(ha.NoRedirect().redirect_request(None,None,None,None,None,None))


if __name__=='__main__':unittest.main()
