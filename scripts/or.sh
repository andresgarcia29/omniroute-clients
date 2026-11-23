#!/usr/bin/env bash
# One launcher for every OmniRoute client, installed as or-claude, or-codex,
# or-kimi, or-pi and or-opencode. Each client runs in its own state dir, never
# the plain client's, and its model list is rebuilt from the gateway's
# /v1/models on every launch (the last good list is kept when it is down).
#
#   or-<client>                     open the client; pick models natively
#   or-<client> cx/gpt-5.6-sol      start on a gateway model id
#   or-<client> --continue          pass ordinary client flags through
set -euo pipefail

client="${OR_CLIENT:-${0##*/}}"
client="${client#or-}"
OR_HOME="${OR_HOME:-$HOME/.omniroute}"
ENV_FILE="${OMNIROUTE_ENV_FILE:-$OR_HOME/.env}"

case "$client" in
  claude)   home_var=CLAUDE_CONFIG_DIR   plain="$HOME/.claude"           fixer=fix-profiles ready=settings.json ;;
  codex)    home_var=CODEX_HOME          plain="$HOME/.codex"            fixer=fix-codex    ready=omniroute.config.toml ;;
  kimi)     home_var=KIMI_CODE_HOME      plain="$HOME/.kimi-code"        fixer=fix-kimi     ready=config.toml ;;
  pi)       home_var=PI_CODING_AGENT_DIR plain="$HOME/.pi/agent"         fixer=fix-pi       ready=models.json ;;
  opencode) home_var=OPENCODE_CONFIG_DIR plain="$HOME/.config/opencode"  fixer=fix-opencode ready=opencode.json ;;
  *) echo "unknown OmniRoute client '$client'; install as or-claude, or-codex, or-kimi, or-pi or or-opencode" >&2; exit 2 ;;
esac

if [[ -n "${WORKENV:-}" ]]; then
  [[ "${WORKENV_OMNIROUTE:-0}" == 1 ]] || { echo "OmniRoute is disabled for workenv '$WORKENV'" >&2; exit 1; }
  dir="${WORKENV_ROOT:-${WORKENV_HOME:-$HOME/.workenv}/$WORKENV}/omniroute/$client"
else
  dir="$OR_HOME/clients/$client"
fi
case "$dir" in
  "$plain"|"$plain/"*|"${!home_var:-$plain}")
    echo "refusing to use $dir: it belongs to plain $client" >&2; exit 1 ;;
esac

mkdir -p "$dir"
[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE: run the installer first" >&2; exit 1; }
setting() { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }
base="$(setting OMNIROUTE_BASE_URL)"
key="$(setting OMNIROUTE_API_KEY)"
[[ -n "$base" && -n "$key" ]] || { echo "OMNIROUTE_BASE_URL/OMNIROUTE_API_KEY not set in $ENV_FILE" >&2; exit 1; }

if ! env "$home_var=$dir" OMNIROUTE_ENV_FILE="$ENV_FILE" OMNIROUTE_REFRESH=1 OMNIROUTE_TIMEOUT=5 \
     OMNIROUTE_ONLY="$(setting OMNIROUTE_ONLY)" OMNIROUTE_KEEP_COMBOS="$(setting OMNIROUTE_KEEP_COMBOS)" \
     python3 "$OR_HOME/lib/$fixer.py" >/dev/null 2>"$dir/.refresh.log"; then
  [[ -f "$dir/$ready" ]] || {
    echo "could not read models from $base and no cached catalog: $(tail -1 "$dir/.refresh.log")" >&2; exit 1;
  }
  echo "warn: $base unreachable, using the cached model list" >&2
fi

# A gateway model id has a slash and no spaces; anything else is the client's own argument.
model=""
if [[ $# -gt 0 && "$1" == */* && "$1" != -* && "$1" != *" "* ]]; then
  model="$1"
  shift
fi

export "$home_var=$dir" OMNIROUTE_API_KEY="$key"
case "$client" in
  claude)
    export ANTHROPIC_BASE_URL="$base" ANTHROPIC_AUTH_TOKEN="$key"
    unset ANTHROPIC_API_KEY ANTHROPIC_MODEL ANTHROPIC_DEFAULT_MODEL \
      ANTHROPIC_DEFAULT_FABLE_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL \
      ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL \
      CLAUDE_CODE_AUTO_COMPACT_WINDOW CLAUDE_CODE_MAX_CONTEXT_TOKENS CLAUDE_CODE_OAUTH_TOKEN \
      CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_BEDROCK ANTHROPIC_VERTEX_BASE_URL \
      ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION
    exec claude ${model:+--model "$model"} --autocompact auto "$@" ;;
  codex)
    unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE CODEX_API_KEY
    exec codex --profile omniroute ${model:+--model "$model"} "$@" ;;
  kimi)
    [[ -z "$model" || "$model" == omniroute/* ]] || model="omniroute/${model//\//-}"
    exec kimi ${model:+--model "$model"} "$@" ;;
  pi)
    export PI_CODING_AGENT_SESSION_DIR="$dir/sessions"
    exec pi --provider omniroute --models 'omniroute/*' ${model:+--model "$model"} "$@" ;;
  opencode)
    # opencode keeps sessions, auth and caches under the XDG dirs, not its config dir.
    export OPENCODE_CONFIG="$dir/opencode.json" \
      XDG_DATA_HOME="$dir/data" XDG_STATE_HOME="$dir/state" XDG_CACHE_HOME="$dir/cache"
    exec opencode ${model:+--model "omniroute/${model#omniroute/}"} "$@" ;;
esac
