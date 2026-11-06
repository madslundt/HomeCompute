"""Read the approved unquantized Whisper GGML artifact without a full CPU model.

The binary layout is the inverse of whisper.cpp f049fff's convert-pt-to-ggml.py:
dimension headers are reversed, payloads are C-order, and conv biases are [n,1].
Only the small constructor changes; all reference Whisper methods are inherited.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import mmap
import os
from pathlib import Path
import stat
import struct
from typing import Callable
import warnings

VERSION = "approved-ggml-reference-loader-v1"
MODEL_SHA256 = "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2"
MODEL_BYTES = 3095033483
FILTER_SHA256 = "2a5f9822897750e047c85dea37cc268d3be0ecfd23c28a5f10da129d99d05afe"
VOCAB_SHA256 = "358bdf83c2253dea0c8a1749f5288d96d2b6f5f4119faee9e5e26611a8c8f17b"
HEADER_KEYS = ("n_vocab", "n_audio_ctx", "n_audio_state", "n_audio_head",
               "n_audio_layer", "n_text_ctx", "n_text_state", "n_text_head",
               "n_text_layer", "n_mels")
DIMENSIONS = dict(zip(HEADER_KEYS, (51866, 1500, 1280, 20, 32, 448, 1280, 20, 32, 128)))
CONV_BIASES = {"encoder.conv1.bias", "encoder.conv2.bias"}
POSITIONS = {"encoder.positional_embedding", "decoder.positional_embedding"}
Guard = Callable[[], None]


class ModelFormatError(ValueError):
    pass


@dataclass(frozen=True)
class TensorRecord:
    name: str
    shape: tuple[int, ...]
    type_id: int
    offset: int
    nbytes: int


def _read(file, count: int) -> bytes:
    value = file.read(count)
    if len(value) != count:
        raise ModelFormatError("Truncated GGML artifact")
    return value


def _ints(file, count: int) -> tuple[int, ...]:
    return struct.unpack("<" + "i" * count, _read(file, count * 4))


def verify_file(file, expected_size: int, expected_sha256: str, guard: Guard):
    before = os.fstat(file.fileno())
    if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
        raise ModelFormatError("Unexpected model file type or size")
    file.seek(0)
    digest = hashlib.sha256()
    while chunk := file.read(1024 * 1024):
        guard()
        digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ModelFormatError("Approved model SHA256 mismatch")
    return before


def inspect_layout(file, *, dimensions: dict, shapes: dict,
                   filter_sha256: str, vocab_sha256: str) -> tuple[TensorRecord, ...]:
    """Validate all metadata before any tensor transfer; file stays on one open inode."""
    size = os.fstat(file.fileno()).st_size
    file.seek(0)
    header = _ints(file, 12)
    if header[0] != 0x67676D6C or header[-1] != 1 or \
            dict(zip(HEADER_KEYS, header[1:-1])) != dimensions:
        raise ModelFormatError("Unexpected GGML header")
    mel_count, fft_count = _ints(file, 2)
    if (mel_count, fft_count) != (dimensions["n_mels"], 201):
        raise ModelFormatError("Unexpected mel filter shape")
    if hashlib.sha256(_read(file, mel_count * fft_count * 4)).hexdigest() != filter_sha256:
        raise ModelFormatError("Mel filter identity mismatch")
    raw_count = _read(file, 4)
    vocab_count = struct.unpack("<i", raw_count)[0]
    if not 0 < vocab_count <= 100000:
        raise ModelFormatError("Invalid base vocabulary size")
    digest = hashlib.sha256(raw_count)
    metadata_bytes = 0
    for _ in range(vocab_count):
        raw_length = _read(file, 4)
        length = struct.unpack("<i", raw_length)[0]
        if not 0 <= length <= 16384 or metadata_bytes + length > 2 * 1024 * 1024:
            raise ModelFormatError("Invalid vocabulary entry")
        digest.update(raw_length)
        digest.update(_read(file, length))
        metadata_bytes += length
    if digest.hexdigest() != vocab_sha256:
        raise ModelFormatError("Base vocabulary identity mismatch")
    records, names = [], set()
    while file.tell() < size:
        rank, name_length, type_id = _ints(file, 3)
        if not 1 <= rank <= 3 or not 1 <= name_length <= 256 or type_id not in (0, 1):
            raise ModelFormatError("Invalid tensor header")
        stored = _ints(file, rank)
        if any(not 0 < v <= 100000 for v in stored):
            raise ModelFormatError("Invalid tensor dimensions")
        try:
            name = _read(file, name_length).decode("ascii")
        except UnicodeError:
            raise ModelFormatError("Invalid tensor name") from None
        if name not in shapes or name in names:
            raise ModelFormatError("Unknown or duplicate tensor")
        shape = tuple(reversed(stored))
        expected = tuple(shapes[name])
        if shape != (expected + (1,) if name in CONV_BIASES else expected):
            raise ModelFormatError("Tensor shape mismatch")
        expected_type = 0 if len(expected) < 2 or name in CONV_BIASES | POSITIONS else 1
        if type_id != expected_type:
            raise ModelFormatError("Tensor storage dtype mismatch")
        nbytes = math.prod(shape) * (4 if type_id == 0 else 2)
        offset = file.tell()
        if nbytes > size - offset:
            raise ModelFormatError("Tensor payload exceeds artifact")
        records.append(TensorRecord(name, expected, type_id, offset, nbytes))
        names.add(name)
        file.seek(nbytes, os.SEEK_CUR)
    if names != set(shapes):
        raise ModelFormatError("Missing reference state tensors")
    return tuple(records)


def construct_meta_model(reference, torch, dimensions: dict, *, model_class=None):
    """Use canonical child constructors; avoid only sparse Meta alignment setup."""
    model_class = reference.Whisper if model_class is None else model_class
    if not issubclass(model_class, reference.Whisper):
        raise TypeError("Expected a canonical Whisper subclass")
    model = model_class.__new__(model_class)
    torch.nn.Module.__init__(model)
    model.dims = reference.ModelDimensions(**dimensions)
    d = model.dims
    with torch.device("meta"):
        model.encoder = reference.AudioEncoder(d.n_mels, d.n_audio_ctx,
                                               d.n_audio_state, d.n_audio_head, d.n_audio_layer)
        model.decoder = reference.TextDecoder(d.n_vocab, d.n_text_ctx,
                                              d.n_text_state, d.n_text_head, d.n_text_layer)
    # Exactly the canonical default. Word alignment is disabled for this diagnostic.
    heads = torch.zeros(d.n_text_layer, d.n_text_head, dtype=torch.bool, device="cpu")
    heads[d.n_text_layer // 2:] = True
    model.register_buffer("alignment_heads", heads.to_sparse(), persistent=False)
    return model


def stream_tensors(model, file, records, torch, device, guard: Guard):
    """Copy each read-only mmap tensor directly to its destination, never cast it."""
    with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        for record in records:
            guard()
            dtype = torch.float32 if record.type_id == 0 else torch.float16
            # PyTorch warns that read-only buffers cannot enforce tensor immutability.
            # This view is NEVER modified; the synchronous destination copy owns storage.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="The given buffer is not writable.*")
                view = torch.frombuffer(mapped, dtype=dtype,
                                        count=math.prod(record.shape), offset=record.offset)
            tensor = view.reshape(record.shape).to(device=device, copy=True)
            if str(device).startswith("cuda"):
                torch.cuda.synchronize(device)
            del view
            module_name, leaf = record.name.rsplit(".", 1)
            module = model.get_submodule(module_name)
            if leaf in module._parameters:
                setattr(module, leaf, torch.nn.Parameter(tensor, requires_grad=False))
            else:
                if leaf not in module._buffers:
                    raise ModelFormatError("Reference state target mismatch")
                setattr(module, leaf, tensor)
            del tensor
            guard()
    # The causal mask is nonpersistent and therefore absent from GGML.
    model.decoder.mask = torch.empty(model.dims.n_text_ctx, model.dims.n_text_ctx,
                                     dtype=torch.float32, device=device).fill_(-float("inf")).triu_(1)
    model.eval()
    if any(v.is_meta or str(v.device) != str(device) for v in model.state_dict().values()):
        raise ModelFormatError("Reference state is not fully resident on target device")
    return model


def load_approved_model(path: Path, reference, torch, *, device: str, guard: Guard, model_class=None):
    if device != "cuda:0":
        raise ValueError("Reference shadow requires explicit cuda:0; CPU fallback is disabled")
    guard()
    model = construct_meta_model(reference, torch, DIMENSIONS, model_class=model_class)
    shapes = {name: tuple(value.shape) for name, value in model.state_dict().items()}
    if len(shapes) != 1259:
        raise ModelFormatError("Reference architecture state count mismatch")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        before = verify_file(file, MODEL_BYTES, MODEL_SHA256, guard)
        records = inspect_layout(file, dimensions=DIMENSIONS, shapes=shapes,
                                 filter_sha256=FILTER_SHA256, vocab_sha256=VOCAB_SHA256)
        if sum(r.nbytes for r in records) != 3094359040 or \
                Counter(r.type_id for r in records) != {0: 744, 1: 515}:
            raise ModelFormatError("Approved tensor inventory mismatch")
        stream_tensors(model, file, records, torch, device, guard)
        after = os.fstat(file.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != \
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ModelFormatError("Model changed during load")
    guard()
    return model
