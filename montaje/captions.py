"""Subtítulos: agrupa las palabras en bloques legibles y marca las palabras clave."""

from __future__ import annotations

import re

from .timeline import Timeline
from .util import fmt_srt_time, normalize

NUM_RE = re.compile(r"\d")
# Un subtítulo no debería terminar en una de estas palabras: pasan al siguiente
WEAK_END = {"a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los", "mi", "mis", "o", "para", "pero",
            "por", "que", "se", "si", "su", "sus", "tu", "tus", "un", "una", "unos", "unas", "y", "e", "ni", "como",
            "muy", "mas", "sin", "sobre", "entre", "hasta", "desde", "le", "les", "me", "te", "nos", "es"}


def caption_chunks(tl: Timeline, cfg: dict) -> list[dict]:
    max_chars = int(cfg.get("max_chars", 42))
    max_dur = float(cfg.get("max_dur", 4.5))
    gap_break = float(cfg.get("gap_break", 0.6))
    words = tl.words
    chunks: list[dict] = []
    cur: list[tuple[int, float, float, int]] = []

    def flush(carry: bool = False):
        moved = []
        if carry:  # no dejar «en», «de», «la»… colgando al final
            while len(cur) > 2 and normalize(words[cur[-1][0]]["w"]) in WEAK_END:
                moved.insert(0, cur.pop())
        if cur:
            chunks.append({"words": [c[0] for c in cur], "start": cur[0][1], "end": cur[-1][2],
                           "section": tl.clips[cur[0][3]].section})
            cur.clear()
        cur.extend(moved)

    for item in tl.timeline_words():
        wi, s, e, ci = item
        if cur:
            text_len = sum(len(words[c[0]]["w"].strip()) + 1 for c in cur) + len(words[wi]["w"].strip())
            prev = cur[-1]
            if s - prev[2] > gap_break or tl.clips[ci].section != tl.clips[prev[3]].section:
                flush()
            elif text_len > max_chars or e - cur[0][1] > max_dur:
                flush(carry=True)
        cur.append(item)
        t = words[wi]["w"].strip()
        n = sum(len(words[c[0]]["w"].strip()) + 1 for c in cur)
        if t.endswith((".", "?", "!", "…")) or (t.endswith((",", ";", ":")) and n > max_chars * 0.6):
            flush()
    flush()

    # una palabra suelta («vida», «conmigo») se une al subtítulo anterior si cabe con poco exceso
    merged: list[dict] = []
    for ch in chunks:
        prev = merged[-1] if merged else None
        if (prev and len(ch["words"]) == 1 and ch["section"] == prev["section"] and ch["start"] - prev["end"] < 0.3
                and sum(len(words[w]["w"]) + 1 for w in prev["words"] + ch["words"]) <= max_chars + 10):
            prev["words"] += ch["words"]
            prev["end"] = ch["end"]
        else:
            merged.append(ch)
    chunks = merged

    min_dur = float(cfg.get("min_dur", 0.8))
    for i, ch in enumerate(chunks):
        nxt = chunks[i + 1]["start"] if i + 1 < len(chunks) else tl.duration
        end = ch["end"] + 0.12
        if nxt - end < 0.35:  # sin parpadeos entre subtítulos seguidos
            end = nxt
        if end - ch["start"] < min_dur:
            end = min(nxt, ch["start"] + min_dur)
        ch["end"] = round(min(end, nxt, tl.duration), 3)
        ch["start"] = round(ch["start"], 3)
        ch["text"] = format_text([words[w]["w"] for w in ch["words"]], cfg)
    return [c for c in chunks if c["end"] - c["start"] > 0.1 and c["text"]]


def format_text(tokens: list[str], cfg: dict) -> str:
    toks = [t.strip() for t in tokens]
    if not cfg.get("punctuation", False):
        toks = [re.sub(r"[.,;:…]+$", "", t) for t in toks]
    text = " ".join(t for t in toks if t)
    if cfg.get("uppercase"):
        text = text.upper()
    return text


def highlight_spans(text: str, highlights: list[str], numbers: bool) -> list[tuple[int, int]]:
    """Rangos (en caracteres) del texto que van resaltados."""
    spans = []
    norm_hl = [normalize(h) for h in highlights if normalize(h)]
    for m in re.finditer(r"\S+", text):
        tok = m.group(0)
        n = normalize(tok)
        if (numbers and NUM_RE.search(tok)) or any(n == h for h in norm_hl if " " not in h):
            spans.append((m.start(), m.end()))
    low = normalize_keep_len(text)
    for h in norm_hl:
        if " " in h:
            for m in re.finditer(re.escape(h), low):
                spans.append((m.start(), m.end()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


def normalize_keep_len(text: str) -> str:
    """Como normalize() pero conservando la longitud, para buscar posiciones."""
    import unicodedata

    out = []
    for ch in text.lower():
        base = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in base if not unicodedata.combining(c)) or ch
        out.append(base[0] if base[0].isalnum() or base[0].isspace() else " ")
    return "".join(out)


def to_srt(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        lines += [str(i), f"{fmt_srt_time(c['start'])} --> {fmt_srt_time(c['end'])}", c["text"], ""]
    return "\n".join(lines)
