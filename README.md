# kokoro-mlx-wyoming

A [Wyoming protocol](https://github.com/OHF-Voice/wyoming) text-to-speech server for
Home Assistant that runs [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) on
Apple Silicon through [MLX](https://github.com/ml-explore/mlx) (via `mlx-audio`).

- Supports Home Assistant's streaming TTS: it speaks each sentence as soon as the
  LLM finishes writing it, instead of waiting for the full reply.
- Includes 41 voices in American and British English, Spanish, French, Hindi,
  Italian and Brazilian Portuguese.
- Can announce itself over mDNS, so Home Assistant can discover it automatically.

## Requirements

- An Apple Silicon Mac
- [uv](https://docs.astral.sh/uv/)
- Homebrew `espeak-ng`, used to pronounce words Kokoro's dictionary doesn't know
  and for the non-English languages

```bash
brew install espeak-ng
```

> **Why not the bundled espeak?** `misaki` depends on `espeakng-loader`, but that
> package's macOS library ignores the data path it is given. It looks for a
> folder from its own CI build instead and exits the process. The server
> therefore points misaki at Homebrew's copy. Use `--espeak-library` and
> `--espeak-data` if yours is somewhere else.

## Run it

```bash
uv sync
uv run kokoro-mlx-wyoming --zeroconf
```

The first run downloads the model and voices (about 350 MB) to
`~/.cache/huggingface`. Startup then warms up every language so the first
request doesn't stall. This takes about 15–20 seconds on a first launch and a
few seconds after that.

Options:

| Flag | Default | |
|---|---|---|
| `--uri` | `tcp://0.0.0.0:10200` | Where to listen |
| `--voice` | `af_heart` | Default voice |
| `--speed` | `1.0` | Speaking speed |
| `--languages` | `abefhip` | Which Kokoro languages to offer (`a` US English, `b` UK English, `e` Spanish, `f` French, `h` Hindi, `i` Italian, `p` Portuguese) |
| `--zeroconf [name]` | off | Announce over mDNS for Home Assistant discovery |
| `--model` | `prince-canuma/Kokoro-82M` | Hugging Face repo or local path |
| `--debug` | off | Log per-sentence timing |

Japanese (`j`) and Mandarin (`z`) need extra packages
(`uv add "misaki[ja]"` or `uv add "misaki[zh]"`) before you add them to `--languages`.

## Run it as a service

```bash
scripts/install-service.sh            # port 10200, voice af_heart
scripts/install-service.sh 10200 bf_emma
```

This installs a launchd agent that starts at login and restarts if the server
crashes. Logs go to `~/Library/Logs/kokoro-mlx-wyoming.log`. To remove it, run
`scripts/uninstall-service.sh`.

For a headless Mac mini:

- **Turn on automatic login** (System Settings → Users & Groups). A launch agent
  only runs while a user is logged in. That user session also gives MLX
  reliable access to the GPU, which a system-level launch daemon may not have.
- **Prevent sleep:** System Settings → Energy → turn on *Prevent automatic sleeping
  when the display is off*. Alternatively, run `sudo pmset -a sleep 0`.
- **Firewall:** if the macOS firewall is on, allow incoming connections for the
  Python in `.venv` when prompted.
- **Network:** give the Mac a fixed IP or a DHCP reservation.

## Connect Home Assistant

With `--zeroconf` (the service turns it on), Home Assistant shows the server
under **Settings → Devices & services → Discovered**. Otherwise, add it manually:
**Add integration → Wyoming Protocol**, then enter the Mac's IP and port `10200`.

Then go to **Settings → Voice assistants**, open your assistant, choose
**kokoro-mlx** as the text-to-speech engine, and pick a voice.

## How requests are handled

- **Streaming** (`synthesize-start` / `-chunk` / `-stop`): text is split into
  sentences as it arrives. Each sentence is spoken as its own audio
  start/chunks/stop, and the server sends `synthesize-stopped` at the end. Home
  Assistant also sends a plain `synthesize` for older servers. Once a stream has
  started on a connection, that request is ignored so nothing is spoken twice.
  This matches `wyoming-piper`.
- **Plain** (`synthesize`): one audio stream for the whole text, sent one
  sentence at a time.
- Voice choice: the requested voice name if it exists. Otherwise, a voice that
  speaks the requested language (the default voice wins when it matches).
  Otherwise, the default voice.
- Audio is 24 kHz, 16-bit, mono.
- All model work runs on a single thread, so concurrent requests are handled
  one after another.

## Speed

Measured on an M5 Pro with warm caches. An M4 will be slower, but still several
times faster than real time.

| Text | Audio | Time to generate |
|---|---|---|
| "Good evening." | 1.65 s | 0.09 s |
| "The thermostat is set to 70 degrees, and it should feel warmer soon." | 4.95 s | 0.58 s |
