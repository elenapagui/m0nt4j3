"""Utilidades comunes: tiempos, ids, rutas y mensajes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable

US = 1_000_000  # CapCut guarda los tiempos en microsegundos


class MontajeError(Exception):
    """Error con un mensaje pensado para quien usa la herramienta (en español)."""


def us(seconds: float) -> int:
    return int(round(seconds * US))


def sec(microseconds: int) -> float:
    return microseconds / US


def new_id() -> str:
    """Id con el formato que usa CapCut (UUID en mayúsculas)."""
    return str(uuid.uuid4()).upper()


def new_local_id() -> str:
    """Los ids de draft_meta_info.json van en minúsculas."""
    return str(uuid.uuid4())


def utf16_len(text: str) -> int:
    """CapCut mide los rangos de estilo de texto en unidades UTF-16."""
    return len(text.encode("utf-16-le")) // 2


def fmt_time(seconds: float, millis: bool = True) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    if millis:
        return f"{h:02d}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}"
    s = int(s)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def normalize(text: str) -> str:
    """Minúsculas, sin tildes ni signos: para comparar palabras."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", "", text).strip()


def slugify(text: str) -> str:
    text = normalize(text)
    return re.sub(r"\s+", "-", text)[:60] or "video"


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: Any, indent: int | None = 2) -> None:
    """Escritura atómica: primero a un temporal y luego se renombra."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".montaje-tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def which(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=capture, text=True, check=check)
    except FileNotFoundError as e:
        raise MontajeError(f"No encuentro el programa «{cmd[0]}». Ejecuta `montaje doctor`.") from e
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or "").strip().splitlines()[-5:]
        raise MontajeError(f"Falló «{' '.join(cmd[:3])} …»:\n" + "\n".join(tail)) from e


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def warn(msg: str) -> None:
    print(f"⚠️  {msg}", file=sys.stderr)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def merge_intervals(intervals: Iterable[tuple[float, float]], gap: float = 0.0) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def deep_update(base: dict, extra: dict) -> dict:
    """Mezcla `extra` sobre `base` de forma recursiva (devuelve `base`)."""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base


def hex_to_rgb(color: str) -> list[float]:
    h = color.lstrip("#")
    if len(h) != 6:
        raise MontajeError(f"Color no válido: {color} (usa el formato #RRGGBB)")
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)]
