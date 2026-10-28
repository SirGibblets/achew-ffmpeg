#!/usr/bin/env bash
# Generate one short fixture per container/codec achew accepts, using a full
# reference ffmpeg (the lean build can't encode most of these).
#
#   tests/make_fixtures.sh [out_dir]      REF_FFMPEG=/path/to/ffmpeg to override
#
# Each fixture is 12 s of pink noise in 3 s bursts separated by 1.5 s of
# silence, so silencedetect at achew's -30 dB / 1 s settings finds exactly 2
# silences. Formats that carry cover art get one, and the m4b gets chapters.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/tests/fixtures/generated}"
FF="${REF_FFMPEG:-ffmpeg}"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

has_encoder() { "$FF" -hide_banner -encoders 2>/dev/null | grep -q " $1 "; }
ff() { "$FF" -hide_banner -loglevel error -y "$@"; }

ff -f lavfi -i "anoisesrc=d=12:c=pink:r=44100:a=0.25" \
    -af "volume='if(lt(mod(t,4.5),3),1,0)':eval=frame" -ac 2 -c:a pcm_s16le "$TMP/signal.wav"
ff -f lavfi -i "color=c=0x336699:s=64x64:d=1" -frames:v 1 "$TMP/cover.jpg"
ff -f lavfi -i "color=c=0x993366:s=64x64:d=1" -frames:v 1 "$TMP/cover.png"
cat >"$TMP/chapters.txt" <<'EOF'
;FFMETADATA1
title=Fixture Book
[CHAPTER]
TIMEBASE=1/1000
START=0
END=4500
title=Chapter 1
[CHAPTER]
TIMEBASE=1/1000
START=4500
END=12000
title=Chapter 2
EOF

if has_encoder libmp3lame; then mp3=(-c:a libmp3lame -b:a 64k); else mp3=(-c:a mp3 -b:a 64k); fi
if has_encoder libvorbis; then vorbis=(-c:a libvorbis -q:a 3); else vorbis=(-c:a vorbis -strict experimental); fi
if has_encoder libopus; then opus=(-c:a libopus -b:a 48k); else opus=(-c:a opus -strict experimental -b:a 48k); fi

S="$TMP/signal.wav"
ff -i "$S" -i "$TMP/cover.jpg" -map 0:a -map 1:v "${mp3[@]}" -c:v copy -id3v2_version 3 \
    -disposition:v attached_pic "$OUT/mp3-cover.mp3"
ff -i "$S" -i "$TMP/chapters.txt" -i "$TMP/cover.jpg" -map 0:a -map 2:v -map_metadata 1 -map_chapters 1 \
    -c:a aac -b:a 64k -c:v copy -disposition:v attached_pic "$OUT/aac-cover-chapters.m4b"
ff -i "$S" -c:a aac -b:a 64k "$OUT/aac.mp4"
ff -i "$S" -c:a alac "$OUT/alac.m4a"
ff -i "$S" -i "$TMP/cover.png" -map 0:a -map 1:v -c:a flac -c:v copy -disposition:v attached_pic "$OUT/flac-cover.flac"
ff -i "$S" "${vorbis[@]}" "$OUT/vorbis.ogg"
ff -i "$S" "${opus[@]}" "$OUT/opus.opus"
ff -i "$S" "${opus[@]}" "$OUT/opus.mka"
ff -i "$S" -c:a pcm_s16le "$OUT/pcm.wav"
ff -i "$S" -c:a pcm_s16be "$OUT/pcm.aiff"
ff -i "$S" -c:a alac "$OUT/alac.caf"
ff -i "$S" -c:a wmav2 -b:a 64k "$OUT/wma.wma"
ff -i "$S" -c:a ac3 -b:a 96k "$OUT/ac3.ac3"
ff -i "$S" -c:a eac3 -b:a 96k "$OUT/eac3.eac3"
ff -i "$S" -c:a aac -b:a 64k "$OUT/adts.aac"

ls "$OUT"
