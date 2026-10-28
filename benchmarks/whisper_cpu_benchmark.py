#!/usr/bin/env python3
"""Reproducible, public-audio Whisper Turbo CPU/GPU placement benchmark."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import time
import unicodedata
import urllib.request
import wave

FLEURS_REVISION = "168de341b3db6859a9bac1c50a2ef5e3b47647e0"
SOURCE_REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"


def words(text):
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        current = [i]
        for j, b in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def percentile(values, q=0.95):
    return sorted(values)[math.ceil(len(values) * q) - 1]


def write_wav(path, samples):
    import numpy as np
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(np.clip(samples * 32767, -32768, 32767).astype("<i2").tobytes())


def prepare(root):
    import numpy as np
    import pyarrow.parquet as pq
    from faster_whisper.audio import decode_audio
    root.mkdir(parents=True, exist_ok=True)
    fixtures = []
    selected = {}
    for language, config in [("da", "da_dk"), ("en", "en_us")]:
        parquet = root / (config + ".parquet")
        if not parquet.exists():
            url = (f"https://huggingface.co/datasets/google/fleurs/resolve/"
                   f"{FLEURS_REVISION}/{config}/test/0000.parquet")
            with urllib.request.urlopen(url, timeout=180) as source, parquet.open("wb") as out:
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
        rows = pq.ParquetFile(parquet).iter_batches(batch_size=12)
        selected[language] = []
        for row in next(rows).to_pylist():
            audio = decode_audio(io.BytesIO(row["audio"]["bytes"]), sampling_rate=16000)
            sr = 16000
            assert audio.ndim == 1
            identifier = f"fleurs-{language}-{len(selected[language]):02d}"
            path = root / (identifier + ".wav")
            write_wav(path, audio)
            reference = row.get("raw_transcription") or row["transcription"]
            fixture = {"id": identifier, "file": path.name, "language": language,
                       "condition": "clean", "reference": reference,
                       "seconds": len(audio) / sr,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            fixtures.append(fixture)
            selected[language].append((audio, fixture))
        # Fixed 10 dB additive noise is a stress test, not a real far-field corpus.
        for index, (audio, fixture) in enumerate(selected[language][:6]):
            rng = np.random.default_rng(20261027 + index)
            rms = np.sqrt(np.mean(audio ** 2))
            noisy = audio + rng.normal(0, rms / np.sqrt(10), audio.shape)
            path = root / (fixture["id"] + "-noise10db.wav")
            write_wav(path, noisy)
            fixtures.append({**fixture, "id": path.stem, "file": path.name,
                             "condition": "noise10db",
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for index in range(4):
        da, da_fixture = selected["da"][index]
        en, en_fixture = selected["en"][index]
        audio = np.concatenate([da, np.zeros(8000), en])
        if len(audio) > 60 * 16000:
            continue
        path = root / f"mixed-{index:02d}.wav"
        write_wav(path, audio)
        fixtures.append({"id": path.stem, "file": path.name, "language": "mixed",
                         "condition": "concatenated-language-switch",
                         "reference": da_fixture["reference"] + " " + en_fixture["reference"],
                         "seconds": len(audio) / 16000,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"source": "google/fleurs", "revision": FLEURS_REVISION,
                "license": "CC-BY-4.0", "selection": "first 12 test rows per language",
                "fixtures": fixtures}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"fixtures": len(fixtures), "seconds": sum(f["seconds"] for f in fixtures)}))


def run(root, output, backend, model_path, threads, limit, without_timestamps):
    fixtures = json.loads((root / "manifest.json").read_text())["fixtures"]
    if limit:
        fixtures = fixtures[:limit]
    for fixture in fixtures:
        if hashlib.sha256((root / fixture["file"]).read_bytes()).hexdigest() != fixture["sha256"]:
            raise ValueError("Audio fixture checksum mismatch: " + fixture["id"])
    if backend == "cpu":
        from faster_whisper import WhisperModel
        started = time.perf_counter()
        model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                             cpu_threads=threads, num_workers=1, local_files_only=True)
        print(json.dumps({"model_load_seconds": time.perf_counter() - started,
                          "compute_type": model.model.compute_type,
                          "source_revision": SOURCE_REVISION,
                          "without_timestamps": without_timestamps}), flush=True)

        def transcribe(fixture):
            segments, info = model.transcribe(str(root / fixture["file"]),
                language=None if fixture["language"] == "mixed" else fixture["language"],
                beam_size=1, temperature=0, vad_filter=False,
                condition_on_previous_text=True, word_timestamps=False,
                without_timestamps=without_timestamps)
            return "".join(segment.text for segment in segments).strip()
    else:
        key = Path("/run/secrets/vllm_api_key").read_text().strip()

        def transcribe(fixture):
            boundary = "homecompute-placement-benchmark"
            fields = {"model": "whisper-turbo", "temperature": "0", "response_format": "json"}
            if fixture["language"] != "mixed":
                fields["language"] = fixture["language"]
            body = b""
            for name, value in fields.items():
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                         f'\r\n\r\n{value}\r\n').encode()
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                     'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
            body += (root / fixture["file"]).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            request = urllib.request.Request("http://127.0.0.1:8000/v1/audio/transcriptions",
                data=body, headers={"Authorization": "Bearer " + key,
                                   "Content-Type": "multipart/form-data; boundary=" + boundary})
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)["text"]

    # Separate first inference from warm trials; never silently discard a measured trial.
    started = time.perf_counter()
    transcribe(fixtures[0])
    print(json.dumps({"warmup_seconds": time.perf_counter() - started}), flush=True)
    with output.open("x") as handle:
        for fixture in fixtures:
            started = time.perf_counter()
            try:
                hypothesis = transcribe(fixture)
                record = {"text": hypothesis, "errors": distance(words(fixture["reference"]), words(hypothesis)),
                          "words": len(words(fixture["reference"])), "error": None}
            except Exception as error:
                record = {"error": type(error).__name__}
            seconds = time.perf_counter() - started
            record.update({k: fixture[k] for k in ["id", "language", "condition", "seconds", "sha256"]})
            record.update(backend=backend, threads=threads,
                          without_timestamps=without_timestamps, elapsed=seconds,
                          rtf=seconds / fixture["seconds"])
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({k: record.get(k) for k in ["id", "elapsed", "rtf", "errors", "words", "error"]}), flush=True)


def summarize(paths):
    for path in paths:
        records = [json.loads(line) for line in path.read_text().splitlines()]
        groups = {}
        for row in records:
            groups.setdefault(row["language"] + "/" + row["condition"], []).append(row)
        result = {"file": str(path), "count": len(records), "failures": sum(bool(r["error"]) for r in records),
                  "p95_rtf": percentile([r["rtf"] for r in records]), "groups": {}}
        for name, rows in groups.items():
            result["groups"][name] = {"n": len(rows), "wer": sum(r.get("errors", 0) for r in rows) / max(1, sum(r.get("words", 0) for r in rows)),
                "p95_rtf": percentile([r["rtf"] for r in rows]),
                "median_seconds": statistics.median(r["elapsed"] for r in rows),
                "p95_seconds": percentile([r["elapsed"] for r in rows])}
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run", "summarize", "self-test"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backend", choices=["cpu", "gpu"], default="cpu")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--without-timestamps", action="store_true")
    parser.add_argument("paths", type=Path, nargs="*")
    args = parser.parse_args()
    if args.mode == "self-test":
        assert words("TÆND, lyset! 21") == ["tænd", "lyset", "21"]
        assert distance(words("do not turn it off"), words("do turn it off")) == 1
        assert distance([], ["one", "two"]) == 2
        assert distance(["one"], []) == 1
        assert percentile(list(range(1, 21))) == 19
        print("Scoring self-tests passed")
    elif args.mode == "prepare":
        prepare(args.root)
    elif args.mode == "run":
        run(args.root, args.output, args.backend, args.model, args.threads, args.limit,
            args.without_timestamps)
    else:
        summarize(args.paths)


if __name__ == "__main__":
    main()
