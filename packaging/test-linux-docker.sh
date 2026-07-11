#!/bin/bash
# Simulate the Linux release locally with Docker:
#   1. Build the Linux binary in a full container (python + rust).
#   2. Run packaging/smoke-test.sh in a BARE container (no python) to prove the
#      binary is truly self-contained.
#
#   packaging/test-linux-docker.sh
#
# Needs Docker. The bare-container stage hits the live PokeAPI; export
# GRACY_SKIP_NET=1 to run offline checks only.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

echo "== stage 1: build the Linux binary (python:3.12 + rust) =="
docker run --rm --platform linux/amd64 \
  -v "$REPO":/src:ro -v "$OUT":/out \
  python:3.12-slim-bookworm bash -euo pipefail -c '
    apt-get update -qq && apt-get install -y -qq curl build-essential >/dev/null
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null 2>&1
    . "$HOME/.cargo/env"
    mkdir /build
    tar -C /src --exclude=./.venv --exclude=./target --exclude=./.git \
        --exclude=./dist --exclude=./dist-bin --exclude=./dist-spec \
        --exclude=./build --exclude=./pib -cf - . | tar -C /build -xf -
    cd /build
    python -m venv /build/venv && . /build/venv/bin/activate
    pip install --quiet maturin pyinstaller rich prompt_toolkit
    maturin build --release --out /build/wheels 2>&1 | tail -1
    pip install --quiet /build/wheels/*.whl
    pyinstaller packaging/gracy.spec --clean --noconfirm --distpath /build/dist --workpath /build/pib 2>&1 | tail -1
    cp /build/dist/gracy /out/gracy-linux-x86_64
    echo "built: $(ls -lh /out/gracy-linux-x86_64 | awk "{print \$5}")"
  '

echo
echo "== stage 2: sanity test in a BARE container (no python) =="
docker run --rm --platform linux/amd64 \
  -e GRACY_SKIP_NET="${GRACY_SKIP_NET:-0}" \
  -v "$OUT/gracy-linux-x86_64":/usr/local/bin/gracy:ro \
  -v "$REPO/packaging/smoke-test.sh":/smoke-test.sh:ro \
  debian:bookworm-slim bash -euo pipefail -c '
    echo "python present? $(command -v python3 || echo NO)"
    apt-get update -qq && apt-get install -y -qq ca-certificates >/dev/null
    sh /smoke-test.sh gracy
  '
