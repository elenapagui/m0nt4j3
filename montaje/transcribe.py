"""Transcripción local con marcas de tiempo por palabra.

En un Mac con Apple Silicon usa mlx-whisper (rápido, usa la GPU del chip).
En cualquier otro equipo usa faster-whisper. Todo se ejecuta en el ordenador:
el audio no se sube a ningún sitio.
"""

from __future__ import annotations

import platform
from pathlib import Path

from .util import MontajeError, info, read_json

# Escribir muletillas en el prompt inicial anima a Whisper a transcribirlas
# en lugar de «limpiarlas», y así podemos detectarlas y cortarlas.
PROMPT_ES = "Eh, bueno, em... o sea, este, vale. Mmm, pues nada, ¿vale? Eh, a ver."

MLX_MODELS = {
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
}
FW_MODELS = {"turbo": "large-v3-turbo", "large": "large-v3", "medium": "medium", "small": "small"}


def available_backend() -> str | None:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx_whisper  # noqa: F401
            return "mlx"
        except ImportError:
            pass
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except ImportError:
        return None


def transcribe(audio: Path, language: str = "es", model: str = "turbo", backend: str | None = None) -> dict:
    backend = backend or available_backend()
    if backend is None:
        raise MontajeError(
            "No hay ningún motor de transcripción instalado. Ejecuta ./instalar.sh "
            "(o `pip install mlx-whisper` en Mac con Apple Silicon / `pip install faster-whisper`)."
        )
    prompt = PROMPT_ES if language == "es" else None
    info(f"Transcribiendo con {backend} (modelo {model}). En un vídeo largo puede tardar unos minutos…")
    if backend == "mlx":
        import mlx_whisper

        res = mlx_whisper.transcribe(
            str(audio), path_or_hf_repo=MLX_MODELS.get(model, model), language=language,
            word_timestamps=True, initial_prompt=prompt, condition_on_previous_text=False, verbose=None,
        )
        raw = [w for seg in res.get("segments", []) for w in seg.get("words", [])]
        words = [{"w": w["word"], "s": w["start"], "e": w["end"], "p": w.get("probability", 1.0)} for w in raw]
    elif backend == "faster-whisper":
        from faster_whisper import WhisperModel

        fw = WhisperModel(FW_MODELS.get(model, model), device="auto", compute_type="int8")
        # le pasamos el audio ya decodificado: evita depender de PyAV (hay versiones incompatibles)
        segments, _ = fw.transcribe(
            read_wav(audio), language=language, word_timestamps=True, initial_prompt=prompt,
            condition_on_previous_text=False, vad_filter=False,
        )
        words = []
        for seg in segments:
            for w in seg.words or []:
                words.append({"w": w.word, "s": w.start, "e": w.end, "p": w.probability})
    else:
        raise MontajeError(f"Motor de transcripción desconocido: {backend}")
    return {"backend": backend, "model": model, "language": language, "words": clean_words(words)}


def read_wav(path: Path):
    """WAV PCM 16 bits mono 16 kHz (el que crea media.extract_audio) → numpy float32."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise MontajeError(f"{path} no es WAV mono de 16 kHz y 16 bits")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def load_words_file(path: Path) -> list[dict]:
    """Acepta JSON de Whisper (segments[].words[]), WhisperX (word_segments) o una lista simple."""
    data = read_json(path)
    if isinstance(data, dict) and "words" in data and isinstance(data["words"], list):
        raw = data["words"]
    elif isinstance(data, dict) and "segments" in data:
        raw = [w for seg in data["segments"] for w in seg.get("words", [])]
    elif isinstance(data, dict) and "word_segments" in data:
        raw = data["word_segments"]
    elif isinstance(data, list):
        raw = data
    else:
        raise MontajeError(f"No reconozco el formato de {path}")
    words = []
    for w in raw:
        text = w.get("w") or w.get("word") or w.get("text") or ""
        s, e = w.get("s", w.get("start")), w.get("e", w.get("end"))
        if s is None or e is None:
            continue
        words.append({"w": text, "s": float(s), "e": float(e), "p": float(w.get("p", w.get("probability", 1.0)))})
    return clean_words(words)


def clean_words(words: list[dict]) -> list[dict]:
    """Ordena, quita vacíos y corrige solapes o duraciones imposibles."""
    out = []
    for w in sorted(words, key=lambda x: x["s"]):
        text = (w["w"] or "").strip()
        if not text:
            continue
        s, e = round(float(w["s"]), 3), round(float(w["e"]), 3)
        if out and s < out[-1]["e"]:
            s = out[-1]["e"]
        if e <= s:
            e = s + 0.05
        # Whisper a veces estira una palabra sobre un silencio largo
        if e - s > 2.5:
            e = s + 1.2 if len(text) < 12 else s + 2.0
        out.append({"w": text, "s": s, "e": round(e, 3), "p": round(float(w.get("p", 1.0)), 3)})
    return out
