#!/usr/bin/env python3
"""Prepare hash-verified Harrier artifacts and run private CPU inference on home-core."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/srv/state/embeddings")
ENV = Path("/etc/homecompute/embeddings.env")


def manifest() -> dict:
    result = json.loads((ROOT / "config/embedding-model.json").read_text())
    if not re.fullmatch(r"[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+", result["model"]):
        raise ValueError("Invalid model ID")
    if not re.fullmatch(r"[0-9a-f]{40}", result["revision"]):
        raise ValueError("Model revision must be immutable")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", result["image"]):
        raise ValueError("Runtime image must be digest-pinned")
    for name, expected in result["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Unsafe model filename")
        if not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]) or expected["size"] <= 0:
            raise ValueError("Model file must have a size and SHA-256")
    return result


def verified(path: Path, expected: dict) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["size"]:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == expected["sha256"]


def download(url: str, target: Path, expected: dict) -> None:
    """Bound downloads and publish only complete artifacts matching the reviewed manifest."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as source, temporary.open("wb") as output:
            remaining = expected["size"]
            while block := source.read(min(1024 * 1024, remaining + 1)):
                if len(block) > remaining:
                    raise ValueError("Download exceeds pinned artifact size")
                output.write(block)
                remaining -= len(block)
        if not verified(temporary, expected):
            raise ValueError("Downloaded artifact failed size/SHA-256 verification")
        temporary.chmod(0o444)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def model_directory(config: dict) -> Path:
    return STATE / "models" / config["revision"]


def prepare(config: dict) -> None:
    directory = model_directory(config)
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < 4 * 1024 ** 3:
        raise ValueError("At least 4 GiB free space required for model preparation")
    for name, expected in config["files"].items():
        target = directory / name
        if not verified(target, expected):
            print(f"Downloading and verifying {name}", flush=True)
            download(f'https://huggingface.co/{config["model"]}/resolve/'
                     f'{config["revision"]}/{name}', target, expected)
    # This operator-owned serving limit is separate from the upstream artifacts.
    serving_config = directory / "sentence_bert_config.json"
    serving_config.write_bytes((ROOT / "deploy/embeddings/sentence_bert_config.json").read_bytes())
    serving_config.chmod(0o444)
    ENV.parent.mkdir(parents=True, exist_ok=True)
    ENV.write_text(f'EMBEDDING_IMAGE={config["image"]}\n'
                   f'EMBEDDING_MODEL={config["model"]}\n'
                   f'EMBEDDING_MODEL_DIR={directory}\n')
    ENV.chmod(0o644)
    print(f'Prepared {config["model"]} at {config["revision"]}', flush=True)


def smoke(config: dict) -> None:
    """Exercise multilingual retrieval using fictional text; print no vectors or source text."""
    documents = ["Vi aftalte at hente børnene fra skolen klokken tre.",
                 "The dentist appointment is on Friday morning.",
                 "Die Rechnung für die Heizung wird nächste Woche bezahlt."]
    queries = ["When should we pick up the children from school?",
               "Hvornår er tandlægeaftalen?", "When will the heating bill be paid?"]
    inputs = documents + [config["query_prefix"] + query for query in queries]
    started = time.monotonic()
    response = subprocess.run(
        ["docker", "exec", "-i", "home-core-embeddings", "curl", "--fail", "--silent",
         "--show-error", "--max-time", "120", "--header", "Content-Type: application/json",
         "--data-binary", "@-", "http://127.0.0.1:8080/v1/embeddings"],
        input=json.dumps({"model": config["model"], "input": inputs,
                          "encoding_format": "float"}),
        capture_output=True, text=True, check=True,
    )
    result = json.loads(response.stdout)
    if result["model"] != config["model"]:
        raise ValueError("Unexpected served model")
    rows = sorted(result["data"], key=lambda row: row["index"])
    if [row["index"] for row in rows] != list(range(len(inputs))):
        raise ValueError("Missing or repeated embedding rows")
    vectors = [row["embedding"] for row in rows]
    for vector in vectors:
        if len(vector) != config["dimensions"] or not all(math.isfinite(x) for x in vector):
            raise ValueError("Invalid embedding dimensions or values")
        if not math.isclose(sum(x * x for x in vector), 1.0, abs_tol=0.001):
            raise ValueError("Embedding is not normalized")
    rankings = [sorted(range(3), reverse=True,
                       key=lambda j: sum(a * b for a, b in zip(query, vectors[j])))
                for query in vectors[3:]]
    top_one = sum(ranking[0] == index for index, ranking in enumerate(rankings))
    top_two = sum(index in ranking[:2] for index, ranking in enumerate(rankings))
    # Retrieval uses several candidates. Report this tiny quality check explicitly;
    # one missed top result does not mean the endpoint or vectors are invalid.
    if top_two < 3:
        raise ValueError("Multilingual retrieval smoke check failed at top 2")
    print(json.dumps({"model": config["model"], "dimensions": config["dimensions"],
                      "cross_language_top1": f"{top_one}/3", "cross_language_top2": f"{top_two}/3",
                      "seconds": round(time.monotonic() - started, 2)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "up", "down", "status", "smoke"])
    action = parser.parse_args().action
    if os.geteuid() != 0 or socket.gethostname() != "home-core":
        raise SystemExit("Run as root on home-core")
    config = manifest()
    compose = ["docker", "compose", "--env-file", str(ENV), "-f",
               str(ROOT / "deploy/embeddings/compose.yaml")]
    if action == "prepare":
        prepare(config)
    elif action == "smoke":
        smoke(config)
    elif action == "up":
        for name, expected in config["files"].items():
            if not verified(model_directory(config) / name, expected):
                raise SystemExit("Model integrity check failed; run prepare first")
        if (model_directory(config) / "sentence_bert_config.json").read_bytes() != (
            ROOT / "deploy/embeddings/sentence_bert_config.json"
        ).read_bytes():
            raise SystemExit("CPU serving limit differs; run prepare first")
        subprocess.run(compose + ["config", "--quiet"], check=True)
        subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "300"], check=True)
        smoke(config)
    else:
        subprocess.run(compose + (["ps"] if action == "status" else ["down"]), check=True)


if __name__ == "__main__":
    main()
