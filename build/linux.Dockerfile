# Fully static musl build. Pick the target with --platform and TARGET:
#   docker build -f build/linux.Dockerfile --platform linux/arm64 \
#     --build-arg TARGET=linux-aarch64 --output out/linux-aarch64 .
FROM alpine:3.22 AS build

RUN apk add --no-cache bash build-base cmake curl git nasm pkgconf linux-headers

WORKDIR /src
COPY . .

ARG TARGET
RUN build/build.sh "$TARGET" /out

FROM scratch
COPY --from=build /out/ /
