#!/usr/bin/env python3
"""Build one native Codex model catalog for an OmniRoute identity.

Codex accepts a JSON model catalog whose entries carry their own context and
auto-compaction limits. That makes one catalog plus the native ``/model`` picker
strictly better than one TOML profile per model. The bundled catalog supplies
the current client schema and instructions; gateway-specific identity, context
and capability fields are overlaid from ``/v1/models``.

The catalog, the default model and the provider live in the layered profile
``omniroute.config.toml``, never in the identity's base ``config.toml``: plain
``codex`` must keep listing the models its own account can reach.
"""
import copy
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
CODEX_HOME = pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex"))
CATALOG_NAME = "omniroute-models.json"
PROVIDER_ID = "omniroute"
PROFILE_NAME = "omniroute"
PROFILE_FILE = f"{PROFILE_NAME}.config.toml"
BEGIN = "# >>> omniroute provider (generated)"
END = "# <<< omniroute provider"
EFFORT_SUFFIXES = ("ultra", "max", "xhigh", "high", "medium", "low")


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
    model_id = str(model.get("id", "")).lower()
    model_type = str(model.get("type", "")).lower()
    blocked = re.compile(
        r"(^|[/_-])(image|img|video|audio|speech|voice|tts|stt|whisper|"
        r"embedding|embeddings|embed|rerank|moderation|transcription)([/_-]|$)"
    )
    if blocked.search(model_id) or blocked.search(model_type):
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


def bundled_template() -> dict:
    result = subprocess.run(
        ["codex", "debug", "models", "--bundled"],
        capture_output=True,
        text=True,
        check=True,
    )
    models = json.loads(result.stdout).get("models") or []
    if not models:
        sys.exit("codex returned an empty bundled model catalog")
    return models[0]


def reasoning_effort(model_id: str, template: dict) -> str:
    lowered = model_id.lower()
    for effort in EFFORT_SUFFIXES:
        if lowered.endswith(f"-{effort}"):
            return effort
    supported = [item.get("effort") for item in template.get("supported_reasoning_levels") or []]
    return template.get("default_reasoning_level") if template.get("default_reasoning_level") in supported else "medium"


def catalog_entry(model: dict, template: dict, priority: int) -> dict:
    entry = copy.deepcopy(template)
    model_id = str(model["id"])
    size = context_size(model)
    caps = model.get("capabilities") or {}
    entry.update(
        {
            "slug": model_id,
            "display_name": model.get("display_name") or model_id,
            "description": f"{model_id} through OmniRoute · {size:,} token context",
            "default_reasoning_level": reasoning_effort(model_id, template),
            "visibility": "list",
            "supported_in_api": True,
            "priority": priority,
            "additional_speed_tiers": [],
            "service_tiers": [],
            "default_service_tier": None,
            "availability_nux": None,
            "upgrade": None,
            "context_window": size,
            "max_context_window": size,
            "auto_compact_token_limit": max(1, int(size * 0.85)),
            "effective_context_window_percent": 95,
            "input_modalities": ["text", "image"] if caps.get("vision") else ["text"],
            "supports_search_tool": False,
            "use_responses_lite": False,
        }
    )
    return entry


def render_catalog(models: list[dict], template: dict) -> dict:
    return {
        "models": [
            catalog_entry(model, template, priority)
            for priority, model in enumerate(models, start=1)
        ]
    }


def preferred_model(models: list[dict]) -> str:
    ids = [str(model["id"]) for model in models]
    exact_preferences = (
        "cx/gpt-5.6-sol",
        "cxa/gpt-5.6-sol",
        "kmc/k3",
        "kmc/kimi-for-coding",
    )
    for preferred in exact_preferences:
        if preferred in ids:
            return preferred
    plain = [model_id for model_id in ids if not model_id.lower().endswith(tuple(f"-{x}" for x in EFFORT_SUFFIXES))]
    return (plain or ids)[0]


def render_profile(base_url: str, catalog: pathlib.Path, default_model: str) -> str:
    return "\n".join(
        [
            f"# generated by omniroute-config; selected with: codex --profile {PROFILE_NAME}",
            f"model = {json.dumps(default_model)}",
            f'model_provider = "{PROVIDER_ID}"',
            f"model_catalog_json = {json.dumps(str(catalog))}",
            "",
            f"[model_providers.{PROVIDER_ID}]",
            'name = "OmniRoute"',
            f'base_url = "{base_url}/v1"',
            'env_key = "OMNIROUTE_API_KEY"',
            'wire_api = "responses"',
            "requires_openai_auth = false",
            "",
        ]
    )


def strip_provider(existing: str) -> str:
    body = re.sub(rf"(?m)^{re.escape(BEGIN)}\n?", "", existing)
    body = re.sub(rf"(?m)^{re.escape(END)}\n?", "", body)
    return re.sub(
        rf"(?ms)^\[model_providers\.{PROVIDER_ID}\]\n.*?(?=^\[|\Z)",
        "",
        body,
    )


def clean_base_config(existing: str) -> str:
    """Drop the gateway defaults older versions spliced into the base config.

    A native Codex model id carries no slash and every gateway slug does, so a
    default hand-picked for plain ``codex`` survives this migration.
    """
    body = strip_provider(existing)
    body = re.sub(rf'(?m)^model_provider\s*=\s*"{PROVIDER_ID}"\s*\n?', "", body)
    body = re.sub(rf"(?m)^model_catalog_json\s*=.*{CATALOG_NAME}.*\n?", "", body)
    body = re.sub(r'(?m)^model\s*=\s*"[^"]*/[^"]*"\s*\n?', "", body)
    return re.sub(r"\n{3,}", "\n\n", body).lstrip("\n")


def is_legacy_generated_profile(path: pathlib.Path) -> bool:
    text = path.read_text()
    return text.startswith("# codex --profile ") and 'model_provider                 = "omniroute"' in text


def remove_legacy_profiles() -> int:
    removed = 0
    for path in CODEX_HOME.glob("*.config.toml"):
        if path.name != PROFILE_FILE and is_legacy_generated_profile(path):
            path.unlink()
            removed += 1
    return removed


def test_template() -> dict:
    return {
        "slug": "gpt-test",
        "display_name": "GPT Test",
        "description": "test",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "low"},
            {"effort": "medium", "description": "medium"},
            {"effort": "high", "description": "high"},
        ],
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 1,
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": "You are a coding agent.",
        "include_skills_usage_instructions": False,
        "default_reasoning_summary": "none",
        "support_verbosity": True,
        "default_verbosity": "low",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "truncation_policy": {"mode": "tokens", "limit": 10_000},
        "supports_image_detail_original": True,
        "context_window": 100_000,
        "max_context_window": 100_000,
        "comp_hash": "test",
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        "tool_mode": "code_mode",
        "multi_agent_version": "v1",
    }


def self_check() -> int:
    payload = {
        "data": [
            {"id": "cx/gpt", "context_length": 400_000, "capabilities": {"vision": True}},
            {"id": "kmc/k3-high", "context_length": 1_048_576, "capabilities": {}},
            {"id": "claude/cx/gpt", "context_length": 400_000, "capabilities": {}},
            {"id": "image/model", "context_length": 10_000, "type": "image"},
        ]
    }
    models = selected_models(payload)
    catalog = render_catalog(models, test_template())
    assert [model["slug"] for model in catalog["models"]] == ["cx/gpt", "kmc/k3-high"]
    assert catalog["models"][0]["context_window"] == 400_000
    assert catalog["models"][0]["auto_compact_token_limit"] == 340_000
    assert catalog["models"][0]["input_modalities"] == ["text", "image"]
    assert catalog["models"][1]["default_reasoning_level"] == "high"
    spliced = (
        'model = "cx/gpt"\nmodel_provider = "omniroute"\n'
        f'model_catalog_json = "/x/{CATALOG_NAME}"\n'
        f"{BEGIN}\n[model_providers.omniroute]\nbase_url = \"https://x/v1\"\n{END}\n"
        '[projects."/w"]\ntrust_level = "trusted"\n'
    )
    assert clean_base_config(spliced) == '[projects."/w"]\ntrust_level = "trusted"\n'
    assert clean_base_config('model = "gpt-5.6-sol"\n') == 'model = "gpt-5.6-sol"\n'
    profile = render_profile("https://x", pathlib.Path("/tmp/models.json"), "cx/gpt")
    assert '\nmodel = "cx/gpt"\nmodel_provider = "omniroute"\n' in profile
    assert 'model_catalog_json = "/tmp/models.json"' in profile
    assert profile.count("[model_providers.omniroute]") == 1
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
    if not models:
        sys.exit("the gateway returned no Codex-compatible text models")

    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    catalog_path = CODEX_HOME / CATALOG_NAME
    catalog_path.write_text(json.dumps(render_catalog(models, bundled_template()), indent=2) + "\n")
    catalog_path.chmod(0o600)

    profile_path = CODEX_HOME / PROFILE_FILE
    profile_path.write_text(render_profile(base, catalog_path, preferred_model(models)))
    profile_path.chmod(0o600)

    config = CODEX_HOME / "config.toml"
    if config.exists():
        existing = config.read_text()
        updated = clean_base_config(existing)
        if existing != updated:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = config.with_suffix(f".toml.bak-{stamp}")
            backup.write_text(existing)
            print(f"  backed up {config.name} -> {backup.name}")
            print(f"  removed the gateway defaults from {config.name}")
            config.write_text(updated)
            config.chmod(0o600)
    removed = remove_legacy_profiles()
    print(f"\n{len(models)} native Codex models written to {catalog_path}")
    print(f"  selected by {profile_path.name}; plain codex keeps its own models")
    if removed:
        print(f"  removed {removed} obsolete generated profile files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
