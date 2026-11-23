#!/usr/bin/env python3
"""Register the OmniRoute catalog in Pi's identity-local models.json.

Pi resolves ``$VAR`` in ``apiKey``, so the credential stays in
``~/.omniroute/.env``. The provider uses OpenAI Chat Completions because Pi's
OpenAI JS Responses client sends a User-Agent blocked by the gateway WAF; the
explicit provider-level User-Agent keeps the request on the proven route.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

ENV_FILE = pathlib.Path(
    os.environ.get("OMNIROUTE_ENV_FILE", pathlib.Path.home() / ".omniroute/.env")
)
PI_HOME = pathlib.Path(
    os.environ.get("PI_CODING_AGENT_DIR", pathlib.Path.home() / ".pi/agent")
)
PROVIDER_ID = "omniroute"
USER_AGENT = "omniroute-pi/1"


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE}")
    result = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def render_provider(base_url: str, models: list[dict]) -> dict:
    rendered = []
    for model in models:
        caps = model.get("capabilities") or {}
        item = {
            "id": model["id"],
            "name": f'{model["id"]} (OmniRoute)',
            "reasoning": bool(caps.get("thinking") or caps.get("reasoning")),
            "input": ["text", "image"] if caps.get("vision") else ["text"],
            "contextWindow": int(model["context_length"]),
            "maxTokens": model.get("max_output_tokens") or 32_768,
        }
        rendered.append(item)
    return {
        "baseUrl": f"{base_url}/v1",
        "api": "openai-completions",
        "apiKey": "$OMNIROUTE_API_KEY",
        "headers": {"User-Agent": USER_AGENT},
        "models": rendered,
    }


def selected_models(payload: dict) -> list[dict]:
    keep_combos = os.environ.get("OMNIROUTE_KEEP_COMBOS") == "1"
    only = [part for part in os.environ.get("OMNIROUTE_ONLY", "").split(",") if part]
    models = [
        model
        for model in payload.get("data", [])
        if model.get("context_length")
        and not str(model.get("id", "")).startswith("claude/")
        and (keep_combos or model.get("owned_by") != "combo")
        and (not only or any(part in str(model.get("id", "")) for part in only))
    ]
    return sorted(models, key=lambda model: model["id"])


def merge(existing: dict, provider: dict) -> dict:
    result = dict(existing)
    result["providers"] = dict(result.get("providers") or {})
    result["providers"][PROVIDER_ID] = provider
    return result


def self_check() -> int:
    models = [
        {
            "id": "cx/gpt",
            "context_length": 100,
            "max_output_tokens": 20,
            "capabilities": {"thinking": True, "vision": True},
        }
    ]
    provider = render_provider("https://x", models)
    assert provider["baseUrl"] == "https://x/v1"
    assert provider["api"] == "openai-completions"
    assert provider["apiKey"] == "$OMNIROUTE_API_KEY"
    assert provider["headers"]["User-Agent"] == USER_AGENT
    assert provider["models"][0]["input"] == ["text", "image"]
    before = {"providers": {"ollama": {"baseUrl": "http://localhost"}}, "other": 1}
    once = merge(before, provider)
    assert merge(once, provider) == once
    assert once["providers"]["ollama"] == before["providers"]["ollama"]
    assert PROVIDER_ID not in before["providers"]
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
    raw = proc.stdout
    models = selected_models(json.loads(raw))

    config = PI_HOME / "models.json"
    if config.exists():
        try:
            existing_text = config.read_text()
            existing = json.loads(existing_text)
        except ValueError as error:
            sys.exit(f"refusing to overwrite non-JSON {config}: {error}")
    else:
        existing_text = ""
        existing = {}
    rendered = json.dumps(merge(existing, render_provider(base, models)), indent=2) + "\n"
    if os.environ.get("OMNIROUTE_REFRESH") != "1" and existing_text and existing_text != rendered:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = config.with_suffix(f".json.bak-{stamp}")
        backup.write_text(existing_text)
        print(f"  backed up {config.name} -> {backup.name}")

    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(rendered)
    config.chmod(0o600)
    print(f"\n{len(models)} Pi models written to {config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
