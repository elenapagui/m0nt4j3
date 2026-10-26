"""El plan de edición (plan.json): lo que Claude revisa y ajusta antes de montar.

Todo lo que se refiere a momentos del vídeo usa el id de una frase («F012») o segundos
del vídeo ORIGINAL; la herramienta calcula después dónde cae en la línea de tiempo final.
"""

from __future__ import annotations

import copy
from pathlib import Path

from .timeline import Timeline, apply_zooms, build_timeline
from .util import MontajeError, deep_update, fmt_time, read_json, write_json

STYLES_DIR = Path(__file__).parent / "estilos"

PLAN_TEMPLATE = {
    "version": 1,
    "source": "",
    "transcript": "transcripcion.json",
    "style": "opinion",
    "notes": "",
    "phrases": [],
    "cut_words": [],
    "remove_ranges": [],
    "hook": None,
    "chapters": [],
    "titles": [],
    "highlights": [],
    "zooms": [],
    "sfx": [],
    "overlays": [],
    "music": None,
    "overrides": {},
    "color": None,
}


def list_styles() -> dict[str, str]:
    return {p.stem: read_json(p).get("descripcion", "") for p in sorted(STYLES_DIR.glob("*.json"))}


def load_style(name_or_path: str) -> dict:
    p = Path(name_or_path).expanduser()
    if not p.suffix:
        p = STYLES_DIR / f"{name_or_path}.json"
    if not p.exists():
        raise MontajeError(f"No existe el estilo «{name_or_path}». Estilos: {', '.join(list_styles())}")
    style = read_json(p)
    base = read_json(STYLES_DIR / "opinion.json")
    return deep_update(copy.deepcopy(base), style)


def effective_style(plan: dict) -> dict:
    """Estilo del plan con los retoques de plan['overrides'] aplicados."""
    return deep_update(load_style(plan.get("style", "opinion")), copy.deepcopy(plan.get("overrides") or {}))


def new_plan(source: Path, analysis: dict, style: str) -> dict:
    plan = copy.deepcopy(PLAN_TEMPLATE)
    plan["source"] = str(source)
    plan["style"] = style
    plan["phrases"] = analysis["phrases"]
    plan["cut_words"] = analysis["cut_words"]
    return plan


class Job:
    """Carpeta de trabajo de un vídeo: plan.json + transcripcion.json + silencios."""

    def __init__(self, plan_path: str | Path):
        self.plan_path = Path(plan_path).expanduser().resolve()
        if self.plan_path.is_dir():
            self.plan_path = self.plan_path / "plan.json"
        if not self.plan_path.exists():
            raise MontajeError(f"No encuentro el plan: {self.plan_path}")
        self.dir = self.plan_path.parent
        self.plan = read_json(self.plan_path)
        tr = read_json(self.dir / self.plan.get("transcript", "transcripcion.json"))
        self.transcript = tr
        self.words: list[dict] = tr["words"]
        self.silences = [tuple(s) for s in tr.get("silences", [])]
        self.duration = float(tr.get("duration") or 0)
        self.validate()

    @property
    def source(self) -> Path:
        return Path(self.plan["source"]).expanduser()

    def path(self, p: str | Path) -> Path:
        """Rutas del plan: absolutas, o relativas a la carpeta del plan."""
        p = Path(p).expanduser()
        return (p if p.is_absolute() else self.dir / p).resolve()

    def style(self) -> dict:
        return effective_style(self.plan)

    def save(self) -> None:
        write_json(self.plan_path, self.plan)

    def phrase(self, pid: str) -> dict:
        for p in self.plan["phrases"]:
            if p["id"] == pid:
                return p
        raise MontajeError(f"La frase «{pid}» no existe en el plan")

    def validate(self) -> None:
        ids = {p["id"] for p in self.plan["phrases"]}
        problems = []
        for key in ("chapters", "titles", "zooms", "sfx", "overlays"):
            for item in self.plan.get(key) or []:
                pid = item.get("phrase")
                if pid and pid not in ids:
                    problems.append(f"{key}: la frase «{pid}» no existe")
                if not pid and "at" not in item and "start" not in item:
                    problems.append(f"{key}: cada elemento necesita «phrase» o «at» (segundos del original)")
        hook = self.plan.get("hook") or {}
        for pid in hook.get("phrases", []):
            if pid not in ids:
                problems.append(f"hook: la frase «{pid}» no existe")
        n = len(self.words)
        if any(not (0 <= i < n) for i in self.plan.get("cut_words", [])):
            problems.append("cut_words contiene índices de palabra fuera de rango")
        if problems:
            raise MontajeError("El plan tiene errores:\n  - " + "\n  - ".join(problems))

    def timeline(self) -> Timeline:
        style = self.style()
        tl = build_timeline(self.plan, self.words, self.silences, style["cuts"], self.duration)
        if not tl.clips:
            raise MontajeError("Con este plan no queda nada en el vídeo (¿todas las frases tienen keep=false?)")
        apply_zooms(tl, self.plan, style["zoom"])
        return tl

    def at(self, item: dict, field: str = "start") -> float:
        """Segundos del original de un elemento que usa «phrase» o «at»."""
        if "at" in item:
            return float(item["at"])
        if field in item:
            return float(item[field])
        ph = self.phrase(item["phrase"])
        return float(ph["end"] if field == "end" else ph["start"])

    def summary(self) -> dict:
        tl = self.timeline()
        kept = [p for p in self.plan["phrases"] if p.get("keep", True)]
        return {
            "duracion_original": fmt_time(self.duration, False),
            "duracion_final": fmt_time(tl.duration, False),
            "recorte": f"{(1 - tl.duration / self.duration) * 100:.0f}%" if self.duration else "?",
            "clips": len(tl.clips),
            "frases": f"{len(kept)} de {len(self.plan['phrases'])}",
            "muletillas_cortadas": len(self.plan.get("cut_words", [])),
            "capitulos": len(self.plan.get("chapters", [])),
            "titulos": len(self.plan.get("titles", [])),
            "zooms_enfasis": len(self.plan.get("zooms", [])),
            "efectos_sonido": len(self.plan.get("sfx", [])),
            "gancho": bool((self.plan.get("hook") or {}).get("phrases")),
        }

    def write_review(self, maybe_fillers: dict | None = None) -> Path:
        """revision.md: la transcripción numerada con lo que se queda y lo que se corta."""
        cut = set(self.plan.get("cut_words", []))
        s = self.summary()
        lines = [
            f"# Revisión de «{self.source.name}»",
            "",
            f"Duración: {s['duracion_original']} → {s['duracion_final']} (−{s['recorte']}) · "
            f"{s['clips']} clips · frases {s['frases']} · muletillas cortadas {s['muletillas_cortadas']}",
            "",
            "Leyenda: ✅ se queda · ❌ se corta (motivo) · ~~palabra~~ muletilla cortada · [nº] índice de palabra",
            "",
        ]
        if maybe_fillers:
            lines += ["Posibles muletillas (no se cortan solas, decide tú): "
                      + ", ".join(f"«{k}» ×{v}" for k, v in maybe_fillers.items()), ""]
        chapters = {c.get("phrase"): c for c in self.plan.get("chapters", [])}
        for p in self.plan["phrases"]:
            if p["id"] in chapters:
                lines += ["", f"## Capítulo: {chapters[p['id']].get('title', '')}", ""]
            a, b = p["w"]
            toks = []
            for i in range(a, b):
                t = self.words[i]["w"].strip()
                toks.append(f"~~{t}~~[{i}]" if i in cut else t)
            mark = "✅" if p.get("keep", True) else f"❌ {p.get('reason', 'descartada')} —"
            lines.append(f"- **{p['id']}** `{fmt_time(p['start'])}` {mark} {' '.join(toks)}  ⟨{a}–{b - 1}⟩")
        out = self.dir / "revision.md"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out
