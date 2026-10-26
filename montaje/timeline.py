"""Convierte un plan (frases que se quedan) en la lista de clips de la línea de tiempo.

Todos los tiempos del plan están en segundos del vídeo ORIGINAL. Aquí se calcula en qué
momento de la línea de tiempo final cae cada cosa.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from .util import merge_intervals


@dataclass
class Clip:
    src_start: float
    src_end: float
    tl_start: float = 0.0
    section: str = "main"  # "hook" (gancho al principio) o "main"
    words: list[int] = field(default_factory=list)
    zoom: float = 1.0  # zoom estático (1.0 = encuadre original)
    push: tuple[float, float] | None = None  # zoom animado (inicio, fin)

    @property
    def dur(self) -> float:
        return self.src_end - self.src_start

    @property
    def tl_end(self) -> float:
        return self.tl_start + self.dur


@dataclass
class Timeline:
    clips: list[Clip]
    words: list[dict]

    @property
    def duration(self) -> float:
        return self.clips[-1].tl_end if self.clips else 0.0

    def main_clips(self) -> list[Clip]:
        return [c for c in self.clips if c.section == "main"]

    def map_src(self, t: float) -> float:
        """Momento de la línea de tiempo en el que se ve el instante `t` del original."""
        main = self.main_clips()
        if not main:
            return 0.0
        starts = [c.src_start for c in main]
        k = bisect_right(starts, t) - 1
        if k >= 0 and t < main[k].src_end:
            return main[k].tl_start + (t - main[k].src_start)
        if k + 1 < len(main):  # cae en un trozo cortado: vamos al siguiente clip
            return main[k + 1].tl_start
        return main[-1].tl_end

    def timeline_words(self) -> list[tuple[int, float, float, int]]:
        """(índice de palabra, inicio, fin, índice de clip) en tiempo de la línea final."""
        out = []
        for ci, c in enumerate(self.clips):
            for wi in c.words:
                w = self.words[wi]
                s = c.tl_start + max(0.0, w["s"] - c.src_start)
                e = c.tl_start + min(c.dur, w["e"] - c.src_start)
                out.append((wi, s, max(e, s + 0.05), ci))
        return out


def _runs(words: list[dict], allowed: list[int], silences: list[tuple[float, float]], cfg: dict,
          total: float) -> list[tuple[float, float, list[int]]]:
    """Agrupa las palabras permitidas en tramos continuos de habla y les da aire."""
    if not allowed:
        return []
    min_pause = cfg.get("min_pause", 0.5)
    pad_b, pad_a = cfg.get("pad_before", 0.12), cfg.get("pad_after", 0.15)
    # Las marcas de tiempo de Whisper son aproximadas: si tenemos el mapa de silencios real del
    # audio, mandan los silencios (no se corta donde la voz sigue, y los bordes caen en silencio).
    use_sil = bool(silences) and cfg.get("snap_silence", True)

    def longest_silence(a: float, b: float) -> tuple[float, float] | None:
        best = None
        for s0, s1 in silences:
            if s1 > a and s0 < b and (best is None or s1 - s0 > best[1] - best[0]):
                best = (s0, s1)
        return best

    groups: list[list[int]] = [[allowed[0]]]
    for prev, cur in zip(allowed, allowed[1:]):
        if cur != prev + 1:  # hay palabras eliminadas en medio: corte obligado
            groups.append([cur])
            continue
        if use_sil:
            sil = longest_silence(words[prev]["e"] - 0.25, words[cur]["s"] + 0.25)
            pause = (sil[1] - sil[0]) if sil else 0.0
        else:
            pause = words[cur]["s"] - words[prev]["e"]
        if pause > min_pause:
            groups.append([cur])
        else:
            groups[-1].append(cur)

    runs = []
    for g in groups:
        first, last = g[0], g[-1]
        lo = words[first - 1]["e"] if first > 0 else 0.0  # no invadir la palabra anterior
        hi = words[last + 1]["s"] if last + 1 < len(words) else total
        start = words[first]["s"] - pad_b
        end = words[last]["e"] + pad_a
        if use_sil:
            before = [s for s in silences if words[first]["s"] - 0.6 <= s[1] <= words[first]["s"] + 0.25]
            if before:  # empezar justo antes de que acabe el silencio previo
                s0, s1 = max(before, key=lambda s: s[1])
                start = max(s0, s1 - pad_b)
            after = [s for s in silences if words[last]["e"] - 0.25 <= s[0] <= words[last]["e"] + 0.6]
            if after:  # terminar un poco después de que empiece el silencio siguiente
                s0, s1 = min(after, key=lambda s: s[0])
                end = min(s1, s0 + pad_a)
        start = max(start, lo, 0.0)
        end = min(end, hi, total)
        runs.append((round(start, 3), round(end, 3), g))
    # tramos que se tocan se fusionan solo si no hay una palabra eliminada entre ellos
    merged: list[tuple[float, float, list[int]]] = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < 0.06 and r[2][0] == merged[-1][2][-1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], r[1]), merged[-1][2] + r[2])
        elif merged and r[0] < merged[-1][1]:
            mid = (merged[-1][1] + r[0]) / 2
            merged[-1] = (merged[-1][0], mid, merged[-1][2])
            merged.append((mid, r[1], r[2]))
        else:
            merged.append(r)
    return merged


def kept_words(plan: dict, words: list[dict], phrase_ids: list[str] | None = None) -> list[int]:
    cut = set(plan.get("cut_words", []))
    removed = merge_intervals((float(a), float(b)) for a, b in plan.get("remove_ranges", []))
    out = []
    for ph in plan["phrases"]:
        if phrase_ids is None and not ph.get("keep", True):
            continue
        if phrase_ids is not None and ph["id"] not in phrase_ids:
            continue
        a, b = ph["w"]
        for i in range(a, b):
            if i in cut:
                continue
            w = words[i]
            if any(s < w["e"] and w["s"] < e for s, e in removed):
                continue
            out.append(i)
    return sorted(set(out))


def build_timeline(plan: dict, words: list[dict], silences: list[tuple[float, float]], cuts_cfg: dict,
                   total: float) -> Timeline:
    clips: list[Clip] = []
    hook = plan.get("hook") or {}
    if hook.get("phrases"):
        hook_cfg = dict(cuts_cfg, min_pause=min(cuts_cfg.get("min_pause", 0.5), 0.35))
        for s, e, g in _runs(words, kept_words(plan, words, hook["phrases"]), silences, hook_cfg, total):
            clips.append(Clip(s, e, section="hook", words=g))
    for s, e, g in _runs(words, kept_words(plan, words), silences, cuts_cfg, total):
        clips.append(Clip(s, e, section="main", words=g))
    t = 0.0
    for c in clips:
        c.tl_start = round(t, 6)
        t += c.dur
    return Timeline(clips=clips, words=words)


def apply_zooms(tl: Timeline, plan: dict, zoom_cfg: dict) -> None:
    """Zoom alterno entre cortes (oculta los saltos) + zooms de énfasis del plan."""
    mode = zoom_cfg.get("mode", "alternate")
    scale = float(zoom_cfg.get("scale", 1.12))
    min_interval = float(zoom_cfg.get("min_interval", 3.0))
    if mode == "alternate":
        level, since = 1.0, 0.0
        for i, c in enumerate(tl.clips):
            if i > 0:
                cut_is_real = abs(tl.clips[i - 1].src_end - c.src_start) > 0.05 or c.section != tl.clips[i - 1].section
                if cut_is_real and since >= min_interval:
                    level = scale if level == 1.0 else 1.0
                    since = 0.0
            c.zoom = level
            since += c.dur
    phrases = {p["id"]: p for p in plan["phrases"]}
    for z in plan.get("zooms", []):
        ph = phrases.get(z.get("phrase", ""))
        s = z.get("start", ph["start"] if ph else None)
        e = z.get("end", ph["end"] if ph else None)
        if s is None or e is None:
            continue
        a, b = tl.map_src(float(s)), tl.map_src(float(e))
        if b - a < 0.2:
            continue
        kind = z.get("kind", "punch")
        target = float(z.get("scale", zoom_cfg.get("push_scale" if kind == "push" else "punch_scale", 1.2)))
        for c in tl.main_clips():
            if c.tl_end <= a or c.tl_start >= b:
                continue
            if kind == "push":
                f0 = (max(c.tl_start, a) - a) / (b - a)
                f1 = (min(c.tl_end, b) - a) / (b - a)
                c.push = (1 + (target - 1) * f0, 1 + (target - 1) * f1)
                c.zoom = 1.0
            else:
                c.zoom = target
