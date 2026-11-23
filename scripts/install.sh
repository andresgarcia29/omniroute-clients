#!/usr/bin/env bash
# Install the OmniRoute launchers: or-claude, or-codex, or-kimi, or-pi, or-opencode.
#
#   ./scripts/install.sh [--url URL] [--only a,b] [--with-combos] [--keep-legacy]
#
# Each or-* runs in its own state dir (~/.omniroute/clients/<client>, or
# ~/.workenv/<identity>/omniroute/<client> inside a workenv) and rebuilds its
# model list from the gateway's /v1/models on every launch. Plain clients are
# never touched; --keep-legacy skips removing what older installers wrote there.
set -euo pipefail

DEFAULT_URL="${OMNIROUTE_DEFAULT_URL:-https://router.the-harness.io}"
KEEP_COMBOS="${OMNIROUTE_KEEP_COMBOS:-0}"
BASE_URL="" ; ONLY="" ; KEEP_LEGACY=0
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
OR_HOME="${OR_HOME:-$HOME/.omniroute}"
ENV_FILE="${OMNIROUTE_ENV_FILE:-$OR_HOME/.env}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) BASE_URL="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --keep-legacy) KEEP_LEGACY=1; shift ;;
    --targets) echo "warn: --targets is obsolete, each workenv identity is configured on first launch" >&2; shift 2 ;;
    --with-combos) KEEP_COMBOS=1; shift ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ask()  { local __v="$1" __p="$2" __s="${3:-}" __r="" __rc=0
         # shellcheck disable=SC2086
         if   [[ -t 0 ]];        then read -r ${__s:+-s} -p "$__p" __r || __rc=$?
         elif [[ -r /dev/tty ]]; then read -r ${__s:+-s} -p "$__p" __r < /dev/tty || __rc=$?
         else return 1; fi
         [[ -n "$__s" ]] && echo >&2
         (( __rc == 0 )) || return "$__rc"
         printf -v "$__v" '%s' "$__r"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m fail\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "this installer targets macOS"

say "checking prerequisites"
command -v python3 >/dev/null || die "python3 not found"
command -v curl    >/dev/null || die "curl not found"

if [[ -z "$BASE_URL" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    BASE_URL="$(grep -m1 '^OMNIROUTE_BASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
  fi
  if [[ -z "$BASE_URL" ]]; then
    ask BASE_URL "OmniRoute base URL [$DEFAULT_URL]: " || true
    BASE_URL="${BASE_URL:-$DEFAULT_URL}"
  fi
fi
BASE_URL="${BASE_URL%/}"; BASE_URL="${BASE_URL%/v1}"

API_KEY="${OMNIROUTE_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$ENV_FILE" ]]; then
  API_KEY="$(grep -m1 '^OMNIROUTE_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
  [[ -n "$API_KEY" ]] && say "reusing the key already in $ENV_FILE"
fi
if [[ -z "$API_KEY" ]]; then
  ask API_KEY "OmniRoute API key: " secret \
    || die "no API key — set OMNIROUTE_API_KEY, or run this from a terminal"
fi
[[ -n "$API_KEY" ]] || die "no API key given"

say "verifying $BASE_URL"
code="$(curl -s -A omniroute-cli -o /dev/null -m 20 -w '%{http_code}' "$BASE_URL/v1/models" \
        -H @- <<<"Authorization: Bearer $API_KEY")"
case "$code" in
  200) printf '      /v1/models → 200\n' ;;
  401) die "401 — the gateway rejected that API key" ;;
  30*) die "$code — an auth proxy is intercepting /v1" ;;
  *)   die "unexpected HTTP $code from $BASE_URL/v1/models" ;;
esac

say "writing $ENV_FILE"
mkdir -p "$(dirname "$ENV_FILE")"
umask 077
cat > "$ENV_FILE" <<ENVEOF
OMNIROUTE_BASE_URL=$BASE_URL
OMNIROUTE_API_KEY=$API_KEY
OMNIROUTE_ONLY=$ONLY
OMNIROUTE_KEEP_COMBOS=$KEEP_COMBOS
ENVEOF
chmod 600 "$ENV_FILE"

say "installing the launcher and model refreshers under $OR_HOME/lib"
mkdir -p "$OR_HOME/lib" "$BIN_DIR"
chmod 700 "$OR_HOME"
install -m 700 "$HERE/or.sh" "$HERE"/fix-*.py "$HERE/clean-legacy.py" "$OR_HOME/lib/"

if [[ "$KEEP_LEGACY" -eq 0 ]]; then
  say "removing what older installers wrote into the plain clients (backups kept)"
  "$OR_HOME/lib/clean-legacy.py"
fi

say "installing launchers under $BIN_DIR"
installed=()
for client in claude codex kimi pi opencode; do
  command -v "$client" >/dev/null || { warn "$client not found, skipping or-$client"; continue; }
  ln -sfn "$OR_HOME/lib/or.sh" "$BIN_DIR/or-$client"
  installed+=("or-$client")
done
(( ${#installed[@]} )) || die "none of claude, codex, kimi, pi or opencode is installed"
for old in omniroute-claude omniroute-codex omniroute-kimi omniroute-pi; do
  if [[ -f "$BIN_DIR/$old" ]] && grep -q OMNIROUTE "$BIN_DIR/$old"; then rm -f "$BIN_DIR/$old"; fi
done

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH — add it to your shell profile:"
     # shellcheck disable=SC2016
     printf '        export PATH="%s:$PATH"\n' "$BIN_DIR" >&2 ;;
esac

say "done: ${installed[*]}"
printf '\n  Models are re-read from %s on every launch; pick them with the client'"'"'s own selector.\n\n' "$BASE_URL"
