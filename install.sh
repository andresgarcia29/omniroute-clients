#!/usr/bin/env bash
set -euo pipefail
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
curl -fsSL https://codeload.github.com/andresgarcia29/omniroute-clients/tar.gz/refs/tags/v1.0.0 -o "$workdir/clients.tar.gz"
tar -xzf "$workdir/clients.tar.gz" -C "$workdir"
bash "$workdir/omniroute-clients-1.0.0/scripts/install.sh" --url https://router.the-harness.io --keep-legacy "$@"
