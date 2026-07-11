#!/bin/sh
# Install the standalone `gracy` binary (no Python needed).
#   curl -fsSL https://raw.githubusercontent.com/guilatrova/gracy/main/packaging/install.sh | sh
#
# Env vars:
#   GRACY_VERSION   tag to install (default: latest release)
#   GRACY_BIN_DIR   install dir (default: ~/.local/bin)
set -eu

REPO="guilatrova/gracy"
BIN_DIR="${GRACY_BIN_DIR:-$HOME/.local/bin}"

os="$(uname -s)"
arch="$(uname -m)"
case "$os" in
  Darwin) case "$arch" in
            arm64) asset="gracy-macos-arm64" ;;
            x86_64) asset="gracy-macos-x86_64" ;;
            *) echo "unsupported macOS arch: $arch" >&2; exit 1 ;;
          esac ;;
  Linux)  case "$arch" in
            x86_64|amd64) asset="gracy-linux-x86_64" ;;
            *) echo "unsupported Linux arch: $arch (Windows/other: download from the Releases page)" >&2; exit 1 ;;
          esac ;;
  *) echo "unsupported OS: $os. On Windows, download gracy-windows-x86_64.exe from the Releases page." >&2; exit 1 ;;
esac

if [ -n "${GRACY_VERSION:-}" ]; then
  url="https://github.com/$REPO/releases/download/$GRACY_VERSION/$asset"
else
  url="https://github.com/$REPO/releases/latest/download/$asset"
fi

echo "Downloading $asset ..."
mkdir -p "$BIN_DIR"
tmp="$(mktemp)"
curl -fSL "$url" -o "$tmp"
chmod +x "$tmp"
mv "$tmp" "$BIN_DIR/gracy"

echo "Installed gracy to $BIN_DIR/gracy"
case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) echo "Add $BIN_DIR to your PATH, e.g.:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
echo "Run:  gracy explore https://pokeapi.co/api/v2"
