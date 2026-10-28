"""Bounded stdlib RFC6455 transport for the trusted household service adapter.

The business adapter supplies validated operations. Tokens and raw HA responses
stay on home-core; no caller-supplied network origins or headers are supported.
A context shares one eight-second deadline and never retries a sent mutation.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import socket
import struct
import time
from typing import Any
import urllib.request

HOST, PORT, PATH = '192.168.30.30', 80, '/api/websocket'
MAX_BYTES, TIMEOUT = 1048576, 8
MAX_FRAMES, MAX_MESSAGES = 128, 64
GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class HomeAssistantError(ValueError):
    """Fixed public diagnostic, plus whether a sent mutation lacks a receipt."""
    def __init__(self, uncertain: bool = False):
        super().__init__('Home Assistant request unavailable; check current state before retrying')
        self.uncertain = uncertain


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def encode(value: Any) -> bytes:
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def decode(raw: bytes) -> dict[str, Any]:
    def invalid(_: str) -> None:
        raise ValueError('Invalid JSON number')
    value = json.loads(raw.decode('utf-8'), parse_constant=invalid)
    if not isinstance(value, dict): raise ValueError('Object required')
    return value


class HomeAssistantSocket:
    def __init__(self, token: str):
        if (not isinstance(token, str) or not 32 <= len(token) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise HomeAssistantError()
        self._token = token
        self.sock: socket.socket | None = None
        self.deadline: float | None = None
        self._buffer = bytearray()
        self._next_id = 0
        self._events: dict[int, Any] = {}
        self._subscriptions: set[int] = set()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _remaining(self) -> float:
        if self.deadline is None: raise HomeAssistantError()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise HomeAssistantError()
        return remaining

    def _read(self, size: int) -> bytes:
        if self.sock is None or size > MAX_BYTES: raise HomeAssistantError()
        while len(self._buffer) < size:
            self.sock.settimeout(self._remaining())
            chunk = self.sock.recv(min(65536, size - len(self._buffer)))
            if not chunk: raise HomeAssistantError()
            self._buffer.extend(chunk)
        value = bytes(self._buffer[:size])
        del self._buffer[:size]
        return value

    def _send(self, payload: bytes, opcode: int = 1) -> None:
        if (self.sock is None or len(payload) > MAX_BYTES
                or opcode >= 8 and len(payload) > 125): raise HomeAssistantError()
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126: header += bytes([0x80 | size])
        elif size < 65536: header += b'\xfe' + struct.pack('!H', size)
        else: header += b'\xff' + struct.pack('!Q', size)
        mask = os.urandom(4)
        frame = header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.settimeout(self._remaining())
        self.sock.sendall(frame)

    def _message(self) -> dict[str, Any]:
        parts = bytearray()
        fragmented = False
        for _ in range(MAX_FRAMES):
            head = self._read(2)
            final, opcode = bool(head[0] & 0x80), head[0] & 15
            if head[0] & 0x70 or head[1] & 0x80: raise HomeAssistantError()
            size = head[1] & 127
            if size == 126:
                size = struct.unpack('!H', self._read(2))[0]
                if size < 126: raise HomeAssistantError()
            elif size == 127:
                size = struct.unpack('!Q', self._read(8))[0]
                if size < 65536 or size & (1 << 63): raise HomeAssistantError()
            if opcode >= 8 and (not final or size > 125): raise HomeAssistantError()
            if size > MAX_BYTES or opcode < 8 and len(parts) + size > MAX_BYTES:
                raise HomeAssistantError()
            payload = self._read(size)
            if opcode == 9:
                self._send(payload, 10)
                continue
            if opcode == 10: continue
            if opcode == 8:
                if len(payload) == 1: raise HomeAssistantError()
                if len(payload) >= 2:
                    code = struct.unpack('!H', payload[:2])[0]
                    if code not in {1000,1001,1002,1003,1007,1008,1009,1010,1011,1012,1013,1014} and not 3000 <= code <= 4999:
                        raise HomeAssistantError()
                    payload[2:].decode('utf-8')
                try: self._send(payload, 8)
                except (OSError, ValueError): pass
                raise HomeAssistantError()
            if opcode == 1:
                if fragmented: raise HomeAssistantError()
                fragmented = not final
            elif opcode == 0:
                if not fragmented: raise HomeAssistantError()
            else: raise HomeAssistantError()
            parts.extend(payload)
            if final: return decode(bytes(parts))
        raise HomeAssistantError()

    def _upgrade(self) -> None:
        if self.sock is None: raise HomeAssistantError()
        key = base64.b64encode(os.urandom(16))
        request = (f'GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ').encode() + key + b'\r\n\r\n'
        self.sock.settimeout(self._remaining())
        self.sock.sendall(request)
        raw = bytearray()
        while b'\r\n\r\n' not in raw:
            if len(raw) >= 16384: raise HomeAssistantError()
            self.sock.settimeout(self._remaining())
            chunk = self.sock.recv(min(1024, 16384-len(raw)))
            if not chunk: raise HomeAssistantError()
            raw.extend(chunk)
        header, trailing = raw.split(b'\r\n\r\n', 1)
        self._buffer.extend(trailing)
        lines = header.decode('ascii').split('\r\n')
        if lines[0].split(' ', 2)[:2] != ['HTTP/1.1', '101']: raise HomeAssistantError()
        fields: dict[str, str] = {}
        for line in lines[1:]:
            if ':' not in line or line.startswith((' ', '\t')): raise HomeAssistantError()
            name, value = line.split(':', 1)
            name = name.lower()
            if name in fields: raise HomeAssistantError()
            fields[name] = value.strip()
        expected = base64.b64encode(hashlib.sha1(key+GUID).digest()).decode()
        if (fields.get('upgrade', '').lower() != 'websocket'
                or 'upgrade' not in {v.strip().lower() for v in fields.get('connection', '').split(',')}
                or fields.get('sec-websocket-accept') != expected
                or 'sec-websocket-extensions' in fields or 'sec-websocket-protocol' in fields):
            raise HomeAssistantError()

    def __enter__(self) -> HomeAssistantSocket:
        if self.sock is not None: raise HomeAssistantError()
        self.deadline = time.monotonic() + TIMEOUT
        self._buffer.clear()
        self._events.clear()
        self._subscriptions.clear()
        self._next_id = 0
        try:
            self.sock = socket.create_connection((HOST, PORT), timeout=self._remaining())
            self._upgrade()
            if self._message().get('type') != 'auth_required': raise HomeAssistantError()
            self._send(encode({'type':'auth', 'access_token':self._token}))
            if self._message().get('type') != 'auth_ok': raise HomeAssistantError()
            return self
        except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
            self._close()
            raise HomeAssistantError() from None

    def _close(self) -> None:
        if self.sock is not None:
            try: self.sock.close()
            except OSError: pass
            self.sock = None

    def __exit__(self, *_: Any) -> None:
        if self.sock is not None:
            try:
                self.sock.settimeout(min(0.1, self._remaining()))
                self._send(struct.pack('!H',1000),8)
            except (OSError, ValueError): pass
        self._close()
        self._buffer.clear()
        self._events.clear()
        self._subscriptions.clear()

    @staticmethod
    def _validate(command: Any) -> bool:
        if not isinstance(command,dict) or 'id' in command: raise HomeAssistantError()
        kind = command.get('type')
        if kind == 'todo/item/list':
            if set(command) != {'type','entity_id'} or command['entity_id'] != 'todo.indkob': raise HomeAssistantError()
            return False
        if kind == 'hygge_storage/subscribe':
            if set(command) != {'type'}: raise HomeAssistantError()
            return False
        if kind == 'hygge_storage/command':
            data = command.get('command')
            if (set(command) != {'type','command'} or not isinstance(data,dict)
                    or data.get('action') not in {'item_add','item_edit','item_remove','set'}): raise HomeAssistantError()
            return True
        if kind == 'call_service':
            if (set(command) != {'type','domain','service','service_data'} or command.get('domain') != 'todo'
                    or command.get('service') not in {'add_item','update_item','remove_item'}): raise HomeAssistantError()
            data = command['service_data']
            if not isinstance(data,dict) or data.get('entity_id') != 'todo.indkob': raise HomeAssistantError()
            allowed = {'add_item':{'entity_id','item','description'},
                       'update_item':{'entity_id','item','rename','status','description'},
                       'remove_item':{'entity_id','item'}}[command['service']]
            if set(data)-allowed or 'item' not in data: raise HomeAssistantError()
            return True
        raise HomeAssistantError()

    def _event(self, message: dict[str, Any]) -> bool:
        if message.get('type') != 'event': return False
        identifier = message.get('id')
        if type(identifier) is not int or identifier not in self._subscriptions or 'event' not in message:
            raise HomeAssistantError()
        self._events[identifier] = message['event']
        return True

    def call(self, command: dict[str, Any]) -> Any:
        mutates = self._validate(command)
        sent = False
        try:
            if self.sock is None: raise HomeAssistantError()
            self._next_id += 1
            identifier = self._next_id
            if command['type'] == 'hygge_storage/subscribe':
                if len(self._subscriptions) >= 4: raise HomeAssistantError()
                self._subscriptions.add(identifier)
            payload = encode({**command, 'id':identifier})
            if len(payload) > MAX_BYTES: raise HomeAssistantError()
            sent = True  # sendall may fail after a partial write; never retry it.
            self._send(payload)
            for _ in range(MAX_MESSAGES):
                message = self._message()
                if self._event(message): continue
                if (message.get('type') != 'result' or type(message.get('id')) is not int
                        or message['id'] != identifier or type(message.get('success')) is not bool): raise HomeAssistantError()
                if message['success'] is not True:
                    raise HomeAssistantError(uncertain=mutates)
                return message.get('result')
            raise HomeAssistantError()
        except (OSError, ValueError, UnicodeError, TypeError, RecursionError, OverflowError):
            # A failed mutation may already have taken effect. Even an HA error
            # is not proof that all service-side effects were rolled back.
            raise HomeAssistantError(uncertain=sent and mutates) from None

    def snapshot(self) -> dict[str, Any]:
        self.call({'type':'hygge_storage/subscribe'})
        identifier = self._next_id
        try:
            for _ in range(MAX_MESSAGES):
                if identifier in self._events:
                    event = self._events.pop(identifier)
                    if not isinstance(event,dict): raise HomeAssistantError()
                    return event
                if not self._event(self._message()): raise HomeAssistantError()
            raise HomeAssistantError()
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise HomeAssistantError() from None

    def discounts(self) -> dict[str, Any]:
        if self.deadline is None: self.deadline = time.monotonic()+TIMEOUT
        url = f'http://{HOST}/api/states/sensor.shopping_discounts'
        request = urllib.request.Request(url, headers={'Authorization':'Bearer '+self._token},method='GET')
        try:
            with self.opener.open(request,timeout=self._remaining()) as response:
                if response.status != 200: raise HomeAssistantError()
                raw = response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES: raise HomeAssistantError()
                self._remaining()
                result = decode(raw)
                if result.get('entity_id') != 'sensor.shopping_discounts': raise HomeAssistantError()
                return result
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise HomeAssistantError() from None
