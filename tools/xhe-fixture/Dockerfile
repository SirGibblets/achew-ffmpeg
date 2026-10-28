# Regenerates tests/fixtures/xhe-aac-speech.m4a, which is committed:
#   docker build -f tools/xhe-fixture/Dockerfile --output tests/fixtures .
#
# The fixture is synthetic speech (espeak-ng, original text in speech.ssml)
# encoded as xHE-AAC by FFmpeg master's libfdk_xhe_aac encoder. Low-bitrate
# speech makes the encoder switch to its speech mode (LPD), which is exactly
# what FFmpeg's native decoder can't handle, so the smoke tests can prove the
# bundled libfdk_aac decoder does. This throwaway encoder build is never
# distributed.
FROM alpine:3.22 AS build

RUN apk add --no-cache bash build-base cmake git nasm pkgconf linux-headers espeak-ng

ARG FDK_AAC_COMMIT=a68a51aa45d86a43c7b228e3d45624af09dcdd8d
RUN git init -q /fdk-aac \
    && git -C /fdk-aac fetch -q --depth 1 https://github.com/mstorsjo/fdk-aac.git "$FDK_AAC_COMMIT" \
    && git -C /fdk-aac checkout -q FETCH_HEAD \
    && cmake -S /fdk-aac -B /fdk-aac/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    && cmake --build /fdk-aac/build -j"$(nproc)" \
    && cmake --install /fdk-aac/build

# FFmpeg master: the libfdk_xhe_aac encoder isn't in any release yet
ARG FFMPEG_COMMIT=c69954c34522c53234dfd43fa7a0cb3b02f39e47
RUN git init -q /ffmpeg \
    && git -C /ffmpeg fetch -q --depth 1 https://github.com/FFmpeg/FFmpeg.git "$FFMPEG_COMMIT" \
    && git -C /ffmpeg checkout -q FETCH_HEAD \
    && cd /ffmpeg \
    && ./configure --disable-everything --disable-autodetect --disable-doc --disable-ffplay --disable-ffprobe \
        --enable-pthreads --enable-libfdk-xhe-aac --enable-encoder=libfdk_xhe_aac \
        --enable-decoder=pcm_s16le --enable-demuxer=wav --enable-muxer=mp4,ipod \
        --enable-protocol=file --enable-filter=aresample,aformat,anull \
        --pkg-config-flags=--static --extra-ldflags="-static -Wl,-z,stack-size=2097152" \
    && make -j"$(nproc)" ffmpeg

COPY tools/xhe-fixture/speech.ssml /speech.ssml
RUN mkdir /out \
    && espeak-ng -m -v en-us -s 165 -f /speech.ssml -w /speech.wav \
    && /ffmpeg/ffmpeg -hide_banner -i /speech.wav -ac 2 -ar 44100 \
        -c:a libfdk_xhe_aac -b:a 32k -movflags +faststart \
        -metadata title="achew-ffmpeg xHE-AAC test fixture" /out/xhe-aac-speech.m4a

FROM scratch
COPY --from=build /out/ /
