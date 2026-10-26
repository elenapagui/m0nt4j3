"""Efectos de sonido. Se buscan en la carpeta `sonidos/`; si falta alguno básico, se genera.

Los sonidos generados son sencillos y libres de derechos. Puedes sustituirlos por los tuyos
dejando en `sonidos/` un archivo con el mismo nombre (whoosh.wav, pop.mp3, …).
"""

from __future__ import annotations

import os
from pathlib import Path

from .media import AUDIO_EXT
from .util import MontajeError, run

SOUNDS_DIR = Path(os.environ.get("MONTAJE_SONIDOS", Path(__file__).resolve().parent.parent / "sonidos"))

# Recetas de ffmpeg (lavfi) para los sonidos básicos
RECIPES = {
    "whoosh": ("anoisesrc=d=0.7:c=pink:a=0.5", "highpass=f=300,lowpass=f=5000,afade=t=in:d=0.35:curve=exp,"
               "afade=t=out:st=0.35:d=0.35,volume=0.9"),
    "pop": ("aevalsrc='0.8*sin(2*PI*(380+900*t)*t)*exp(-28*t)':d=0.18:s=48000", "afade=t=out:st=0.12:d=0.06"),
    "click": ("aevalsrc='0.7*sin(2*PI*2200*t)*exp(-120*t)':d=0.05:s=48000", "anull"),
    "ding": ("aevalsrc='0.5*(sin(2*PI*1320*t)+0.4*sin(2*PI*2640*t))*exp(-5*t)':d=1.2:s=48000", "afade=t=out:st=0.9:d=0.3"),
    "impacto": ("aevalsrc='0.9*sin(2*PI*(90-40*t)*t)*exp(-7*t)':d=0.6:s=48000", "lowpass=f=900,afade=t=out:st=0.4:d=0.2"),
}


def find_sound(name: str) -> Path:
    p = Path(name).expanduser()
    if p.exists():
        return p.resolve()
    for ext in sorted(AUDIO_EXT):
        cand = SOUNDS_DIR / f"{name}{ext}"
        if cand.exists():
            return cand.resolve()
    if name in RECIPES:
        return generate(name)
    available = sorted({f.stem for f in SOUNDS_DIR.glob("*") if f.suffix.lower() in AUDIO_EXT} | set(RECIPES))
    raise MontajeError(f"No encuentro el sonido «{name}». Disponibles: {', '.join(available)}")


def generate(name: str, force: bool = False) -> Path:
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    out = SOUNDS_DIR / f"{name}.wav"
    if out.exists() and not force:
        return out.resolve()
    src, filt = RECIPES[name]
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-af", filt, "-ar", "48000", "-ac", "2", str(out)])
    return out.resolve()


def generate_all(force: bool = False) -> list[Path]:
    return [generate(n, force) for n in RECIPES]
