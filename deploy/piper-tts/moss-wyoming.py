from __future__ import annotations

import argparse
import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import Optional

import numpy as np
from onnx_tts_runtime import OnnxTtsRuntime
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop, SynthesizeStopped

LOGGER = logging.getLogger("moss_wyoming")


def build_info() -> Info:
    attribution = Attribution(name="OpenMOSS Team", url="https://github.com/OpenMOSS/MOSS-TTS-Nano")
    return Info(
        tts=[TtsProgram(
            name="moss-tts-nano",
            attribution=attribution,
            installed=True,
            description="CPU-local Danish MOSS TTS with automatic Piper fallback",
            version="0.1.0",
            voices=[TtsVoice(
                name="da_DK-moss-nano",
                attribution=attribution,
                installed=True,
                description="Danish MOSS-TTS-Nano using the upstream Danish demo voice",
                version="0.1.0",
                languages=["da", "da_DK"],
            )],
            supports_synthesize_streaming=True,
        )]
    )


class MossRuntime:
    def __init__(self, model_dir: Path, cpu_threads: int, voice: str) -> None:
        self.voice = voice
        self.lock = asyncio.Lock()
        self.runtime = OnnxTtsRuntime(
            model_dir=model_dir,
            thread_count=cpu_threads,
            execution_provider="cpu",
            output_dir=Path("/tmp/moss-tts"),
        )

    async def synthesize(self, text: str) -> tuple[int, int, bytes]:
        async with self.lock:
            result = await asyncio.to_thread(
                self.runtime.synthesize,
                text=text,
                voice=self.voice,
                sample_mode="fixed",
                do_sample=True,
                streaming=False,
                max_new_frames=375,
                voice_clone_max_text_tokens=75,
                enable_wetext=False,
                enable_normalize_tts_text=True,
            )

        waveform = np.asarray(result["waveform"], dtype=np.float32)
        if waveform.ndim == 1:
            waveform = waveform[:, None]
        elif waveform.ndim == 2 and waveform.shape[0] <= 8 and waveform.shape[0] < waveform.shape[1]:
            waveform = waveform.T
        if waveform.ndim != 2:
            raise ValueError(f"Unsupported MOSS waveform shape: {waveform.shape}")

        pcm = (np.clip(waveform, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return int(result["sample_rate"]), int(waveform.shape[1]), pcm


class MossEventHandler(AsyncEventHandler):
    def __init__(
        self,
        runtime: Optional[MossRuntime],
        fallback_uri: str,
        samples_per_chunk: int,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.runtime = runtime
        self.fallback_uri = fallback_uri
        self.samples_per_chunk = samples_per_chunk
        self.streaming = False
        self.stream_text: list[str] = []

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(build_info().event())
            return True

        try:
            if SynthesizeStart.is_type(event.type):
                self.streaming = True
                self.stream_text = []
                return True

            if SynthesizeChunk.is_type(event.type):
                if self.streaming:
                    self.stream_text.append(SynthesizeChunk.from_event(event).text)
                return True

            if SynthesizeStop.is_type(event.type):
                text = "".join(self.stream_text).strip()
                self.streaming = False
                self.stream_text = []
                if text:
                    await self.synthesize(text)
                await self.write_event(SynthesizeStopped().event())
                return True

            if Synthesize.is_type(event.type):
                if not self.streaming:
                    await self.synthesize(Synthesize.from_event(event).text)
                return True

            return True
        except Exception as err:
            LOGGER.exception("Both MOSS and Piper synthesis failed")
            await self.write_event(Error(text=str(err), code=err.__class__.__name__).event())
            return True

    async def synthesize(self, text: str) -> None:
        normalized_text = " ".join(text.strip().splitlines())
        if not normalized_text:
            await self.write_event(AudioStop().event())
            return

        if self.runtime is not None:
            try:
                rate, channels, pcm = await self.runtime.synthesize(normalized_text)
                await self.write_pcm(rate=rate, channels=channels, pcm=pcm)
                LOGGER.info("Synthesized with MOSS: %s", normalized_text)
                return
            except Exception:
                LOGGER.exception("MOSS synthesis failed; using Piper fallback")
        else:
            LOGGER.warning("MOSS runtime is unavailable; using Piper fallback")

        await self.proxy_to_piper(normalized_text)

    async def write_pcm(self, *, rate: int, channels: int, pcm: bytes) -> None:
        width = 2
        bytes_per_chunk = self.samples_per_chunk * width * channels
        await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
        for offset in range(0, len(pcm), bytes_per_chunk):
            await self.write_event(AudioChunk(
                rate=rate,
                width=width,
                channels=channels,
                audio=pcm[offset : offset + bytes_per_chunk],
            ).event())
        await self.write_event(AudioStop().event())

    async def proxy_to_piper(self, text: str) -> None:
        client = AsyncClient.from_uri(self.fallback_uri, connect_timeout=5, read_timeout=120)
        async with client:
            await client.write_event(Synthesize(text=text).event())
            while True:
                event = await client.read_event()
                if event is None:
                    raise RuntimeError("Piper fallback disconnected before completing audio")
                if Error.is_type(event.type):
                    error = Error.from_event(event)
                    raise RuntimeError(f"Piper fallback failed: {error.text}")
                if AudioStart.is_type(event.type) or AudioChunk.is_type(event.type) or AudioStop.is_type(event.type):
                    await self.write_event(event)
                if AudioStop.is_type(event.type):
                    LOGGER.info("Synthesized with Piper fallback: %s", text)
                    return


async def async_main(args: argparse.Namespace) -> None:
    runtime: Optional[MossRuntime]
    try:
        LOGGER.info("Loading MOSS-TTS-Nano ONNX models")
        runtime = await asyncio.to_thread(MossRuntime, Path(args.model_dir), args.cpu_threads, args.voice)
        LOGGER.info("MOSS-TTS-Nano is ready (voice=%s, threads=%s)", args.voice, args.cpu_threads)
    except Exception:
        LOGGER.exception("MOSS initialization failed; starting with Piper fallback only")
        runtime = None

    server = AsyncServer.from_uri(args.uri)
    await server.run(partial(MossEventHandler, runtime, args.fallback_uri, args.samples_per_chunk))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="tcp://0.0.0.0:10200")
    parser.add_argument("--fallback-uri", default="tcp://piper:10200")
    parser.add_argument("--model-dir", default="/opt/moss-tts-nano/models")
    parser.add_argument("--cpu-threads", type=int, default=8)
    parser.add_argument("--voice", default="Adam")
    parser.add_argument("--samples-per-chunk", type=int, default=4096)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
