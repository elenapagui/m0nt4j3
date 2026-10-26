#!/usr/bin/env bash
# Lanzador: usa el entorno de .venv creado por instalar.sh
DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -x "$DIR/.venv/bin/python" ]]; then
  exec "$DIR/.venv/bin/python" -m montaje "$@"
fi
cd "$DIR" && exec python3 -m montaje "$@"
