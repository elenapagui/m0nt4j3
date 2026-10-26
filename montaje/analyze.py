"""Análisis de la transcripción: frases, muletillas y tomas repetidas.

El resultado es una *propuesta*. Claude (o tú) la revisa en plan.json antes de montar.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .util import normalize

# Muletillas «vocales» que se pueden cortar sin cambiar el sentido.
FILLER_RE = re.compile(r"^(e+h+m*|e+m+|m{2,}|h+m+|a+h{2,}|u+h+|u+m+|e{2,})$")
# Expresiones que a veces son muletilla y a veces no: se señalan, no se cortan.
MAYBE_FILLERS = {"este", "o sea", "bueno", "pues", "vale", "en plan", "tipo", "sabes", "a ver", "digamos", "ah"}
# Frases con las que solemos marcar que repetimos una toma.
RETAKE_CUES = ["otra vez", "vuelvo a empezar", "lo repito", "repito", "desde el principio", "me he equivocado",
               "me he liado", "corta", "empiezo de nuevo", "de nuevo", "perdon", "espera"]

END_PUNCT = (".", "?", "!", "…", "?»", "!»")


def is_filler(word: str) -> bool:
    return bool(FILLER_RE.match(normalize(word)))


def build_phrases(words: list[dict], max_gap: float = 0.7, max_words: int = 28) -> list[dict]:
    phrases: list[dict] = []
    start = 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        n = i - start + 1
        gap = words[i + 1]["s"] - w["e"] if not last else 0
        text = w["w"].strip()
        brk = (
            last
            or text.endswith(END_PUNCT)
            or gap > max_gap
            or n >= max_words
            or (n >= 18 and text.endswith((",", ";", ":")))
        )
        if brk:
            seg = words[start:i + 1]
            phrases.append({
                "id": f"F{len(phrases) + 1:03d}",
                "w": [start, i + 1],
                "start": round(seg[0]["s"], 2),
                "end": round(seg[-1]["e"], 2),
                "text": join_words(seg),
                "keep": True,
            })
            start = i + 1
    return phrases


def join_words(ws: list[dict]) -> str:
    return " ".join(w["w"].strip() for w in ws).replace(" ,", ",").replace(" .", ".")


def _tokens(words: list[dict], ph: dict, cut: set[int]) -> list[str]:
    a, b = ph["w"]
    return [t for i in range(a, b) if i not in cut and (t := normalize(words[i]["w"]))]


def find_fillers(words: list[dict]) -> tuple[list[int], dict[str, int]]:
    """Índices de muletillas seguras y recuento de posibles muletillas."""
    cut = [i for i, w in enumerate(words) if is_filler(w["w"])]
    maybe: dict[str, int] = {}
    norm = [normalize(w["w"]) for w in words]
    for i in range(len(norm)):
        for cand in MAYBE_FILLERS:
            parts = cand.split()
            if norm[i:i + len(parts)] == parts:
                maybe[cand] = maybe.get(cand, 0) + 1
    return cut, dict(sorted(maybe.items(), key=lambda kv: -kv[1]))


def find_retakes(words: list[dict], phrases: list[dict], cut: set[int], lookahead: int = 3) -> None:
    """Marca keep=False en las frases que parecen tomas fallidas (se queda la última)."""
    toks = [_tokens(words, ph, cut) for ph in phrases]
    for i, ph in enumerate(phrases):
        t = toks[i]
        if not t:
            ph["keep"] = False
            ph["reason"] = "solo muletillas"
            continue
        joined = " ".join(t)
        # 1) La propia frase es un comentario de toma («perdón, otra vez»)
        if len(t) <= 6 and any(re.search(rf"\b{cue}\b", joined) for cue in RETAKE_CUES):
            ph["keep"] = False
            ph["reason"] = "comentario de toma"
            if i > 0 and phrases[i - 1]["keep"]:
                phrases[i - 1]["keep"] = False
                phrases[i - 1]["reason"] = f"toma descartada (antes de «{ph['text'][:30]}»)"
            continue
        # 2) La frase se repite poco después: nos quedamos con la última versión
        for j in range(i + 1, min(i + 1 + lookahead, len(phrases))):
            u = toks[j]
            if len(t) < 3 or not u:
                if len(t) < 3 and u and t == u[:len(t)] and not ph["text"].rstrip().endswith(END_PUNCT):
                    ph["keep"] = False
                    ph["reason"] = f"arranque en falso (se repite en {phrases[j]['id']})"
                    break
                continue
            prefix = u[:len(t)]
            ratio = SequenceMatcher(None, t, prefix).ratio()
            # Más exigente cuanto más lejos: «El primer motivo… / El segundo motivo…» NO es una repetición
            needed = 0.72 if j == i + 1 else 0.85
            if ratio >= needed and abs(len(prefix) - len(t)) <= max(2, len(t) // 3):
                ph["keep"] = False
                ph["reason"] = f"toma repetida (mejor versión en {phrases[j]['id']})"
                break
            # frase cortada a medias que vuelve a empezar igual
            if len(t) >= 3 and t[:3] == u[:3] and len(t) < len(u) and not ph["text"].rstrip().endswith(END_PUNCT):
                ph["keep"] = False
                ph["reason"] = f"frase a medias (se repite en {phrases[j]['id']})"
                break


def analyze(words: list[dict]) -> dict:
    phrases = build_phrases(words)
    cut, maybe = find_fillers(words)
    find_retakes(words, phrases, set(cut))
    return {"phrases": phrases, "cut_words": cut, "maybe_fillers": maybe}
