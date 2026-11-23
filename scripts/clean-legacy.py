#!/usr/bin/env python3
"""Remove what earlier installers wrote into the plain clients' own config.

Before the or-* launchers, the gateway was spliced into ~/.claude, ~/.codex,
~/.kimi-code, ~/.pi/agent and every ~/.workenv/<identity>/<client>, so plain
``claude``, ``kimi`` or ``pi`` silently started on OmniRoute. This deletes only
what carries OmniRoute's fingerprint and backs every changed file up first.

    clean-legacy.py [--dry-run] [DIR ...]     default: every known plain dir
"""
import datetime
import importlib.util
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path.home()
WORKENV_HOME = pathlib.Path(os.environ.get("WORKENV_HOME", HOME / ".workenv"))
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_gateway_id(model: str) -> bool:
    return "/" in model


def clean_claude(text: str) -> str:
    data = json.loads(text)
    env = dict(data.get("env") or {})
    if "omniroute" in str(env.get("ANTHROPIC_BASE_URL", "")).lower():
        env.pop("ANTHROPIC_BASE_URL")
        env.pop("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", None)
    if env:
        data["env"] = env
    else:
        data.pop("env", None)
    options = (data.get("modelPicker") or {}).get("options") or []
    if options and all("OmniRoute" in str(option.get("description", "")) for option in options):
        data.pop("modelPicker")
    if is_gateway_id(str(data.get("model", ""))):
        data.pop("model")
    return json.dumps(data, indent=2) + "\n"


def clean_kimi(text: str) -> str:
    kimi = sibling("fix-kimi")
    start = text.find(kimi.BEGIN)
    if start != -1:
        end = text.find(kimi.END, start)
        text = text[:start] + ("" if end == -1 else text[end + len(kimi.END):].lstrip("\n"))
    text = re.sub(r'(?m)^default_model\s*=\s*"omniroute/[^"]*"\s*\n?', "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return text + "\n" if text else ""


def clean_pi_models(text: str) -> str:
    data = json.loads(text)
    providers = dict(data.get("providers") or {})
    providers.pop("omniroute", None)
    data["providers"] = providers
    return json.dumps(data, indent=2) + "\n"


def clean_pi_settings(text: str) -> str:
    data = json.loads(text)
    if data.get("defaultProvider") == "omniroute":
        data.pop("defaultProvider")
        data.pop("defaultModel", None)
    return json.dumps(data, indent=2) + "\n"


CLEANERS = {
    "claude": {"settings.json": clean_claude},
    "codex": {"config.toml": lambda text: sibling("fix-codex").clean_base_config(text)},
    "kimi": {"config.toml": clean_kimi},
    "pi": {"models.json": clean_pi_models, "settings.json": clean_pi_settings},
}
GENERATED = {"codex": ("omniroute.config.toml", "omniroute-models.json")}


def known_dirs() -> list[tuple[str, pathlib.Path]]:
    dirs = [("claude", HOME / ".claude"), ("codex", HOME / ".codex"),
            ("kimi", HOME / ".kimi-code"), ("pi", HOME / ".pi/agent")]
    for identity in sorted(WORKENV_HOME.glob("*/")):
        dirs += [(client, identity / client) for client in CLEANERS]
    return [(client, path) for client, path in dirs if path.is_dir()]


def client_of(path: pathlib.Path) -> str:
    for client in CLEANERS:
        if path.name == client or (client == "kimi" and path.name == ".kimi-code") \
                or (client == "pi" and path.parts[-2:] == (".pi", "agent")) \
                or (client == path.name.lstrip(".")):
            return client
    sys.exit(f"cannot tell which client owns {path}")


def clean_dir(client: str, path: pathlib.Path, dry_run: bool) -> list[str]:
    changed = []
    for name, cleaner in CLEANERS[client].items():
        config = path / name
        if not config.is_file():
            continue
        before = config.read_text()
        try:
            after = cleaner(before)
        except ValueError as error:
            changed.append(f"skipped {config}: {error}")
            continue
        if name.endswith(".json") and json.loads(after) == json.loads(before):
            continue
        if after != before:
            changed.append(f"cleaned {config}")
            if not dry_run:
                config.with_name(f"{name}.bak-omniroute-{STAMP}").write_text(before)
                config.write_text(after)
    for name in GENERATED.get(client, ()):
        generated = path / name
        if generated.is_file():
            changed.append(f"removed {generated}")
            if not dry_run:
                generated.unlink()
    return changed


def self_check() -> int:
    claude = json.dumps({"theme": "dark", "model": "cc/claude-opus-5[1m]",
                         "env": {"ANTHROPIC_BASE_URL": "https://omniroute.x", "KEEP": "1",
                                 "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"},
                         "modelPicker": {"options": [{"model": "a", "description": "a through OmniRoute; x"}]}})
    assert json.loads(clean_claude(claude)) == {"theme": "dark", "env": {"KEEP": "1"}}
    untouched = json.dumps({"model": "opus", "env": {"ANTHROPIC_BASE_URL": "https://corp.example"}}, indent=2) + "\n"
    assert clean_claude(untouched) == untouched

    kimi = sibling("fix-kimi")
    original = '[providers."managed:kimi-code"]\ntype = "kimi"\n'
    spliced = 'default_model = "omniroute/kmc-k3"\n\n' + original + "\n" + kimi.BEGIN + "\nx\n" + kimi.END + "\n"
    assert clean_kimi(spliced) == original
    assert clean_kimi('default_model = "kimi-code/k3"\n') == 'default_model = "kimi-code/k3"\n'

    pi = json.dumps({"providers": {"omniroute": {}, "ollama": {"x": 1}}})
    assert json.loads(clean_pi_models(pi)) == {"providers": {"ollama": {"x": 1}}}
    assert json.loads(clean_pi_settings('{"defaultProvider": "omniroute", "defaultModel": "m", "t": 1}')) == {"t": 1}
    assert client_of(pathlib.Path("/h/.kimi-code")) == "kimi"
    assert client_of(pathlib.Path("/h/.pi/agent")) == "pi"
    assert client_of(pathlib.Path("/h/.workenv/azra/codex")) == "codex"
    print("self-check ok")
    return 0


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()
    dry_run = "--dry-run" in sys.argv
    paths = [pathlib.Path(arg).expanduser() for arg in sys.argv[1:] if not arg.startswith("--")]
    targets = [(client_of(path), path) for path in paths] if paths else known_dirs()
    report = [line for client, path in targets for line in clean_dir(client, path, dry_run)]
    for line in report:
        print(f"  {'would be ' if dry_run else ''}{line}")
    print(f"{len(report)} legacy OmniRoute entries {'found' if dry_run else 'removed'} from plain client dirs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
