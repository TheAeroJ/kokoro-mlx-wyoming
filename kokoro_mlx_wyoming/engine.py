"""Kokoro on MLX: loading, voice discovery and synthesis."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 24000

# Kokoro voice names start with a language letter and a gender letter,
# e.g. "af_heart" = American English, female.
LANGUAGES: dict[str, tuple[str, str]] = {
    "a": ("en-US", "American English"),
    "b": ("en-GB", "British English"),
    "e": ("es-ES", "Spanish"),
    "f": ("fr-FR", "French"),
    "h": ("hi-IN", "Hindi"),
    "i": ("it-IT", "Italian"),
    "p": ("pt-BR", "Brazilian Portuguese"),
    # Japanese and Mandarin need `misaki[ja]` / `misaki[zh]`, which aren't
    # installed by default. Enable them with --languages if you add those.
    "j": ("ja-JP", "Japanese"),
    "z": ("zh-CN", "Mandarin Chinese"),
}
DEFAULT_LANGUAGES = "abefhip"
GENDERS = {"f": "female", "m": "male"}


@dataclass(frozen=True)
class Voice:
    name: str
    lang_code: str

    @property
    def language(self) -> str:
        return LANGUAGES[self.lang_code][0]

    @property
    def description(self) -> str:
        speaker = self.name.split("_", 1)[-1].replace("_", " ").title()
        lang = LANGUAGES[self.lang_code][1]
        gender = GENDERS.get(self.name[1:2], "")
        return f"{speaker} ({lang}, {gender})" if gender else f"{speaker} ({lang})"


def configure_espeak(library: Path, data_path: Path) -> None:
    """Point misaki's espeak fallback at a working espeak-ng install.

    misaki ships with `espeakng-loader`, but its macOS build ignores the data
    path it is given and looks for a CI build folder instead, which kills the
    process. Homebrew's espeak-ng works, so we override misaki's choice here.
    """
    if not library.is_file() or not data_path.is_dir():
        raise SystemExit(
            f"espeak-ng not found at {library} / {data_path}. "
            "Install it with `brew install espeak-ng`, or pass "
            "--espeak-library and --espeak-data."
        )

    # Importing misaki.espeak sets the broken defaults, so import it first and
    # then replace them.
    import misaki.espeak  # noqa: F401
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    EspeakWrapper.set_library(str(library))
    EspeakWrapper.set_data_path(str(data_path))


class KokoroEngine:
    """Owns the model. All MLX work runs on one dedicated thread."""

    def __init__(self, model_id: str, languages: str, speed: float) -> None:
        self.model_id = model_id
        self.languages = set(languages)
        self.speed = speed
        self.voices: dict[str, Voice] = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kokoro")
        self._model = None

    # --- run on the executor thread ------------------------------------

    def load(self, warmup_voice: str) -> None:
        from mlx_audio.tts.utils import load

        _LOGGER.info("Loading %s", self.model_id)
        self._model = load(self.model_id)
        self.voices = self._find_voices(Path(self._model.config.model_path))
        if not self.voices:
            raise SystemExit(f"No voices found for languages {sorted(self.languages)}")

        # The first synthesis per language loads its phonemizer (and spaCy for
        # English) and compiles Metal kernels, which takes seconds. Do it now
        # instead of on someone's first request.
        warmups = {warmup_voice} if warmup_voice in self.voices else set()
        for lang_code in sorted({v.lang_code for v in self.voices.values()}):
            if not any(self.voices[w].lang_code == lang_code for w in warmups):
                warmups.add(next(v.name for v in self.voices.values() if v.lang_code == lang_code))
        for name in sorted(warmups):
            _LOGGER.info("Warming up %s", name)
            self.synthesize("Ready.", name)
        _LOGGER.info("Model ready with %d voices", len(self.voices))

    def synthesize(self, text: str, voice_name: str) -> np.ndarray:
        """Return 16-bit mono PCM at 24 kHz for one piece of text."""
        voice = self.voices[voice_name]
        parts = [
            np.asarray(result.audio, dtype=np.float32)
            for result in self._model.generate(
                text,
                voice=voice.name,
                speed=self.speed,
                lang_code=voice.lang_code,
            )
        ]
        if not parts:
            return np.zeros(0, dtype=np.int16)
        audio = np.concatenate(parts)
        return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

    # --- helpers --------------------------------------------------------

    def _find_voices(self, model_path: Path) -> dict[str, Voice]:
        voices = {}
        for path in sorted((model_path / "voices").glob("*.safetensors")):
            name = path.stem
            lang_code = name[:1]
            if lang_code in LANGUAGES and lang_code in self.languages:
                voices[name] = Voice(name=name, lang_code=lang_code)
        return voices

    def resolve_voice(
        self, name: str | None, language: str | None, default: str
    ) -> str:
        """Pick a voice from a Wyoming request, falling back sensibly."""
        if name and name in self.voices:
            return name
        if name:
            _LOGGER.warning("Unknown voice %r, using fallback", name)
        if language:
            wanted = language.lower()
            # Prefer the configured default when it speaks the language, then
            # an exact match like "en-GB", then a bare one like "en" or "es".
            candidates = [self.voices[default], *self.voices.values()]
            for voice in candidates:
                if voice.language.lower() == wanted:
                    return voice.name
            for voice in candidates:
                if voice.language.lower().split("-")[0] == wanted.split("-")[0]:
                    return voice.name
        return default
