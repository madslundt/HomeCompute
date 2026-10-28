"""Finite, in-memory Wyoming synthesis through the dedicated local Piper voice."""
from __future__ import annotations

import io
import json
import math
import socket
import time
from typing import Any, Callable
import wave

HOST = '172.28.202.2'
PORT = 10200
VOICE = 'da_DK-talesyntese-medium'
MAX_HEADER = 16 * 1024
MAX_DATA = 64 * 1024
MAX_CHUNK = 1024 * 1024
MAX_PCM = 8 * 1024 * 1024
MAX_EVENTS = 4096
MAX_DURATION = 180


class WyomingError(RuntimeError):
    """Fixed errors exclude speech text and remote response contents."""


class _Reader:
    def __init__(self, connection: Any, deadline: float):
        self.connection, self.deadline = connection, deadline
        self.buffer = bytearray()

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise WyomingError('Local speech deadline exceeded')
        return remaining

    def receive(self, maximum: int) -> bytes:
        self.connection.settimeout(self.remaining())
        value = self.connection.recv(maximum)
        if not value:
            raise WyomingError('Local speech ended early')
        return value

    def exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            self.buffer.extend(self.receive(min(65536, size - len(self.buffer))))
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def event(self) -> tuple[str, dict[str, Any], bytes]:
        while b'\n' not in self.buffer:
            if len(self.buffer) >= MAX_HEADER:
                raise WyomingError('Local speech event header exceeded')
            self.buffer.extend(self.receive(min(4096, MAX_HEADER - len(self.buffer))))
        end = self.buffer.index(b'\n')
        if end + 1 > MAX_HEADER:
            raise WyomingError('Local speech event header exceeded')
        header = json.loads(bytes(self.buffer[:end]))
        del self.buffer[:end + 1]
        if not isinstance(header, dict) or not isinstance(header.get('type'), str):
            raise WyomingError('Invalid local speech event')
        data = header.get('data', {})
        if not isinstance(data, dict):
            raise WyomingError('Invalid local speech event data')
        lengths = []
        for key, maximum in (('data_length', MAX_DATA), ('payload_length', MAX_CHUNK)):
            value = header.get(key, 0)
            if type(value) is not int or not 0 <= value <= maximum:
                raise WyomingError('Local speech frame exceeded')
            lengths.append(value)
        if lengths[0]:
            extra = json.loads(self.exact(lengths[0]))
            if not isinstance(extra, dict):
                raise WyomingError('Invalid local speech event data')
            data = {**data, **extra}
        return header['type'], data, self.exact(lengths[1])


def _format(data: dict[str, Any]) -> tuple[int, int, int]:
    values = tuple(data.get(key) for key in ('rate', 'width', 'channels'))
    if (not all(type(value) is int for value in values)
            or not 8000 <= values[0] <= 48000 or values[1:] != (2, 1)):
        raise WyomingError('Unsupported local speech PCM format')
    return values


def synthesize(text: str, *, timeout: float = 20,
               connector: Callable[..., Any] = socket.create_connection) -> bytes:
    """Return bounded mono PCM WAV; the endpoint and Danish voice are fixed.

    ``connector`` is an integration-test seam, never an endpoint selected from
    a request. One absolute deadline covers connection, request and all frames.
    """
    if (not isinstance(text, str) or not text.strip() or len(text) > 2000 or '\0' in text
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 60):
        raise ValueError('Bounded speech text and timeout required')
    deadline = time.monotonic() + timeout
    data = json.dumps({'text': text, 'voice': {'name': VOICE}}, ensure_ascii=False).encode()
    request = json.dumps({'type': 'synthesize', 'data_length': len(data)}).encode() + b'\n' + data
    try:
        with connector((HOST, PORT), timeout=timeout) as connection:
            reader = _Reader(connection, deadline)
            connection.settimeout(reader.remaining())
            connection.sendall(request)
            audio_format = None
            pcm = bytearray()
            for _ in range(MAX_EVENTS):
                event, event_data, payload = reader.event()
                if event == 'audio-start':
                    if audio_format is not None or pcm or payload:
                        raise WyomingError('Invalid local speech start')
                    audio_format = _format(event_data)
                elif event == 'audio-chunk':
                    if audio_format is None or _format(event_data) != audio_format or not payload or len(payload) % 2:
                        raise WyomingError('Invalid local speech chunk')
                    if len(pcm) + len(payload) > min(MAX_PCM, audio_format[0] * 2 * MAX_DURATION):
                        raise WyomingError('Local speech audio budget exceeded')
                    pcm.extend(payload)
                elif event == 'audio-stop':
                    if audio_format is None or not pcm or payload:
                        raise WyomingError('Invalid local speech stop')
                    output = io.BytesIO()
                    with wave.open(output, 'wb') as wav:
                        wav.setframerate(audio_format[0])
                        wav.setsampwidth(2)
                        wav.setnchannels(1)
                        wav.writeframes(bytes(pcm))
                    return output.getvalue()
                else:
                    raise WyomingError('Local speech synthesis refused')
            raise WyomingError('Local speech event budget exceeded')
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        raise WyomingError('Local speech synthesis unavailable') from None
