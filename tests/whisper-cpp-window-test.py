#!/usr/bin/env python3
"""Exact PCM coverage and all-or-nothing native-window adapter regressions."""
from __future__ import annotations

from array import array
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('window_adapter', ROOT / 'deploy/whisper-cpp-stt/adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ADAPTER
SPEC.loader.exec_module(ADAPTER)
CODEC = ADAPTER._CODEC_MODULE


def wav(frames):
    output = io.BytesIO()
    # Nonconstant samples expose dropped, duplicated or reordered boundaries.
    samples = array('h', (index % 32001 - 16000 for index in range(frames)))
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
        target.writeframes(samples.tobytes())
    return output.getvalue()


def pcm(audio):
    with wave.open(io.BytesIO(audio), 'rb') as source:
        return source.readframes(source.getnframes())


def verbose(text=' Ord', language='danish', *, end=1.0):
    return json.dumps({'text': text + '\n', 'duration': 9999, 'language': language, 'task': 'transcribe',
                       'segments': [{'id': 0, 'start': 0.0, 'end': end, 'text': text, 'tokens': [10, 20]}]}).encode()


class WindowContractTests(unittest.TestCase):
    def test_exact_contiguous_pcm_with_nonempty_tail_and_silence(self):
        for frames in (480001, 527999, 528000, 960000, 960017):
            with self.subTest(frames=frames):
                original = wav(frames)
                windows = list(CODEC.native_audio_windows(original))
                self.assertEqual(b''.join(pcm(item[2]) for item in windows), pcm(original))
                previous_end = 0
                for offset, duration, _audio in windows:
                    self.assertAlmostEqual(offset, previous_end)
                    previous_end = offset + duration
                self.assertEqual(sum(round(item[1] * 16000) for item in windows), frames)
                self.assertTrue(all(3 <= item[1] <= 30 for item in windows))
                if frames == 528000:
                    self.assertEqual([item[1] for item in windows], [30, 3])
                if frames == 480001:
                    self.assertEqual([round(item[1] * 16000) for item in windows], [240000, 240001])
        original = wav(480001)
        original = original[:44] + bytes(len(original) - 44)
        self.assertEqual(b''.join(pcm(item[2]) for item in CODEC.native_audio_windows(original)), pcm(original))

    def test_all_formats_join_without_changing_native_text(self):
        for format_ in ('text', 'json', 'verbose_json'):
            with self.subTest(format_=format_):
                words = ('First', '', 'Sidst')
                raw = [word.encode() if format_ == 'text' else json.dumps({'text': word}).encode()
                       for word in words]
                if format_ == 'verbose_json':
                    raw = [verbose(' Først'), json.dumps({'text': '', 'segments': [], 'language': 'english',
                                                         'task': 'transcribe'}).encode(), verbose(' sidst')]
                payload, content_type = CODEC.combine_native_windows(
                    [(i * 30, 30 if i < 2 else 1, response) for i, response in enumerate(raw)], format_, 61)
                result = payload.decode() if format_ == 'text' else json.loads(payload)['text']
                self.assertEqual(result, ' Først\n sidst\n' if format_ == 'verbose_json' else 'First\nSidst')
                self.assertIn('text/plain' if format_ == 'text' else 'application/json', content_type)
                if format_ == 'verbose_json':
                    parsed = json.loads(payload)
                    self.assertEqual(parsed['language'], 'danish')
                    self.assertEqual(parsed['duration'], 61)

    def test_global_timestamps_unicode_anchors_ids_and_mixed_metadata(self):
        payload, _ = CODEC.combine_native_windows(
            [(0, 30, verbose(' Æble 🧵', 'danish', end=29.99)),
             (30, 1.25, verbose(' Words', 'english', end=1.25))], 'verbose_json', 31.25)
        result = json.loads(payload)
        self.assertEqual(result['language'], 'mixed')
        self.assertEqual(result['duration'], 31.25)
        self.assertEqual(result['language_windows'], [
            {'start': 0, 'end': 30, 'language': 'danish'},
            {'start': 30, 'end': 31.25, 'language': 'english'}])
        self.assertEqual([item['id'] for item in result['segments']], [0, 1])
        self.assertEqual((result['segments'][1]['start'], result['segments'][1]['end']), (30, 31.25))
        for segment in result['segments']:
            self.assertEqual(result['text'][segment['start_offset']:segment['end_offset']], segment['text'])
            self.assertEqual(segment['kind'], 'segment')
            self.assertEqual(segment['tokens'], [10, 20])
            self.assertNotIn('words', segment)

    def test_unknown_boundary_timing_stays_unknown_after_global_aggregation(self):
        overrun = json.dumps({'text': 'Acknowledgement.\n', 'language': 'english', 'task': 'transcribe',
                              'segments': [{'start': 29.58, 'end': 30.42, 'text': 'Acknowledgement.'}]}).encode()
        payload, _ = CODEC.combine_native_windows(
            [(0, 30, verbose('First.', 'english')), (30, 30, overrun),
             (60, 5, verbose('Last.', 'english', end=5))], 'verbose_json', 65)
        result = json.loads(payload)
        self.assertEqual(result['text'], 'First.\nAcknowledgement.\nLast.\n')
        self.assertEqual((result['segments'][2]['start'], result['segments'][2]['end']), (60, 65))
        unknown = result['segments'][1]
        self.assertIsNone(unknown['start']); self.assertIsNone(unknown['end'])
        self.assertEqual(unknown['timing_status'], 'unknown_out_of_bounds')
        for segment in result['segments']:
            self.assertEqual(result['text'][segment['start_offset']:segment['end_offset']], segment['text'])

    def test_mismatched_native_serialization_never_gets_invented_anchors(self):
        bad = json.dumps({'text': 'Canonical', 'segments': [{'start': 0, 'end': 1, 'text': 'Different'}]}).encode()
        payload, _ = CODEC.combine_native_windows([(0, 30, bad), (30, 1, verbose())], 'verbose_json', 31)
        result = json.loads(payload)
        self.assertEqual(result['text'], 'Canonical Ord\n')
        self.assertNotIn('start_offset', result['segments'][0])
        segment = result['segments'][1]
        self.assertEqual(result['text'][segment['start_offset']:segment['end_offset']], segment['text'])

    def test_local_bounds_and_cumulative_response_budget_refuse(self):
        invalid = json.dumps({'text': 'x\n', 'segments': [{'start': 1.01, 'end': 2, 'text': 'x'}]}).encode()
        with self.assertRaisesRegex(ADAPTER.RequestError, 'out-of-bounds'):
            CODEC.combine_native_windows([(30, 1, invalid)], 'verbose_json', 31)
        with patch.object(CODEC, 'MAX_RESPONSE_BYTES', 15), self.assertRaisesRegex(ADAPTER.RequestError, 'too large'):
            CODEC.combine_native_windows([(0, 30, b'1234567890'), (30, 1, b'abcdefghij')], 'text', 31)


class NativeDispatchTests(unittest.TestCase):
    def test_short_original_wav_fields_format_and_timeout_are_unchanged(self):
        for frames in (1, 1600, 480000):
            original = wav(frames)
            fields = [('response_format', 'json'), ('language', 'auto'), ('prompt', 'Æble')]
            def native(body, content_type, timeout):
                parsed, audio = ADAPTER.parse_multipart(body, content_type)
                self.assertEqual(audio, original)
                self.assertEqual(parsed, dict(fields))
                self.assertEqual(timeout, 900)
                return b'{"text":"Original"}'
            with patch.object(ADAPTER, '_native_request', side_effect=native) as call, \
                    patch.object(ADAPTER, 'native_audio_windows', side_effect=AssertionError('short path must not partition')):
                payload, _ = ADAPTER.transcribe_audio(original, frames / 16000, 'json', fields, 900)
            self.assertEqual(json.loads(payload)['text'], 'Original')
            self.assertEqual(call.call_count, 1)

    def test_long_calls_cover_frames_and_preserve_explicit_or_auto_language(self):
        original = wav(976000)
        for language in ('auto', 'da'):
            fields = [('response_format', 'json'), ('language', language), ('prompt', 'Neutral'),
                      ('temperature', '0.0'), ('temperature_inc', '0.0')]
            observed = []
            def native(body, content_type, timeout):
                parsed, audio = ADAPTER.parse_multipart(body, content_type)
                self.assertEqual(parsed, dict(fields))
                self.assertTrue(0 < timeout <= 900)
                observed.append(audio)
                return b'{"text":"Native"}'
            with patch.object(ADAPTER, '_native_request', side_effect=native):
                result, _ = ADAPTER.transcribe_audio(original, 61, 'json', fields, 900)
            self.assertEqual(b''.join(pcm(audio) for audio in observed), pcm(original))
            self.assertEqual([len(pcm(audio)) // 2 for audio in observed], [480000, 248000, 248000])
            self.assertEqual(json.loads(result)['text'], 'Native\nNative\nNative')

    def test_long_native_format_is_preserved_for_every_window(self):
        original = wav(496000)
        for format_ in ('text', 'json', 'verbose_json'):
            def native(body, content_type, timeout):
                fields, _audio = ADAPTER.parse_multipart(body, content_type)
                self.assertEqual(fields['response_format'], format_)
                if format_ == 'text':
                    return b'Native'
                if format_ == 'json':
                    return b'{"text":"Native"}'
                return verbose('Native')
            with self.subTest(format_=format_), patch.object(ADAPTER, '_native_request', side_effect=native) as call:
                payload, _ = ADAPTER.transcribe_audio(original, 31, format_, [('response_format', format_)], 900)
            self.assertEqual(call.call_count, 2)
            text = payload.decode() if format_ == 'text' else json.loads(payload)['text']
            self.assertEqual(text, 'Native\nNative\n' if format_ == 'verbose_json' else 'Native\nNative')

    def test_http_later_window_error_returns_error_without_partial_text_or_provenance(self):
        server = ADAPTER.CandidateServer(ADAPTER.CandidateConfig(Path('/unused'), Path('/unused')),
                                        b'synthetic-secret-value', ('127.0.0.1', 0))
        self.addCleanup(server.server_close)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start(); self.addCleanup(server.shutdown)
        body, content_type = ADAPTER.make_native_multipart([('model', 'whisper')], wav(496000))
        request = urllib.request.Request(
            f'http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions', data=body,
            headers={'Authorization': 'Bearer synthetic-secret-value', 'Content-Type': content_type})
        with patch.object(ADAPTER, '_native_request', side_effect=[b'{"text":"Private partial words"}',
                          ADAPTER.RequestError(502, 'Speech backend is unavailable')]), \
                self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(failure.exception.code, 502)
        self.assertIsNone(failure.exception.headers.get('X-HomeCompute-STT-Image-ID'))
        self.assertEqual(json.loads(failure.exception.read()), {'error': 'Speech backend is unavailable'})
        failure.exception.close()
        self.assertTrue(server.inflight.acquire(blocking=False)); server.inflight.release()

    def test_one_deadline_is_decremented_and_late_result_never_succeeds(self):
        original = wav(496000)
        fields = [('response_format', 'text'), ('language', 'auto')]
        with patch.object(ADAPTER.time, 'monotonic', side_effect=[0, 1, 4, 5, 9, 9]), \
                patch.object(ADAPTER, '_native_request', return_value=b'words') as native:
            ADAPTER.transcribe_audio(original, 31, 'text', fields, 10)
            self.assertEqual([item.args[2] for item in native.call_args_list], [9, 5])
        with patch.object(ADAPTER.time, 'monotonic', side_effect=[0, 1, 4, 10]), \
                patch.object(ADAPTER, '_native_request', return_value=b'words') as native, \
                self.assertRaisesRegex(ADAPTER.RequestError, 'deadline'):
            ADAPTER.transcribe_audio(original, 31, 'text', fields, 10)
        self.assertEqual(native.call_count, 1)
        with patch.object(ADAPTER.time, 'monotonic', side_effect=[0, 1, 10]), \
                patch.object(ADAPTER, '_native_request', return_value=b'words'), \
                self.assertRaisesRegex(ADAPTER.RequestError, 'deadline'):
            ADAPTER.transcribe_audio(original, 31, 'text', fields, 10)

    def test_failed_later_window_returns_no_partial_canonical_result(self):
        with patch.object(ADAPTER, '_native_request', side_effect=[b'{"text":"First"}',
                          ADAPTER.RequestError(502, 'Speech backend is unavailable')]) as native, \
                self.assertRaisesRegex(ADAPTER.RequestError, 'unavailable'):
            ADAPTER.transcribe_audio(wav(496000), 31, 'json', [('response_format', 'json')], 900)
        self.assertEqual(native.call_count, 2)

    def test_whole_upload_admission_and_health_remain_busy_between_native_calls(self):
        config = ADAPTER.CandidateConfig(Path('/unused'), Path('/unused'), device='cuda', native_timeout_seconds=10)
        native_health = {'status': 'ok', 'compute_backend': 'cuda', 'gpu_model_buffer_bytes': 4096,
                         'inference_busy': False}
        server = ADAPTER.CandidateServer(config, b'synthetic-secret-value', ('127.0.0.1', 0), native_health=native_health)
        self.addCleanup(server.server_close)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start(); self.addCleanup(server.shutdown)
        between = threading.Event(); resume = threading.Event()
        original_windows = ADAPTER.native_audio_windows
        def windows(audio):
            for index, window in enumerate(original_windows(audio)):
                if index == 1:
                    between.set()
                    if not resume.wait(5):
                        raise AssertionError('test did not resume second window')
                yield window
        url = f'http://127.0.0.1:{server.server_address[1]}'
        headers = {'Authorization': 'Bearer synthetic-secret-value'}
        body, content_type = ADAPTER.make_native_multipart([('model', 'whisper'), ('response_format', 'json')], wav(496000))
        received = []
        def post():
            try:
                request = urllib.request.Request(url + '/v1/audio/transcriptions', data=body,
                                                 headers={**headers, 'Content-Type': content_type})
                with urllib.request.urlopen(request, timeout=8) as response:
                    received.append((response.status, json.loads(response.read())))
            except BaseException as error:
                received.append(error)
        with patch.object(ADAPTER, 'native_audio_windows', side_effect=windows), \
                patch.object(ADAPTER, '_native_health', return_value=native_health), \
                patch.object(ADAPTER, '_native_request', return_value=b'{"text":"Words"}'):
            client = threading.Thread(target=post, daemon=True); client.start()
            try:
                self.assertTrue(between.wait(3))
                with urllib.request.urlopen(url + '/health', timeout=2) as response:
                    health = json.loads(response.read())
                self.assertIs(health['inference_busy'], True)
                self.assertIs(health['native_inference_busy'], False)
                tiny_body, tiny_type = ADAPTER.make_native_multipart([('model', 'whisper')], wav(1600))
                second = urllib.request.Request(url + '/v1/audio/transcriptions', data=tiny_body,
                                                headers={**headers, 'Content-Type': tiny_type})
                with self.assertRaises(urllib.error.HTTPError) as busy:
                    urllib.request.urlopen(second, timeout=2)
                self.assertEqual(busy.exception.code, 429); busy.exception.close()
            finally:
                resume.set(); client.join(8)
        self.assertEqual(received, [(200, {'text': 'Words\nWords'})])


if __name__ == '__main__':
    unittest.main(verbosity=2)
