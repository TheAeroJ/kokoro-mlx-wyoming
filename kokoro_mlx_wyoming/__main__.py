"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from wyoming.info import Attribution, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncServer

from . import __version__
from .engine import DEFAULT_LANGUAGES, LANGUAGES, KokoroEngine, configure_espeak
from .handler import KokoroEventHandler

_LOGGER = logging.getLogger(__name__)

BREW_ESPEAK = Path("/opt/homebrew/opt/espeak-ng")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kokoro-mlx-wyoming")
    parser.add_argument("--uri", default="tcp://0.0.0.0:10200", help="unix:// or tcp://")
    parser.add_argument("--model", default="prince-canuma/Kokoro-82M", help="Hugging Face repo or local path")
    parser.add_argument("--voice", default="af_heart", help="Default voice")
    parser.add_argument("--speed", type=float, default=1.0, help="Speaking speed (1.0 = normal)")
    parser.add_argument(
        "--languages",
        default=DEFAULT_LANGUAGES,
        help=f"Kokoro language letters to offer (default: {DEFAULT_LANGUAGES}). "
        + ", ".join(f"{k}={v[1]}" for k, v in LANGUAGES.items()),
    )
    parser.add_argument(
        "--zeroconf",
        nargs="?",
        const="kokoro-mlx",
        help="Announce the server over mDNS so Home Assistant finds it (optional name)",
    )
    parser.add_argument("--espeak-library", type=Path, default=BREW_ESPEAK / "lib/libespeak-ng.dylib")
    parser.add_argument("--espeak-data", type=Path, default=BREW_ESPEAK / "share/espeak-ng-data")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Hugging Face's HTTP client is very chatty at debug level.
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    unknown = set(args.languages) - set(LANGUAGES)
    if unknown:
        raise SystemExit(f"Unknown language letters: {''.join(sorted(unknown))}")

    configure_espeak(args.espeak_library, args.espeak_data)

    engine = KokoroEngine(args.model, args.languages, args.speed)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(engine.executor, engine.load, args.voice)
    if args.voice not in engine.voices:
        raise SystemExit(f"Default voice {args.voice!r} not found. Options: {', '.join(engine.voices)}")

    attribution = Attribution(name="hexgrad", url="https://huggingface.co/hexgrad/Kokoro-82M")
    info = Info(
        tts=[
            TtsProgram(
                name="kokoro-mlx",
                description="Kokoro-82M on Apple Silicon (MLX)",
                attribution=attribution,
                installed=True,
                version=__version__,
                supports_synthesize_streaming=True,
                voices=[
                    TtsVoice(
                        name=voice.name,
                        description=voice.description,
                        attribution=attribution,
                        installed=True,
                        version=None,
                        languages=[voice.language],
                    )
                    for voice in engine.voices.values()
                ],
            )
        ]
    )

    server = AsyncServer.from_uri(args.uri)

    if args.zeroconf:
        parsed = urlparse(args.uri)
        if parsed.scheme != "tcp":
            raise SystemExit("--zeroconf needs a tcp:// URI")
        from wyoming.zeroconf import HomeAssistantZeroconf

        zeroconf = HomeAssistantZeroconf(name=args.zeroconf, port=parsed.port or 10200)
        await zeroconf.register_server()
        _LOGGER.info("Announced over zeroconf as %s", args.zeroconf)

    _LOGGER.info("Listening on %s (default voice %s)", args.uri, args.voice)
    await server.run(partial(KokoroEventHandler, info, engine, args.voice))


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
