#!/usr/bin/env bash
# Transcribe any audio/video via whisper.cpp large-v3-turbo.
# ffmpeg -> 16kHz mono WAV (whisper-cli reads only WAV PCM), tmp cleaned up.
#   wt voice.m4a -l ru           transcription to stdout
#   wt video.mp4 -otxt -of out   text file (extra flags pass to whisper-cli)
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

f="${1:-}"
if [ -z "$f" ] || [ ! -f "$f" ]; then
  echo "usage: wt <audio|video> [whisper-cli flags]" >&2; exit 1
fi
shift

model="$HOME/.local/share/whisper/models/ggml-large-v3-turbo.bin"
[ -f "$model" ] || { echo "wt: model missing ($model) — run ~/dotfiles/scripts/bootstrap.sh" >&2; exit 1; }

tmpd="$(mktemp -d "${TMPDIR:-/tmp}/wt.XXXXXX")"
wav="$tmpd/wt.wav"
trap 'rm -rf "$tmpd"' EXIT
ffmpeg -y -v error -i "$f" -ar 16000 -ac 1 -c:a pcm_s16le "$wav"
whisper-cli -m "$model" -f "$wav" "$@"
