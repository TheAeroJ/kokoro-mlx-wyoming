"""Wyoming event handler: one instance per client connection."""

from __future__ import annotations

import asyncio
import logging
import time

from sentence_stream import SentenceBoundaryDetector
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
    SynthesizeVoice,
)

from .engine import SAMPLE_RATE, KokoroEngine

_LOGGER = logging.getLogger(__name__)

SAMPLES_PER_CHUNK = 1024
WIDTH = 2  # 16-bit
CHANNELS = 1


class KokoroEventHandler(AsyncEventHandler):
    def __init__(
        self,
        info: Info,
        engine: KokoroEngine,
        default_voice: str,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.info = info
        self.engine = engine
        self.default_voice = default_voice

        # Home Assistant also sends a plain Synthesize alongside a stream, for
        # older servers. Once a stream has started on this connection, we
        # ignore it so the text isn't spoken twice (same as wyoming-piper).
        self.is_streaming = False
        self.stream_voice = default_voice
        self.detector = SentenceBoundaryDetector()

    async def handle_event(self, event: Event) -> bool:
        try:
            return await self._handle_event(event)
        except Exception as err:
            _LOGGER.exception("Error handling %s", event.type)
            await self.write_event(Error(text=str(err), code=err.__class__.__name__).event())
            raise

    async def _handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.info.event())
            return True

        if Synthesize.is_type(event.type):
            if self.is_streaming:
                return True
            synthesize = Synthesize.from_event(event)
            voice = self._voice(synthesize.voice)
            detector = SentenceBoundaryDetector()
            sentences = list(detector.add_chunk(_clean(synthesize.text)))
            sentences.append(detector.finish())

            # One audio stream for the whole reply, sent sentence by sentence
            # so playback can start before the last sentence is ready.
            await self._write_start()
            for sentence in sentences:
                await self._write_sentence_audio(sentence, voice)
            await self.write_event(AudioStop().event())
            return True

        if SynthesizeStart.is_type(event.type):
            start = SynthesizeStart.from_event(event)
            self.is_streaming = True
            self.stream_voice = self._voice(start.voice)
            self.detector = SentenceBoundaryDetector()
            return True

        if SynthesizeChunk.is_type(event.type):
            chunk = SynthesizeChunk.from_event(event)
            for sentence in self.detector.add_chunk(chunk.text):
                await self._speak_sentence(sentence, self.stream_voice)
            return True

        if SynthesizeStop.is_type(event.type):
            await self._speak_sentence(self.detector.finish(), self.stream_voice)
            await self.write_event(SynthesizeStopped().event())
            return True

        return True

    def _voice(self, voice: SynthesizeVoice | None) -> str:
        if voice is None:
            return self.default_voice
        return self.engine.resolve_voice(voice.name, voice.language, self.default_voice)

    async def _speak_sentence(self, text: str, voice: str) -> None:
        """Streaming mode: each sentence is its own audio start/stop."""
        if not _clean(text):
            return
        await self._write_start()
        await self._write_sentence_audio(text, voice)
        await self.write_event(AudioStop().event())

    async def _write_start(self) -> None:
        await self.write_event(
            AudioStart(rate=SAMPLE_RATE, width=WIDTH, channels=CHANNELS).event()
        )

    async def _write_sentence_audio(self, text: str, voice: str) -> None:
        text = _clean(text)
        if not text:
            return

        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            audio = await loop.run_in_executor(
                self.engine.executor, self.engine.synthesize, text, voice
            )
        except Exception:
            # One bad sentence (e.g. only symbols) shouldn't silence the rest.
            _LOGGER.exception("Failed to synthesize %r", text)
            return

        elapsed = time.perf_counter() - started
        _LOGGER.debug(
            "%s: %.2fs audio in %.2fs: %r",
            voice,
            len(audio) / SAMPLE_RATE,
            elapsed,
            text,
        )

        for i in range(0, len(audio), SAMPLES_PER_CHUNK):
            await self.write_event(
                AudioChunk(
                    audio=audio[i : i + SAMPLES_PER_CHUNK].tobytes(),
                    rate=SAMPLE_RATE,
                    width=WIDTH,
                    channels=CHANNELS,
                ).event()
            )


def _clean(text: str) -> str:
    return " ".join(text.split())
