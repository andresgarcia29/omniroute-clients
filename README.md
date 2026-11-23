# Clientes de OmniRoute para macOS

Conecta Claude Code, Codex, Kimi, Pi y OpenCode a tu gateway de OmniRoute. Cada cliente usa una configuración independiente en tu Mac.

## Instalación

Necesitas macOS, Python 3, curl y al menos uno de los clientes instalado. Por ejemplo, si usas Homebrew, puedes instalar Claude Code con `brew install --cask claude-code`.

Ejecuta en Terminal:

```bash
curl -fsSL https://router-cli.the-harness.io -o /tmp/install-omniroute.sh && bash /tmp/install-omniroute.sh
```

Introduce tu API key de OmniRoute cuando te la pida. El instalador verifica la clave en `https://router.the-harness.io/v1/models` y la guarda en `~/.omniroute/.env` con permisos de lectura y escritura para tu usuario.

Abre `or-claude`, `or-codex`, `or-kimi`, `or-pi` u `or-opencode`. Se instala el lanzador de cada cliente que encuentre en tu PATH. Si instalas otro cliente después, vuelve a ejecutar el instalador. Si tu terminal no encuentra los lanzadores, añade `export PATH="$HOME/.local/bin:$PATH"` a `~/.zshrc` y abre otra terminal.
