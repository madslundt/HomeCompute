"""Pinned CPU-only Silero inference through the existing whisper.cpp C API."""
from __future__ import annotations

from array import array
import argparse
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
PIN = json.loads((ROOT / "vad-pin.json").read_text())
MODEL = ROOT / PIN["filename"]
LIBRARY = Path("/usr/local/lib/libwhisper.so")


class Params(C.Structure):
    _fields_ = [("n_threads", C.c_int), ("use_gpu", C.c_bool), ("gpu_device", C.c_int)]


def verify_model(path=MODEL):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != PIN["size"]:
            raise RuntimeError("Invalid VAD model")
        raw = b""
        while len(raw) <= PIN["size"]:
            part = os.read(fd, min(65536, PIN["size"] + 1 - len(raw)))
            if not part:break
            raw += part
        after = os.fstat(fd)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if (len(raw) != PIN["size"] or hashlib.sha256(raw).hexdigest() != PIN["sha256"]
                or identity(before) != identity(after)
                or identity(after) != identity(os.stat(path, follow_symlinks=False))):
            raise RuntimeError("VAD model identity mismatch")
    finally:
        os.close(fd)


def probabilities(pcm: bytes, *, model_path=MODEL, library_path=LIBRARY) -> list[float]:
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 1200 * 16000 * 2:
        raise ValueError("Invalid bounded VAD audio")
    verify_model(model_path)
    if C.sizeof(Params) != 12:
        raise RuntimeError("Unsupported native VAD ABI")
    lib = C.CDLL(str(library_path))
    lib.whisper_vad_default_context_params.argtypes = []
    lib.whisper_vad_default_context_params.restype = Params
    lib.whisper_vad_init_from_file_with_params.argtypes = [C.c_char_p, Params]
    lib.whisper_vad_init_from_file_with_params.restype = C.c_void_p
    lib.whisper_vad_detect_speech.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_int]
    lib.whisper_vad_detect_speech.restype = C.c_bool
    lib.whisper_vad_n_probs.argtypes = [C.c_void_p]
    lib.whisper_vad_n_probs.restype = C.c_int
    lib.whisper_vad_probs.argtypes = [C.c_void_p]
    lib.whisper_vad_probs.restype = C.POINTER(C.c_float)
    lib.whisper_vad_free.argtypes = [C.c_void_p]
    lib.whisper_vad_free.restype = None
    callback_type = C.CFUNCTYPE(None, C.c_int, C.c_void_p, C.c_void_p)
    lib.whisper_log_set.argtypes = [callback_type, C.c_void_p]
    lib.whisper_log_set.restype = None
    errors = []

    @callback_type
    def log(level, _message_pointer, _data):
        # Never copy or emit native log values. A native compute error makes the
        # entire observation unknown, even if the C function returns true.
        if level == 4:errors.append(True)

    context = None
    lib.whisper_log_set(log, None)
    try:
        params = lib.whisper_vad_default_context_params()
        params.n_threads, params.use_gpu, params.gpu_device = 1, False, 0
        context = lib.whisper_vad_init_from_file_with_params(os.fsencode(model_path), params)
        if not context or errors:
            raise RuntimeError("Native VAD initialization failed")
        samples = array("h", pcm)
        if sys.byteorder != "little":samples.byteswap()
        floats = array("f", (value / 32768.0 for value in samples))
        pointer = (C.c_float * len(floats)).from_buffer(floats)
        if not lib.whisper_vad_detect_speech(context, pointer, len(floats)) or errors:
            raise RuntimeError("Native VAD observation failed")
        count = lib.whisper_vad_n_probs(context)
        if count != (len(samples) + 511) // 512:
            raise RuntimeError("Native VAD frame count differs")
        result = lib.whisper_vad_probs(context)
        if not result:
            raise RuntimeError("Native VAD probabilities unavailable")
        values = [float(result[i]) for i in range(count)]
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise RuntimeError("Invalid native VAD probability")
        return values
    finally:
        if context:lib.whisper_vad_free(context)
        lib.whisper_log_set(callback_type(), None)


def provision():
    if MODEL.exists() or MODEL.is_symlink():
        verify_model();return
    url = "https://huggingface.co/" + PIN["repository"] + "/resolve/" + PIN["snapshot"] + "/" + PIN["filename"]
    with urllib.request.urlopen(url, timeout=60) as response:
        if not response.url.startswith("https://"):
            raise RuntimeError("Invalid public VAD artifact response")
        raw = response.read(PIN["size"] + 1)
    if len(raw) != PIN["size"] or hashlib.sha256(raw).hexdigest() != PIN["sha256"]:
        raise RuntimeError("Public VAD artifact identity mismatch")
    fd = os.open(MODEL, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, "wb") as stream:stream.write(raw)
    verify_model()


if __name__ == "__main__":
    parser = argparse.ArgumentParser();parser.add_argument("--provision", action="store_true", required=True)
    parser.parse_args();provision()


class SegmentParams(C.Structure):
    _fields_ = [("threshold", C.c_float), ("min_speech_duration_ms", C.c_int),
                ("min_silence_duration_ms", C.c_int), ("max_speech_duration_s", C.c_float),
                ("speech_pad_ms", C.c_int), ("samples_overlap", C.c_float)]


def speech_spans(pcm: bytes, *, model_path=MODEL, library_path=LIBRARY) -> list[list[int]]:
    """Unpadded native-default speech segments on raw PCM; never crop ASR audio.

    Segment hysteresis and native short-gap merging remain unchanged. These
    segments authorize only a local ranking comparison, never speech coverage.
    """
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 30 * 16000 * 2:
        raise ValueError("Invalid bounded local-ranking VAD audio")
    verify_model(model_path)
    if C.sizeof(Params) != 12 or C.sizeof(SegmentParams) != 24:
        raise RuntimeError("Unsupported native VAD ABI")
    lib = C.CDLL(str(library_path))
    declarations = {
        "whisper_vad_default_context_params": ([], Params),
        "whisper_vad_init_from_file_with_params": ([C.c_char_p, Params], C.c_void_p),
        "whisper_vad_detect_speech": ([C.c_void_p, C.POINTER(C.c_float), C.c_int], C.c_bool),
        "whisper_vad_n_probs": ([C.c_void_p], C.c_int),
        "whisper_vad_probs": ([C.c_void_p], C.POINTER(C.c_float)),
        "whisper_vad_default_params": ([], SegmentParams),
        "whisper_vad_segments_from_probs": ([C.c_void_p, SegmentParams], C.c_void_p),
        "whisper_vad_segments_n_segments": ([C.c_void_p], C.c_int),
        "whisper_vad_segments_get_segment_t0": ([C.c_void_p, C.c_int], C.c_float),
        "whisper_vad_segments_get_segment_t1": ([C.c_void_p, C.c_int], C.c_float),
        "whisper_vad_free_segments": ([C.c_void_p], None),
        "whisper_vad_free": ([C.c_void_p], None),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = arguments, result
    callback_type = C.CFUNCTYPE(None, C.c_int, C.c_void_p, C.c_void_p)
    lib.whisper_log_set.argtypes, lib.whisper_log_set.restype = [callback_type, C.c_void_p], None
    errors = []

    @callback_type
    def log(level, _message, _data):
        if level == 4:
            errors.append(True)

    context = segments = None
    lib.whisper_log_set(log, None)
    try:
        params = lib.whisper_vad_default_context_params()
        params.n_threads, params.use_gpu, params.gpu_device = 1, False, 0
        context = lib.whisper_vad_init_from_file_with_params(os.fsencode(model_path), params)
        if not context or errors:
            raise RuntimeError("Native VAD initialization failed")
        samples = array("h", pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        floats = array("f", (value / 32768.0 for value in samples))
        pointer = (C.c_float * len(floats)).from_buffer(floats)
        if not lib.whisper_vad_detect_speech(context, pointer, len(floats)) or errors:
            raise RuntimeError("Native VAD observation failed")
        count = lib.whisper_vad_n_probs(context)
        values = lib.whisper_vad_probs(context)
        if count != (len(samples) + 511) // 512 or not values or any(
                not math.isfinite(values[i]) or not 0 <= values[i] <= 1 for i in range(count)):
            raise RuntimeError("Invalid native VAD probabilities")
        params = lib.whisper_vad_default_params()
        if (params.threshold != 0.5 or params.min_speech_duration_ms != 250
                or params.min_silence_duration_ms != 100 or params.speech_pad_ms != 30
                or params.max_speech_duration_s != float.fromhex("0x1.fffffep+127")
                or abs(params.samples_overlap - 0.1) > 1e-8):
            raise RuntimeError("Native VAD defaults differ")
        params.speech_pad_ms = 0  # Padding must never create same-segment eligibility.
        segments = lib.whisper_vad_segments_from_probs(context, params)
        if not segments or errors:
            raise RuntimeError("Native VAD segments unavailable")
        segment_count = lib.whisper_vad_segments_n_segments(segments)
        if not 0 <= segment_count <= count:
            raise RuntimeError("Invalid native VAD segment count")
        result, previous = [], -1
        for index in range(segment_count):
            seconds = [lib.whisper_vad_segments_get_segment_t0(segments, index) / 100,
                       lib.whisper_vad_segments_get_segment_t1(segments, index) / 100]
            if any(not math.isfinite(value) or value < 0 for value in seconds):
                raise RuntimeError("Invalid native VAD segment endpoint")
            points = [round(value * 16000 / 512) * 512 for value in seconds]
            if any(abs(value - point / 16000) > 0.00501 for value, point in zip(seconds, points)):
                raise RuntimeError("Native VAD endpoint grid differs")
            start, end = points
            # Native probability padding may extend the final segment by <512
            # samples; intersect metadata with the exact submitted sample range.
            end = min(end, len(samples))
            if not previous < start < end <= len(samples):
                raise RuntimeError("Invalid native VAD speech geometry")
            result.append([start, end])
            previous = end
        return result
    finally:
        if segments:
            lib.whisper_vad_free_segments(segments)
        if context:
            lib.whisper_vad_free(context)
        lib.whisper_log_set(callback_type(), None)
