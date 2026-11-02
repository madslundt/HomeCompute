#!/usr/bin/env python3
"""Apply the two narrowly scoped Whisper fixes to the pinned NVIDIA vLLM image."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path


PATCHES = {
    "model_executor/models/whisper.py": (
        "44d196b1d3ec3182de51f4b5f87764a33a1b58c83e6afb5e452f52f4bb653dcf",
        'f"<|prev|>{request_prompt}" if request_prompt else ""',
        'f"<|startofprev|>{request_prompt}" if request_prompt else ""',
    ),
    "entrypoints/openai/speech_to_text/speech_to_text.py": (
        "7b9b47511ba3643ccae14ae8a4095153959a35a671eb3cc07707dfd1f10ebfa3",
        '''                    if request.response_format == "verbose_json":
                        assert op.outputs[0].logprobs
                        segments: list[SpeechToTextSegment] = (
                            self._get_verbose_segments(
                                tokens=tuple(op.outputs[0].token_ids),
                                segment_class=segment_class,
                                request=request,
                                start_time=start_time,
                                log_probs=op.outputs[0].logprobs,
                            )
                        )

                        total_segments.extend(segments)
                        text_parts.extend([seg.text for seg in segments])''',
        '''                    if request.response_format == "verbose_json":
                        assert op.outputs[0].logprobs
                        raw_text = op.outputs[0].text
                        segments: list[SpeechToTextSegment] = (
                            self._get_verbose_segments(
                                tokens=tuple(op.outputs[0].token_ids),
                                segment_class=segment_class,
                                request=request,
                                start_time=start_time,
                                log_probs=op.outputs[0].logprobs,
                            )
                        )

                        total_segments.extend(segments)
                        # Keep the full model text if timestamp parsing drops any
                        # segment or an unclosed trailing passage. Never invent times.
                        text_parts.append(self.model_cls.post_process_output(raw_text))''',
    ),
}


def patch_bytes(original: bytes, expected_sha256: str, old: str, new: str) -> bytes:
    digest = sha256(original).hexdigest()
    if digest != expected_sha256:
        raise ValueError("Pinned vLLM source checksum changed")
    source = original.decode("utf-8")
    if source.count(old) != 1:
        raise ValueError("Pinned vLLM patch anchor is missing or ambiguous")
    updated = source.replace(old, new, 1).encode("utf-8")
    if sha256(updated).hexdigest() == digest:
        raise ValueError("Pinned vLLM patch did not change source")
    return updated


def apply(root: Path) -> None:
    files = []
    for relative, (digest, old, new) in PATCHES.items():
        target = root / relative
        original = target.read_bytes()
        updated = patch_bytes(original, digest, old, new)
        compile(updated.decode("utf-8"), str(target), "exec")
        files.append((target, updated))
    for target, content in files:
        temporary = target.with_suffix(target.suffix + ".homecompute-tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    if not verify(root):
        raise ValueError("Patched vLLM source did not pass post-write checks")


def verify(root: Path) -> bool:
    whisper = (root / "model_executor/models/whisper.py").read_text()
    serving = (root / "entrypoints/openai/speech_to_text/speech_to_text.py").read_text()
    return (
        '<|startofprev|>{request_prompt}' in whisper
        and '<|prev|>{request_prompt}' not in whisper
        and 'text_parts.append(self.model_cls.post_process_output(raw_text))' in serving
        and serving.count('text_parts.append(self.model_cls.post_process_output(raw_text))') == 2
        and 'total_segments.extend(segments)' in serving
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vllm-root", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify:
            if not verify(args.vllm_root):
                raise ValueError("Whisper runtime patch verification failed")
        else:
            apply(args.vllm_root)
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
