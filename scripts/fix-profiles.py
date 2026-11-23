#!/usr/bin/env python3
"""Configure one native Claude Code model picker for OmniRoute.

Claude Code is intentionally different from Codex and Kimi: gateway discovery
only reads ``id`` and ``display_name`` from ``/v1/models``. It cannot import a
per-model context window. Restricting this client to recognizable Anthropic
model IDs lets Claude Code own model selection, effort and compaction safely:

* regular 200K Claude IDs use their native window;
* 1M routes are written with Claude Code's ``[1m]`` suffix;
* effort-suffixed aliases are omitted because ``/effort`` is the native control;
* combo, mirror, ``no-think`` and ``dva`` routes are omitted.

The script merges ``env`` and ``modelPicker`` into the identity-local
``settings.json`` and preserves every unrelated setting.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

ENV_FILE = pathlib.Path(
    os.environ.get("OMNIROUTE_ENV_FILE", pathlib.Path.home() / ".omniroute/.env")
)
CLAUDE_HOME = pathlib.Path(
    os.environ.get("CLAUDE_CONFIG_DIR", pathlib.Path.home() / ".claude")
)
EFFORT_SUFFIXES = ("-ultra", "-max", "-xhigh", "-high", "-medium", "-low")
LEGACY_ENV_KEYS = {
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
}


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


def is_plain_claude_route(model: dict) -> bool:
    model_id = str(model.get("id", ""))
    lowered = model_id.lower()
    if not re.search(r"(?:^|[/.])claude-(?:fable|opus|sonnet|haiku)-", lowered):
        return False
    if lowered.startswith(("claude/", "no-think/", "dva/")):
        return False
    if model.get("owned_by") == "combo":
        return False
    return not lowered.endswith(EFFORT_SUFFIXES)


def picker_option(model: dict) -> dict | None:
    """Translate one gateway route into an exact native Claude picker row.

    Claude Code can express 200K and 1M windows natively. Anything in between
    would require a process-wide override and is therefore deliberately omitted.
    """
    if not is_plain_claude_route(model):
        return None
    size = context_size(model)
    if size == 200_000:
        selected_id = model["id"]
    elif size >= 1_000_000:
        selected_id = f'{model["id"]}[1m]'
    else:
        return None
    display = model.get("display_name") or model["id"]
    window = "1M" if size >= 1_000_000 else "200K"
    return {
        "model": selected_id,
        "label": f"{display} · {window}",
        "description": f"{model['id']} through OmniRoute; reasoning is selected with /effort",
    }


def model_version(model_id: str) -> tuple[int, ...]:
    match = re.search(r"claude-(?:fable|opus|sonnet|haiku)-([0-9][0-9.-]*)", model_id)
    return tuple(int(part) for part in re.findall(r"\d+", match.group(1))) if match else ()


def option_sort_key(option: dict) -> tuple:
    model_id = option["model"].lower()
    family_order = next(
        (index for index, family in enumerate(("fable", "opus", "sonnet", "haiku"))
         if f"claude-{family}-" in model_id),
        9,
    )
    provider_order = 0 if model_id.startswith("cc/") else 1
    return family_order, provider_order, model_id


def choose_default(options: list[dict], existing_model: str | None) -> str:
    by_wire_id = {option["model"].removesuffix("[1m]"): option["model"] for option in options}
    if existing_model:
        normalized = existing_model.removesuffix("[1m]")
        if normalized in by_wire_id:
            return by_wire_id[normalized]

    sonnets = [option["model"] for option in options if "claude-sonnet-" in option["model"]]
    candidates = sonnets or [option["model"] for option in options]
    return max(
        candidates,
        key=lambda model_id: (model_id.startswith("cc/"), model_version(model_id)),
    )


def build_settings(existing: dict, base_url: str, options: list[dict]) -> dict:
    if not options:
        raise ValueError("the gateway exposes no native 200K/1M Anthropic model routes")
    result = dict(existing)
    env = dict(result.get("env") or {})
    for key in LEGACY_ENV_KEYS:
        env.pop(key, None)
    env["ANTHROPIC_BASE_URL"] = base_url
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    result["env"] = env
    result["modelPicker"] = {
        "options": sorted(options, key=option_sort_key),
        "replaceBuiltInOptions": True,
    }
    result["model"] = choose_default(options, result.get("model"))
    return result


def selected_options(payload: dict) -> tuple[list[dict], int]:
    only = [part for part in os.environ.get("OMNIROUTE_ONLY", "").split(",") if part]
    options = []
    skipped_context = 0
    for model in payload.get("data", []):
        if only and not any(part in str(model.get("id", "")) for part in only):
            continue
        option = picker_option(model)
        if option:
            options.append(option)
        elif is_plain_claude_route(model):
            skipped_context += 1
    unique = {option["model"]: option for option in options}
    return list(unique.values()), skipped_context


def self_check() -> int:
    payload = {
        "data": [
            {"id": "cc/claude-opus-5", "context_length": 1_000_000},
            {"id": "cc/claude-opus-5-high", "context_length": 1_000_000},
            {"id": "cc/claude-haiku-4-5-20251001", "context_length": 200_000},
            {"id": "claude/cc/claude-opus-5", "context_length": 1_000_000},
            {"id": "dva/claude-opus-5", "context_length": 1_000_000},
            {"id": "other/claude-odd-1", "context_length": 400_000},
            {"id": "cx/gpt-5.6-sol", "context_length": 872_000},
        ]
    }
    options, skipped = selected_options(payload)
    assert {option["model"] for option in options} == {
        "cc/claude-opus-5[1m]",
        "cc/claude-haiku-4-5-20251001",
    }
    assert skipped == 0
    settings = build_settings(
        {"theme": "dark", "env": {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "200000"}},
        "https://x",
        options,
    )
    assert settings["theme"] == "dark"
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in settings["env"]
    assert settings["model"] == "cc/claude-opus-5[1m]"
    assert settings["modelPicker"]["replaceBuiltInOptions"] is True
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
    options, skipped_context = selected_options(json.loads(raw))
    config = CLAUDE_HOME / "settings.json"
    existing = json.loads(config.read_text()) if config.exists() else {}
    updated = build_settings(existing, base, options)
    rendered = json.dumps(updated, indent=2) + "\n"

    refresh = os.environ.get("OMNIROUTE_REFRESH") == "1"
    if not refresh and config.exists() and config.read_text() != rendered:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = config.with_suffix(f".json.bak-{stamp}")
        backup.write_text(config.read_text())
        print(f"  backed up {config.name} -> {backup.name}")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(rendered)
    config.chmod(0o600)
    print(f"\n{len(options)} native Claude picker rows written to {config}")
    if skipped_context:
        print(f"  skipped {skipped_context} Claude routes whose context was neither 200K nor 1M")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
