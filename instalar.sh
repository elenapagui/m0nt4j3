#!/usr/bin/env bash
# Instalación de «montaje» en macOS (una sola vez, ~10-30 minutos según la conexión).
# Uso: abre Terminal en esta carpeta y ejecuta:  ./instalar.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Este instalador es para macOS. En otros sistemas: python3 -m venv .venv && .venv/bin/pip install -e '.[whisper]'"
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  cat <<'MSG'
Falta Homebrew (el instalador de programas para Mac). Instálalo pegando esto en Terminal:

  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Sigue las instrucciones que aparecen al final (las líneas «Next steps») y vuelve a ejecutar ./instalar.sh
MSG
  exit 1
fi

say "Instalando ffmpeg y Python (si no están)…"
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
PY="$(brew --prefix)/bin/python3.12"

say "Creando el entorno de Python en .venv…"
[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[mac,dev]"

say "Generando los efectos de sonido básicos…"
./montaje.sh sonidos >/dev/null

say "Comprobación final:"
./montaje.sh doctor

cat <<'MSG'

✅ Listo. La primera transcripción descargará el modelo de Whisper (~1,5 GB, solo una vez).

Siguiente paso: abre esta carpeta con Claude Code (app de Claude → Code → elegir carpeta,
o en Terminal: `claude`) y dile por ejemplo:
    «Edita ~/Movies/grabaciones/mi-opinion.mov con estilo opinión. El proyecto de CapCut se llama Mi opinión.»
MSG
