"""Exercise the real Wyoming socket framing, finite budgets and timeout behavior."""
import io
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import wyoming_tts_client as speech


def event(kind, data=None, payload=b'', *, inline=False):
    header = {'type': kind, 'payload_length': len(payload)}
    encoded = b''
    if data:
        if inline:
            header['data'] = data
        else:
            encoded = json.dumps(data).encode()
            header['data_length'] = len(encoded)
    return json.dumps(header).encode() + b'\n' + encoded + payload


FORMAT = {'rate': 22050, 'width': 2, 'channels': 1}
PCM = b'\x01\x00\xff\xff' * 100


class Server:
    def __init__(self, response, *, delay=0, fragment=False):
        self.response, self.delay, self.fragment = response, delay, fragment
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.request = None
        self.connection_targets = []
        self.errors = []
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.listener.close()
        self.thread.join(timeout=2)
        if self.errors:
            raise self.errors[0]

    def connector(self, target, **options):
        self.connection_targets.append(target)
        return socket.create_connection(self.listener.getsockname(), **options)

    def serve(self):
        try:
            with self.listener.accept()[0] as connection:
                connection.settimeout(1)
                with connection.makefile('rb') as stream:
                    header = json.loads(stream.readline())
                    data = json.loads(stream.read(header['data_length']))
                    self.request = (header, data)
                    if self.delay:
                        time.sleep(self.delay)
                    if self.fragment:
                        for start in range(0, len(self.response), 7):
                            connection.sendall(self.response[start:start + 7])
                    else:
                        connection.sendall(self.response)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.errors.append(error)


class WyomingClientTests(unittest.TestCase):
    def test_protocol_request_and_fragmented_audio_produce_exact_pcm_wav(self):
        response = (event('audio-start', FORMAT) + event('audio-chunk', FORMAT, PCM[:200], inline=True)
                    + event('audio-chunk', FORMAT, PCM[200:]) + event('audio-stop'))
        with Server(response, fragment=True) as server:
            result = speech.synthesize('Dette er en dansk talebesked.', connector=server.connector)
        self.assertEqual(server.connection_targets, [(speech.HOST, speech.PORT)])
        self.assertEqual(server.request[0]['type'], 'synthesize')
        self.assertEqual(server.request[1], {'text': 'Dette er en dansk talebesked.',
                                            'voice': {'name': 'da_DK-talesyntese-medium'}})
        with wave.open(io.BytesIO(result), 'rb') as wav:
            self.assertEqual((wav.getframerate(), wav.getsampwidth(), wav.getnchannels()), (22050, 2, 1))
            self.assertEqual(wav.readframes(wav.getnframes()), PCM)

    def test_error_early_eof_bad_order_or_mismatched_audio_fail_without_remote_text(self):
        for response in (
            event('error', {'text': 'PRIVATE_REMOTE_AUDIO_TEXT'}),
            b'',
            event('audio-start', FORMAT),
            event('audio-chunk', FORMAT, PCM),
            event('audio-start', FORMAT) + event('audio-start', FORMAT),
            event('audio-start', FORMAT) + event('audio-stop'),
            event('audio-start', FORMAT) + event('audio-chunk', {**FORMAT, 'rate': 16000}, PCM),
            event('audio-start', {**FORMAT, 'channels': 2}),
            event('audio-start', FORMAT) + event('audio-chunk', FORMAT, b'\x01'),
        ):
            with self.subTest(response=response[:50]), Server(response) as server:
                with self.assertRaises(speech.WyomingError) as caught:
                    speech.synthesize('Private input text.', connector=server.connector)
                self.assertNotIn('PRIVATE_REMOTE_AUDIO_TEXT', str(caught.exception))
                self.assertNotIn('Private input text.', str(caught.exception))

    def test_malicious_frame_lengths_and_oversize_headers_are_refused(self):
        for response in (
            b'x' * speech.MAX_HEADER,
            b'[]\n', b'{invalid}\n',
            b'{"type":"audio-start","data":[]}\n',
            b'{"type":"audio-start","data_length":true}\n',
            b'{"type":"audio-start","payload_length":-1}\n',
            ('{"type":"audio-chunk","payload_length":%d}\n' % (speech.MAX_CHUNK + 1)).encode(),
            ('{"type":"audio-start","data_length":%d}\n' % (speech.MAX_DATA + 1)).encode(),
        ):
            with self.subTest(response=response[:60]), Server(response) as server:
                with self.assertRaises(speech.WyomingError):
                    speech.synthesize('Test.', connector=server.connector)

    def test_audio_bytes_duration_and_event_count_are_bounded(self):
        response = event('audio-start', FORMAT) + event('audio-chunk', FORMAT, PCM) + event('audio-stop')
        for setting, value in (('MAX_PCM', len(PCM) - 2), ('MAX_DURATION', 0), ('MAX_EVENTS', 2)):
            with self.subTest(setting=setting), patch.object(speech, setting, value), Server(response) as server:
                with self.assertRaises(speech.WyomingError):
                    speech.synthesize('Test.', connector=server.connector)

    def test_response_timeout_is_finite_and_deadline_is_total(self):
        with Server(event('audio-start', FORMAT), delay=0.15) as server:
            start = time.monotonic()
            with self.assertRaises(speech.WyomingError):
                speech.synthesize('Test.', timeout=0.05, connector=server.connector)
            self.assertLess(time.monotonic() - start, 0.14)
        fake = unittest.mock.Mock()
        reader = speech._Reader(fake, time.monotonic() - 1)
        with self.assertRaises(speech.WyomingError):
            reader.receive(1)
        fake.recv.assert_not_called()

    def test_invalid_text_and_unbounded_timeout_do_not_connect(self):
        connector = unittest.mock.Mock()
        for text, timeout in (('', 20), ('x' * 2001, 20), ('bad\0text', 20), ('Test', 0),
                              ('Test', float('inf')), ('Test', 61), ('Test', True)):
            with self.assertRaises(ValueError):
                speech.synthesize(text, timeout=timeout, connector=connector)
        connector.assert_not_called()


if __name__ == '__main__':
    unittest.main()
