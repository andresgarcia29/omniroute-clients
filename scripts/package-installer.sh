#!/usr/bin/env bash
set -euo pipefail
version="${1:?expected a version tag}"
output="${2:?expected an output path}"
[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
sed -E "s/v[0-9]+\.[0-9]+\.[0-9]+/$version/g; s/omniroute-clients-[0-9]+\.[0-9]+\.[0-9]+/omniroute-clients-${version#v}/g" "$(dirname "$0")/../install.sh" > "$output"
bash -n "$output"
