# Refiner STT (Rust)

On-prem speech-to-text service for Refiner, optimized for native Ubuntu arm64 deployments.
It keeps Whisper models in memory, decodes browser audio directly, and avoids Docker/cloud dependencies.

## Requirements

- Ubuntu 22.04+ (`arm64` / `aarch64`)
- Rust toolchain (stable)
- Local Whisper model file (for example `ggml-base.en.bin`)

## Build (native, no Docker)

```bash
RUSTFLAGS="-C target-cpu=native" cargo build --release
```

## Run (manual)

```bash
./target/release/refiner-stt \
  --model /opt/refiner/models/ggml-base.en.bin \
  --bind 127.0.0.1:7079 \
  --lang en-GB \
  --threads 2 \
  --workers 23 \
  --max-audio-bytes 8000000
```

## Audio Formats

The Rust server accepts common browser/container formats directly (`webm`, `ogg`, `wav`, `mp4`, `aac`) and resamples to `16kHz mono` internally.
No `ffmpeg` preprocess is needed in this server mode.

## API

- `GET /health` -> `ok`
- `POST /transcribe` (multipart form)
- form field `audio` (required file)
- form field `lang` (optional)
- form field `prompt` (optional initial vocabulary/context hint)
- form field `gesture_mode` (optional: `bsl` or `gesticulation`)
- alias fields `gestureMode`, `motion_style`, `motionStyle` (also accepted)
- form field `avatar_mode` (optional: `office` or `chat`)
- alias field `avatarMode` (also accepted)
- form field `office_mode` (optional bool, used when `avatar_mode` is omitted)
- alias field `officeMode` (also accepted)
- collaboration fields `collaboration_mode`, `collaborationMode`, `multi_speaker`, `multiSpeaker` (optional bool)

Response:

```json
{
  "status": "ok",
  "text": "...",
  "lang": "en-GB",
  "gesture_mode": "bsl",
  "avatar_mode": "office",
  "gesture_summary": { "style": "bsl_signing", "token_count": 9 },
  "gesture_timeline": [{ "word": "hello", "intent": "greeting", "template": "greeting", "start_ms": 80, "end_ms": 320 }],
  "avatar_motion": { "duration_ms": 2500, "keyframes": [{ "t": 0, "pose": { "...": "..." } }] }
}
```

Environment toggles:

- `REFINER_STT_GESTURE_ENABLED=1` (default `1`)
- `REFINER_STT_BSL_ENABLED=1` (default `1`)
- `REFINER_STT_GESTURE_DEFAULT_MODE=gesticulation`
- `REFINER_STT_GESTURE_DEFAULT_AVATAR_MODE=chat`
- `REFINER_STT_BUILTIN_CONTEXT_ENABLED=1` (default `1`, applies built-in NeuralMimicry terms locally)
- `REFINER_STT_BUILTIN_CONTEXT_PROMPT=...` (optional override for built-in local context)
- `REFINER_STT_PROMPT_ALLOW_CLIENT=0` (default `0`, avoids per-request prompt retransmission)
- `REFINER_STT_CANONICALIZE_ENTITIES=1` (default `1`, normalizes `Tracey`, `AARNN`, and known names)

Built-in context and canonicalization are intended to keep recognition fast while reducing repeated transmission of personal/domain terms.

## 46-Core arm64 Preset (systemd)

Quick deploy script:

```bash
./install_native_arm64.sh
```

Manual install:

1. Install binary and unit:

```bash
sudo install -d -m 755 /opt/refiner/stt_rust
sudo install -m 755 target/release/refiner-stt /opt/refiner/stt_rust/refiner-stt
sudo install -m 644 refiner-stt.service /etc/systemd/system/refiner-stt.service
sudo install -m 644 native_arm64_46core.env /etc/default/refiner-stt
```

2. Start and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now refiner-stt
sudo systemctl status refiner-stt
```

The default preset uses:

- `REFINER_STT_THREADS=2`
- `REFINER_STT_WORKERS=23`
- `TOKIO_WORKER_THREADS=46`

This drives high parallel throughput while keeping request handling non-blocking.
Each worker loads one Whisper context, so memory usage scales with `REFINER_STT_WORKERS`.

## Performance Tuning Notes

- Throughput-first (46 cores): `threads=2`, `workers=23`
- Lower-RAM fallback: `threads=4`, `workers=11`
- Rule of thumb: `workers ~= cpu_cores / threads`

Tune values in `/etc/default/refiner-stt`, then restart:

```bash
sudo systemctl restart refiner-stt
```

## Refiner Backend Integration

Set these vars on the Refiner API server:

```bash
REFINER_STT_BACKEND=server
REFINER_STT_SERVER_URL=http://127.0.0.1:7079
REFINER_STT_SERVER_TIMEOUT=25
REFINER_STT_SERVER_PREPROCESS=0
REFINER_STT_GESTURE_PREFER_SERVER=1
```

`REFINER_STT_SERVER_PREPROCESS=0` keeps server mode free from preprocess dependencies.

## whisper.cpp Preset (command backend)

If you want command-mode `whisper.cpp` instead of this Rust HTTP service, use:

- `whisper_cpp_preset.env`

and set:

```bash
REFINER_STT_BACKEND=command
```
