#!/usr/bin/env python3
"""Register the gateway's models with the Kimi CLI (`kimi`).

OmniRoute ships no `setup-kimi`, so this writes the equivalent: a provider plus
one model alias per catalog entry, into ~/.kimi-code/config.toml.

Everything is written between marker comments and only that region is ever
rewritten. The identity's ``default_model`` is set to an OmniRoute alias so the
launcher starts on this gateway; every unrelated provider and setting survives.

Unlike the Codex side, the key has to be inline: a custom Kimi provider with
`api_key = ""` fails with `no credential configured`, and there is no env
fallback for it. ~/.omniroute/.env stays the source of truth, and re-running
this refreshes the copy after a rotation.

Config: ~/.omniroute/.env with OMNIROUTE_BASE_URL and OMNIROUTE_API_KEY.
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

ENV_FILE = pathlib.Path(os.environ.get("OMNIROUTE_ENV_FILE", pathlib.Path.home() / ".omniroute/.env"))
KIMI_HOME = pathlib.Path(os.environ.get("KIMI_CODE_HOME", pathlib.Path.home() / ".kimi-code"))
BEGIN = "# >>> omniroute (generated — everything to the end marker is rewritten)"
END = "# <<< omniroute"


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE}")
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def alias(model_id: str) -> str:
    return "omniroute/" + model_id.replace("/", "-")


def render(base_url: str, api_key: str, models: list[dict]) -> str:
    lines = [
        BEGIN,
        "",
        "[providers.omniroute]",
        'type = "openai"',
        f'api_key = "{api_key}"',
        f'base_url = "{base_url}/v1"',
    ]
    for m in models:
        model_caps = m.get("capabilities") or {}
        caps = ["tool_use"] if model_caps.get("tool_calling") else []
        if model_caps.get("thinking"):
            caps.insert(0, "thinking")
        model_lines = [
            "",
            f'[models."{alias(m["id"])}"]',
            'provider = "omniroute"',
            f'model = "{m["id"]}"',
            f'max_context_size = {m["context_length"]}',
        ]
        max_input = m.get("max_input_tokens")
        if isinstance(max_input, int) and 0 < max_input < int(m["context_length"]):
            model_lines.append(f"max_input_size = {max_input}")
        model_lines += [
            f'capabilities = [ {", ".join(chr(34) + c + chr(34) for c in caps)} ]',
            f'display_name = "{m["id"]} (OmniRoute)"',
        ]
        lines += model_lines
    return "\n".join(lines + ["", END, ""])


def splice(existing: str, block: str) -> str:
    """Replace the marked region, or append it when there is none."""
    start = existing.find(BEGIN)
    if start == -1:
        sep = "" if existing.endswith("\n") or not existing else "\n"
        return existing + sep + "\n" + block
    end = existing.find(END, start)
    if end == -1:
        return existing[:start] + block
    return existing[:start] + block + existing[end + len(END):].lstrip("\n")


def preferred_alias(models: list[dict]) -> str:
    model_ids = [model["id"] for model in models]
    for preferred in ("kmc/k3", "kmc/kimi-for-coding", "cx/gpt-5.6-sol"):
        if preferred in model_ids:
            return alias(preferred)
    return alias(model_ids[0])


def current_default(existing: str) -> str:
    match = re.search(r'(?m)^default_model\s*=\s*"([^"]*)"', existing)
    return match.group(1) if match else ""


def set_default_model(existing: str, model_alias: str) -> str:
    body = re.sub(r"(?m)^default_model\s*=.*\n?", "", existing).lstrip("\n")
    return f'default_model = "{model_alias}"\n\n{body}'


def self_check() -> int:
    assert alias("cx/gpt-5.6-sol") == "omniroute/cx-gpt-5.6-sol"
    models = [{"id": "kmc/k3", "context_length": 10,
               "capabilities": {"tool_calling": True, "thinking": True}}]
    block = render("https://x", "K", models)
    assert 'base_url = "https://x/v1"' in block
    assert 'capabilities = [ "thinking", "tool_use" ]' in block
    assert current_default('x = 1\ndefault_model = "omniroute/kmc-k3"\n') == "omniroute/kmc-k3"
    assert set_default_model('default_model = "old"\ntelemetry = false\n', "omniroute/kmc-k3") == (
        'default_model = "omniroute/kmc-k3"\n\ntelemetry = false\n'
    )

    # Re-splicing is stable and never touches what sits outside the markers.
    original = 'default_model = "kimi-code/k3"\n\n[providers."managed:kimi-code"]\ntype = "kimi"\n'
    once = splice(original, block)
    twice = splice(once, block)
    assert once == twice, "splice is not idempotent"
    assert once.startswith(original.rstrip("\n")[:40])
    assert '[providers."managed:kimi-code"]' in twice
    assert twice.count(BEGIN) == 1

    # A truncated region (end marker lost) is repaired rather than duplicated.
    assert splice(original + BEGIN + "\ngarbage\n", block).count(BEGIN) == 1
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
    keep_combos = os.environ.get("OMNIROUTE_KEEP_COMBOS") == "1"
    # Same substring filter the setup-* generators take, so one --only applies
    # across every CLI the installer touches.
    only = [p for p in os.environ.get("OMNIROUTE_ONLY", "").split(",") if p]
    models = [
        m for m in json.loads(raw)["data"]
        if m.get("context_length")
        and not m["id"].startswith("claude/")            # discovery mirrors
        and (keep_combos or m.get("owned_by") != "combo")
        and (not only or any(p in m["id"] for p in only))
    ]
    models.sort(key=lambda m: m["id"])

    config = KIMI_HOME / "config.toml"
    existing = config.read_text() if config.exists() else ""
    aliases = {alias(m["id"]) for m in models}
    default = current_default(existing)
    if default not in aliases:
        default = preferred_alias(models)
    updated = set_default_model(splice(existing, render(base, key, models)), default)
    if os.environ.get("OMNIROUTE_REFRESH") != "1" and existing and existing != updated:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = config.with_suffix(f".toml.bak-{stamp}")
        backup.write_text(existing)
        print(f"  backed up {config.name} -> {backup.name}")

    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(updated)
    config.chmod(0o600)
    print(f"\n{len(models)} kimi model aliases written to {config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
