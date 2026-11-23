#!/usr/bin/env python3
"""Register the OmniRoute catalog as opencode's only provider.

opencode reads ``{env:VAR}`` in provider options, so the credential stays in
``~/.omniroute/.env``. The file written is the launcher's own
``$OPENCODE_CONFIG_DIR/opencode.json``; ``enabled_providers`` hides every other
provider so ``/models`` lists exactly the gateway catalog.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ENV_FILE = pathlib.Path(
    os.environ.get("OMNIROUTE_ENV_FILE", pathlib.Path.home() / ".omniroute/.env")
)
OPENCODE_HOME = pathlib.Path(
    os.environ.get("OPENCODE_CONFIG_DIR", pathlib.Path.home() / ".config/opencode")
)
PROVIDER_ID = "omniroute"


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE}")
    result = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def context_size(model: dict) -> int:
    for key in ("context_length", "max_context_window_tokens", "max_input_tokens"):
        value = model.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def is_text_model(model: dict) -> bool:
    blocked = re.compile(
        r"(^|[/_-])(image|img|video|audio|speech|voice|tts|stt|whisper|"
        r"embedding|embeddings|embed|rerank|moderation|transcription)([/_-]|$)"
    )
    if blocked.search(str(model.get("id", "")).lower()) or blocked.search(str(model.get("type", "")).lower()):
        return False
    outputs = [str(value).lower() for value in model.get("output_modalities") or []]
    return not outputs or "text" in outputs


def selected_models(payload: dict) -> list[dict]:
    keep_combos = os.environ.get("OMNIROUTE_KEEP_COMBOS") == "1"
    only = [part for part in os.environ.get("OMNIROUTE_ONLY", "").split(",") if part]
    models = [
        model
        for model in payload.get("data", [])
        if model.get("id")
        and context_size(model)
        and is_text_model(model)
        and not str(model["id"]).startswith("claude/")
        and (keep_combos or model.get("owned_by") != "combo")
        and (not only or any(part in str(model["id"]) for part in only))
    ]
    return sorted(models, key=lambda model: str(model["id"]))


def render_provider(base_url: str, models: list[dict]) -> dict:
    rendered = {}
    for model in models:
        caps = model.get("capabilities") or {}
        size = context_size(model)
        rendered[str(model["id"])] = {
            "name": f'{model.get("display_name") or model["id"]} (OmniRoute)',
            "tool_call": caps.get("tool_calling", True) is not False,
            "reasoning": bool(caps.get("thinking") or caps.get("reasoning")),
            "attachment": bool(caps.get("vision")),
            "limit": {
                "context": size,
                "output": min(model.get("max_output_tokens") or 32_768, size),
            },
        }
    return {
        "npm": "@ai-sdk/openai-compatible",
        "name": "OmniRoute",
        "options": {"baseURL": f"{base_url}/v1", "apiKey": "{env:OMNIROUTE_API_KEY}"},
        "models": rendered,
    }


def preferred_model(models: list[dict]) -> str:
    ids = [str(model["id"]) for model in models]
    for preferred in ("cx/gpt-5.6-sol", "kmc/k3", "cc/claude-sonnet-5-5"):
        if preferred in ids:
            return preferred
    return ids[0]


def merge(existing: dict, provider: dict, models: list[dict]) -> dict:
    result = dict(existing)
    result.setdefault("$schema", "https://opencode.ai/config.json")
    result["provider"] = dict(result.get("provider") or {})
    result["provider"][PROVIDER_ID] = provider
    result["enabled_providers"] = [PROVIDER_ID]
    current = str(result.get("model", ""))
    if current.removeprefix(f"{PROVIDER_ID}/") not in provider["models"]:
        result["model"] = f"{PROVIDER_ID}/{preferred_model(models)}"
    return result


def self_check() -> int:
    payload = {
        "data": [
            {"id": "cx/gpt", "context_length": 400_000, "max_output_tokens": 128_000,
             "capabilities": {"vision": True, "thinking": True}},
            {"id": "kmc/k3", "context_length": 20_000, "capabilities": {"tool_calling": False}},
            {"id": "claude/cx/gpt", "context_length": 400_000},
            {"id": "image/model", "context_length": 10_000, "type": "image"},
        ]
    }
    models = selected_models(payload)
    provider = render_provider("https://x", models)
    assert list(provider["models"]) == ["cx/gpt", "kmc/k3"]
    assert provider["options"] == {"baseURL": "https://x/v1", "apiKey": "{env:OMNIROUTE_API_KEY}"}
    assert provider["models"]["cx/gpt"]["limit"] == {"context": 400_000, "output": 128_000}
    assert provider["models"]["cx/gpt"]["attachment"] and provider["models"]["cx/gpt"]["reasoning"]
    assert provider["models"]["kmc/k3"]["tool_call"] is False
    assert provider["models"]["kmc/k3"]["limit"]["output"] == 20_000
    once = merge({"theme": "x", "model": "omniroute/kmc/k3"}, provider, models)
    assert once["theme"] == "x" and once["model"] == "omniroute/kmc/k3"
    assert once["enabled_providers"] == ["omniroute"]
    assert merge(once, provider, models) == once
    assert merge({"model": "omniroute/gone"}, provider, models)["model"] == "omniroute/kmc/k3"
    print("self-check ok")
    return 0


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()
    env = read_env()
    base = env.get("OMNIROUTE_BASE_URL", "").rstrip("/").removesuffix("/v1")
    key = env.get("OMNIROUTE_API_KEY", "")
    if not base or not key:
        sys.exit(f"set OMNIROUTE_BASE_URL and OMNIROUTE_API_KEY in {ENV_FILE}")

    proc = subprocess.run(
        ["curl", "-sSf", "-m", os.environ.get("OMNIROUTE_TIMEOUT", "30"), f"{base}/v1/models", "-H", "@-"],
        input=f"Authorization: Bearer {key}\n",
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        sys.exit(f"{base}/v1/models failed: {proc.stderr.strip() or f'curl exit {proc.returncode}'}")
    models = selected_models(json.loads(proc.stdout))
    if not models:
        sys.exit("the gateway returned no opencode-compatible text models")

    config = OPENCODE_HOME / "opencode.json"
    existing = json.loads(config.read_text()) if config.exists() else {}
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(merge(existing, render_provider(base, models), models), indent=2) + "\n")
    config.chmod(0o600)
    print(f"\n{len(models)} opencode models written to {config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
