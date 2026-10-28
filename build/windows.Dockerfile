# Static mingw-w64 cross build for windows-x86_64. Runs on any host arch:
#   docker build -f build/windows.Dockerfile --output out/windows-x86_64 .
FROM debian:trixie-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash build-essential ca-certificates cmake curl git nasm pkgconf \
        mingw-w64 g++-mingw-w64-x86-64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .

RUN build/build.sh windows-x86_64 /out

FROM scratch
COPY --from=build /out/ /
