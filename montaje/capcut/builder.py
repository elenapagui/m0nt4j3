"""Aplica un plan de edición a un proyecto de CapCut.

Principio de diseño: no inventamos la estructura de CapCut, la CLONAMOS de tu propio
proyecto. El clip de vídeo que importaste en CapCut se usa como molde para cada corte
(conserva el encuadre y cualquier ajuste de color o LUT que le hayas puesto), y los textos
de ejemplo que escribas (SUBTITULO, TITULO, CLAVE) se usan como molde de cada subtítulo
o título, con su fuente, color, animación y posición. Solo si no hay moldes se usan
estructuras por defecto copiadas de un proyecto real.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..captions import caption_chunks, highlight_spans
from ..media import probe
from ..timeline import Timeline
from ..util import MontajeError, clamp, hex_to_rgb, new_id, new_local_id, normalize, read_json, utf16_len

DEFAULTS = read_json(Path(__file__).parent / "defaults.json")
PHOTO_DURATION = 10_800_000_000  # CapCut da a las fotos una duración «infinita» de 3 h
AUDIO_REF_CATEGORIES = {"speeds", "placeholder_infos", "sound_channel_mappings", "vocal_separations"}
TEMPLATE_LABELS = {"subtitulo": "caption", "subtitulos": "caption", "titulo": "title", "capitulo": "title",
                   "clave": "keyword", "destacado": "keyword"}


@dataclass
class Template:
    seg: dict
    mat: dict
    user: bool = True  # True = sale de tu proyecto; False = estructura por defecto


@dataclass
class Report:
    clips: int = 0
    captions: int = 0
    titles: int = 0
    sfx: int = 0
    overlays: int = 0
    music: int = 0
    duration_s: float = 0.0
    templates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DraftEditor:
    def __init__(self, draft: dict, meta: dict | None):
        self.d = copy.deepcopy(draft)
        self.meta = copy.deepcopy(meta) if meta is not None else None
        self.fps = float(self.d.get("fps") or 30) or 30.0
        self.mats: dict = self.d.setdefault("materials", {})
        self.report = Report()
        self.removed: set[str] = set()  # materiales de segmentos retirados (se limpian al final)

    def forget(self, seg: dict) -> None:
        self.removed.add(seg.get("material_id", ""))
        self.removed.update(seg.get("extra_material_refs") or [])

    def cleanup(self) -> None:
        used = set()
        for tr in self.d.get("tracks", []):
            for s in tr.get("segments", []):
                used.add(s.get("material_id", ""))
                used.update(s.get("extra_material_refs") or [])
        self.drop_materials(self.removed - used)

    # ---------- materiales ----------
    def find_mat(self, mid: str) -> tuple[str | None, dict | None]:
        for cat, items in self.mats.items():
            if isinstance(items, list):
                for m in items:
                    if isinstance(m, dict) and m.get("id") == mid:
                        return cat, m
        return None, None

    def add_mat(self, cat: str, mat: dict) -> dict:
        self.mats.setdefault(cat, []).append(mat)
        return mat

    def clone_refs(self, refs: list[str], dur_us: int | None = None,
                   only: set[str] | None = None) -> list[str]:
        out = []
        for rid in refs:
            cat, m = self.find_mat(rid)
            if m is None or (only is not None and cat not in only):
                continue
            c = copy.deepcopy(m)
            c["id"] = new_id()
            if cat == "material_animations" and dur_us is not None:
                fit_animations(c, dur_us)
            self.add_mat(cat, c)
            out.append(c["id"])
        return out

    def drop_materials(self, ids: set[str]) -> None:
        for cat, items in self.mats.items():
            if isinstance(items, list):
                self.mats[cat] = [m for m in items if not (isinstance(m, dict) and m.get("id") in ids)]

    # ---------- tiempo ----------
    def frame(self, t: float) -> int:
        return int(round(t * self.fps))

    def us_at(self, frame: int) -> int:
        return int(round(frame * 1_000_000 / self.fps))

    def span(self, start_s: float, end_s: float) -> tuple[int, int]:
        """(inicio, duración) en µs alineados a fotograma."""
        f0, f1 = self.frame(start_s), self.frame(end_s)
        if f1 <= f0:
            f1 = f0 + 1
        a = self.us_at(f0)
        return a, self.us_at(f1) - a

    # ---------- pistas ----------
    def new_track(self, kind: str, like: dict | None = None, name: str = "") -> dict:
        base = like or DEFAULTS["text_track"]
        tr = {k: copy.deepcopy(v) for k, v in base.items() if k != "segments"}
        tr.update({"id": new_id(), "type": kind, "segments": [], "name": name, "is_default_name": not name})
        return tr

    # ---------- registro de medios importados (draft_meta_info.json) ----------
    def register(self, path: Path, kind: str, duration_us: int, width: int = 0, height: int = 0) -> str:
        if self.meta is None:
            return new_local_id()
        groups = self.meta.setdefault("draft_materials", [])
        group = next((g for g in groups if g.get("type") == 0), None)
        if group is None:
            group = {"type": 0, "value": []}
            groups.append(group)
        fp = str(path)
        for e in group["value"]:
            if e.get("file_Path") == fp:
                return e["id"]
        entry = {
            "ai_group_type": "", "create_time": -1, "duration": duration_us, "enter_from": 0,
            "extra_info": path.name, "file_Path": fp, "height": height, "id": new_local_id(),
            "import_time": -1, "import_time_ms": -1, "item_source": 1, "md5": "",
            "metetype": {"audio": "music"}.get(kind, kind),
            "roughcut_time_range": {"duration": -1, "start": -1},
            "sub_time_range": {"duration": -1, "start": -1}, "type": 0, "width": width,
        }
        group["value"].append(entry)
        return entry["id"]


def fit_animations(mat: dict, dur_us: int) -> None:
    """Recoloca las animaciones de entrada/salida al cambiar la duración de un texto."""
    for a in mat.get("animations") or []:
        d = int(a.get("duration") or 0)
        kind = a.get("type")
        if kind in ("in", "out"):
            d = min(d, dur_us // 2)
            a["duration"] = d
            a["start"] = 0 if kind == "in" else max(0, dur_us - d)
        elif kind in ("loop", "group"):
            a["start"] = 0
            a["duration"] = dur_us


def solid(color: str) -> dict:
    return {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": hex_to_rgb(color)}}}


def keyframes(prop: str, points: list[tuple[int, float]]) -> dict:
    return {
        "id": new_id(), "material_id": "", "property_type": prop,
        "keyframe_list": [
            {"curveType": "Line", "graphID": "", "id": new_id(), "left_control": {"x": 0.0, "y": 0.0},
             "right_control": {"x": 0.0, "y": 0.0}, "time_offset": t, "values": [round(v, 6)]}
            for t, v in points
        ],
    }


def text_of(mat: dict) -> str:
    try:
        return json.loads(mat.get("content") or "{}").get("text", "")
    except json.JSONDecodeError:
        return ""


# =====================================================================================

def build(draft: dict, meta: dict | None, job, tl: Timeline, style: dict, *,
          sound: Callable[[str], Path], media_info=probe,
          template_draft: dict | None = None) -> tuple[dict, dict | None, Report]:
    ed = DraftEditor(draft, meta)
    plan = job.plan
    rep = ed.report

    main_track, main_seg, main_mat = find_main(ed, job.source, job.duration)
    templates, audio_tpl = harvest(ed, main_seg)
    if template_draft is not None:  # moldes del proyecto «Plantilla montaje» si este no trae los suyos
        for k, t in import_templates(ed, template_draft).items():
            if not templates[k].user:
                templates[k] = t
    rep.templates = [f"{k} ({'tu proyecto' if t.user else 'por defecto'})" for k, t in templates.items()]

    # ---------- vídeo principal ----------
    color_kf = color_keyframes(plan.get("color"))
    if main_seg.get("common_keyframes"):
        rep.warnings.append("El clip original tenía fotogramas clave; no se copian a los cortes.")
    canvas = ed.d.get("canvas_config") or {}
    base_clip = main_seg.get("clip") or {}
    bs = float((base_clip.get("scale") or {}).get("x", 1.0))
    by = float((base_clip.get("scale") or {}).get("y", bs))
    bt = base_clip.get("transform") or {"x": 0.0, "y": 0.0}
    anchor = style["zoom"].get("anchor", [0.0, 0.0])
    others = [s for s in main_track["segments"] if s is not main_seg]
    if others:
        rep.warnings.append(f"La pista principal tenía {len(others)} clips más (aparte de tu vídeo); se han quitado.")
        for s in others:
            ed.forget(s)
    new_segs = []
    frame_cursor = 0
    for i, c in enumerate(tl.clips):
        f0, f1 = ed.frame(c.src_start), ed.frame(c.src_end)
        if f1 <= f0:
            continue
        n = f1 - f0
        src_us = ed.us_at(f0)
        tl_us = ed.us_at(frame_cursor)
        dur_us = ed.us_at(frame_cursor + n) - tl_us
        frame_cursor += n
        seg = copy.deepcopy(main_seg)
        seg["id"] = new_id()
        seg["extra_material_refs"] = list(main_seg.get("extra_material_refs", [])) if i == 0 else \
            ed.clone_refs(main_seg.get("extra_material_refs", []))
        seg["source_timerange"] = {"start": src_us, "duration": dur_us}
        seg["target_timerange"] = {"start": tl_us, "duration": dur_us}
        kfs = [copy.deepcopy(k) for k in color_kf]
        for k in kfs:
            k["id"] = new_id()
        z0, z1 = c.push if c.push else (c.zoom, c.zoom)
        sx0, tx0, ty0 = zoomed(bs, bt, anchor, z0)
        seg["clip"] = copy.deepcopy(base_clip) or {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
                                                     "rotation": 0.0, "scale": {}, "transform": {}}
        seg["clip"]["scale"] = {"x": sx0, "y": round(by * z0, 6)}
        seg["clip"]["transform"] = {"x": tx0, "y": ty0}
        if c.push:
            sx1, tx1, ty1 = zoomed(bs, bt, anchor, z1)
            kfs += [keyframes("UNIFORM_SCALE", [(0, sx0), (dur_us, sx1)]),
                    keyframes("KFTypePositionX", [(0, tx0), (dur_us, tx1)]),
                    keyframes("KFTypePositionY", [(0, ty0), (dur_us, ty1)])]
        seg["common_keyframes"] = kfs
        new_segs.append(seg)
    main_track["segments"] = new_segs
    total_us = ed.us_at(frame_cursor)
    rep.clips = len(new_segs)
    rep.duration_s = total_us / 1e6

    # ---------- lo que ya había en el proyecto ----------
    keep_others(ed, main_track, main_seg, total_us)

    tracks = ed.d["tracks"]
    insert_at = tracks.index(main_track) + 1

    # ---------- B-roll / imágenes encima ----------
    overlay_tracks: list[dict] = []
    for ov in plan.get("overlays") or []:
        place_overlay(ed, job, tl, ov, main_seg, main_mat, overlay_tracks, total_us, canvas, media_info)
    for k, tr in enumerate(overlay_tracks):
        tracks.insert(insert_at + k, tr)

    # ---------- títulos, capítulos y palabras clave ----------
    title_tracks: list[dict] = []
    sfx_events: list[tuple[float, str, float]] = []  # (segundo de la línea de tiempo, sonido, volumen)
    for ch in plan.get("chapters") or []:
        t = tl.map_src(job.at(ch))
        cfg = style["titles"]
        dur = float(ch.get("duration", cfg.get("duration", 2.2)))
        add_text(ed, title_tracks, templates.get("title"), ch.get("title", ""), t, min(t + dur, total_us / 1e6),
                 cfg, [], plan, kind="title")
        if cfg.get("sfx"):
            sfx_events.append((t - 0.2, cfg["sfx"], style["sfx"]["volume"]))
    for ti in plan.get("titles") or []:
        kind = ti.get("kind", "keyword")
        cfg = style["titles" if kind == "title" else "keywords"]
        a = tl.map_src(job.at(ti))
        b = tl.map_src(job.at(ti, "end")) if "duration" not in ti else a + float(ti["duration"])
        b = clamp(b, a + 1.2, a + 6.0)
        add_text(ed, title_tracks, templates.get("title" if kind == "title" else "keyword"), ti.get("text", ""),
                 a, min(b, total_us / 1e6), cfg, [], plan, kind=kind)
        if cfg.get("sfx") and ti.get("sfx", True):
            sfx_events.append((a - 0.05, ti.get("sound", cfg["sfx"]), style["sfx"]["volume"]))
    for tr in title_tracks:
        tr["segments"].sort(key=lambda x: x["target_timerange"]["start"])
    tracks.extend(title_tracks)
    rep.titles = sum(len(tr["segments"]) for tr in title_tracks)

    # ---------- subtítulos ----------
    ccfg = style["captions"]
    if ccfg.get("enabled", True):
        cap_tracks: list[dict] = []
        hl = plan.get("highlights") or []
        for ch in caption_chunks(tl, ccfg):
            spans = highlight_spans(ch["text"], hl, ccfg.get("highlight_numbers", False))
            add_text(ed, cap_tracks, templates.get("caption"), ch["text"], ch["start"], ch["end"], ccfg, spans,
                     plan, kind="caption")
        tracks.extend(cap_tracks)
        rep.captions = sum(len(tr["segments"]) for tr in cap_tracks)

    # ---------- efectos de sonido ----------
    hook_clips = [c for c in tl.clips if c.section == "hook"]
    if hook_clips and style["titles"].get("sfx"):
        sfx_events.append((hook_clips[-1].tl_end - 0.25, style["titles"]["sfx"], style["sfx"]["volume"]))
    for s in plan.get("sfx") or []:
        sfx_events.append((tl.map_src(job.at(s)) + float(s.get("offset", 0)), s.get("sound", "whoosh"),
                           float(s.get("volume", style["sfx"]["volume"]))))
    sfx_tracks: list[dict] = []
    for t, name, vol in sorted(sfx_events):
        path = sound(str(job.path(name)) if Path(name).suffix else name)
        place_audio(ed, sfx_tracks, audio_tpl, main_seg, path, max(0.0, t), vol, total_us, media_info)
    rep.sfx = sum(len(tr["segments"]) for tr in sfx_tracks)
    tracks.extend(sfx_tracks)

    # ---------- música ----------
    music = plan.get("music")
    if music and music.get("file"):
        mtracks: list[dict] = []
        place_music(ed, mtracks, audio_tpl, main_seg, job, tl, music, style["music"], total_us, media_info)
        rep.music = sum(len(tr["segments"]) for tr in mtracks)
        tracks.extend(mtracks)

    # ---------- orden de capas y duración ----------
    for ti_, tr in enumerate(tracks):
        tr.get("segments", []).sort(key=lambda x: (x.get("target_timerange") or {}).get("start", 0))
        for s in tr.get("segments", []):
            s["track_render_index"] = ti_
            if tr.get("type") == "text":
                s["render_index"] = 14000 + ti_
            elif tr.get("type") == "video" and tr is not main_track:
                s["render_index"] = ti_
    ed.cleanup()
    ed.d["duration"] = max([total_us] + [
        s["target_timerange"]["start"] + s["target_timerange"]["duration"]
        for tr in tracks for s in tr.get("segments", []) if s.get("target_timerange")
    ])
    return ed.d, ed.meta, rep


def zoomed(base_scale: float, base_t: dict, anchor: list[float], z: float) -> tuple[float, float, float]:
    """Escala y posición para hacer zoom `z` manteniendo fijo el punto `anchor` de la pantalla.

    Un punto p del clip se ve en t + s·p. Para que el ancla A no se mueva al escalar por z:
    t' = A·(1 − z) + z·t.
    """
    ax, ay = float(anchor[0]), float(anchor[1])
    tx, ty = float(base_t.get("x", 0.0)), float(base_t.get("y", 0.0))
    return round(base_scale * z, 6), round(ax * (1 - z) + z * tx, 6), round(ay * (1 - z) + z * ty, 6)


def color_keyframes(color: dict | None) -> list[dict]:
    """Brillo, contraste y saturación como valores fijos del clip (-1…1; ×50 en la interfaz)."""
    if not color or color.get("mode", "auto") in ("none", "ninguno", "hornear", "lut"):
        return []
    adj = color.get("capcut") or {}
    props = {"brightness": "KFTypeBrightness", "contrast": "KFTypeContrast", "saturation": "KFTypeSaturation"}
    return [keyframes(p, [(0, clamp(float(adj[k]), -1, 1))]) for k, p in props.items() if abs(float(adj.get(k, 0))) > 1e-3]


def find_main(ed: DraftEditor, source: Path, duration: float) -> tuple[dict, dict, dict]:
    cands = []
    for tr in ed.d.get("tracks", []):
        if tr.get("type") != "video":
            continue
        for s in tr.get("segments", []):
            cat, m = ed.find_mat(s.get("material_id", ""))
            if m and cat == "videos" and m.get("type", "video") == "video":
                cands.append((tr, s, m))
    if not cands:
        raise MontajeError("El proyecto de CapCut no tiene ningún vídeo. Importa tu vídeo, arrástralo a la línea de "
                           "tiempo, cierra CapCut y vuelve a intentarlo.")
    by_name = [c for c in cands if Path(str(c[2].get("path", ""))).name == source.name]
    by_dur = [c for c in cands if abs(c[2].get("duration", 0) / 1e6 - duration) < 1.0]
    pick = by_name or by_dur
    if not pick:
        names = ", ".join(sorted({Path(str(c[2].get("path", ""))).name for c in cands}))
        raise MontajeError(f"En el proyecto no está «{source.name}» (hay: {names}). Importa ese vídeo en CapCut "
                           "o usa el proyecto correcto.")
    tr, seg, mat = max(pick, key=lambda c: (c[1].get("target_timerange") or {}).get("duration", 0))
    if abs(float(seg.get("speed", 1.0)) - 1.0) > 1e-6:
        raise MontajeError("El clip principal tiene la velocidad cambiada. Déjalo a velocidad 1x antes de montar.")
    if duration and abs(mat.get("duration", 0) / 1e6 - duration) > 1.0:
        ed.report.warnings.append(f"La duración del vídeo en CapCut ({mat.get('duration', 0) / 1e6:.1f}s) no coincide "
                                  f"con la transcrita ({duration:.1f}s). ¿Es el mismo archivo?")
    return tr, seg, mat


def harvest(ed: DraftEditor, main_seg: dict) -> tuple[dict[str, Template], Template | None]:
    """Busca moldes en tu proyecto (textos SUBTITULO / TITULO / CLAVE) y los retira de la línea de tiempo."""
    found: dict[str, Template] = {}
    audio_tpl = None
    for tr in ed.d.get("tracks", []):
        keep = []
        for s in tr.get("segments", []):
            if s is main_seg:
                keep.append(s)
                continue
            cat, m = ed.find_mat(s.get("material_id", ""))
            label = TEMPLATE_LABELS.get(normalize(text_of(m)).split(" ")[0]) if m and cat == "texts" else None
            if label:
                found.setdefault(label, Template(seg=copy.deepcopy(s), mat=copy.deepcopy(m)))
                ed.forget(s)
                continue
            if m and cat == "audios" and audio_tpl is None:
                audio_tpl = Template(seg=copy.deepcopy(s), mat=copy.deepcopy(m))
            keep.append(s)
        tr["segments"] = keep
    if "caption" not in found:
        found["caption"] = Template(seg=copy.deepcopy(DEFAULTS["text_segment"]),
                                    mat=copy.deepcopy(DEFAULTS["text_material"]), user=False)
    for k in ("title", "keyword"):
        if k not in found:
            found[k] = Template(seg=copy.deepcopy(DEFAULTS["text_segment"]),
                                mat=copy.deepcopy(DEFAULTS["text_material"]), user=False)
    return found, audio_tpl


def import_templates(ed: DraftEditor, other: dict) -> dict[str, Template]:
    """Copia los moldes de texto de otro proyecto (con sus animaciones) a este."""
    found: dict[str, Template] = {}
    src = DraftEditor(other, None)
    for tr in other.get("tracks", []):
        for s in tr.get("segments", []):
            cat, m = src.find_mat(s.get("material_id", ""))
            label = TEMPLATE_LABELS.get(normalize(text_of(m)).split(" ")[0]) if m and cat == "texts" else None
            if not label or label in found:
                continue
            seg = copy.deepcopy(s)
            refs = []
            for rid in s.get("extra_material_refs") or []:
                rcat, rm = src.find_mat(rid)
                if rm is None:
                    continue
                c = copy.deepcopy(rm)
                c["id"] = new_id()
                ed.add_mat(rcat, c)
                ed.removed.add(c["id"])  # solo sirven de molde: se limpian al final
                refs.append(c["id"])
            seg["extra_material_refs"] = refs
            found[label] = Template(seg=seg, mat=copy.deepcopy(m))
    return found


def keep_others(ed: DraftEditor, main_track: dict, main_seg: dict, total_us: int) -> None:
    """Lo que ya había: capas que cubren todo el vídeo se adaptan; el resto se quita (desincronizado)."""
    orig = (main_seg.get("target_timerange") or {}).get("duration", 0) or 1
    o_start = (main_seg.get("target_timerange") or {}).get("start", 0)
    dropped = 0
    for tr in ed.d.get("tracks", []):
        if tr is main_track:
            continue
        keep = []
        for s in tr.get("segments", []):
            r = s.get("target_timerange") or {}
            covers = r.get("start", 0) <= o_start + orig * 0.05 and r.get("duration", 0) >= orig * 0.9
            if not covers:
                dropped += 1
                ed.forget(s)
                continue
            if s.get("source_timerange"):  # vídeo o audio: se recorta, no se estira
                d = min(r["duration"], total_us)
                s["source_timerange"]["duration"] = int(d * float(s.get("speed", 1.0)))
                s["target_timerange"] = {"start": 0, "duration": d}
            else:  # textos fijos, filtros, capas de ajuste: cubren todo el vídeo nuevo
                s["target_timerange"] = {"start": 0, "duration": total_us}
            keep.append(s)
        tr["segments"] = keep
    if dropped:
        ed.report.warnings.append(f"Se han quitado {dropped} elemento(s) que ya había en el proyecto y que "
                                  "quedarían desincronizados tras los cortes (siguen en la copia de seguridad).")
    ed.d["tracks"] = [t for t in ed.d.get("tracks", []) if t is main_track or t.get("segments")]


def add_text(ed: DraftEditor, tracks: list[dict], tpl: Template | None, text: str, start: float, end: float, cfg: dict,
             spans: list[tuple[int, int]], plan: dict, kind: str) -> None:
    if not text.strip() or end - start < 0.1:
        return
    tpl = tpl or Template(seg=DEFAULTS["text_segment"], mat=DEFAULTS["text_material"], user=False)
    a_us, d_us = ed.span(start, end)
    mat = copy.deepcopy(tpl.mat)
    mat["id"] = new_id()
    try:
        content = json.loads(mat.get("content") or "{}")
    except json.JSONDecodeError:
        content = {}
    styles = content.get("styles") or [json.loads(DEFAULTS["text_material"]["content"])["styles"][0]]
    base = copy.deepcopy(styles[0])
    hl_style = copy.deepcopy(styles[1]) if tpl.user and len(styles) > 1 else None
    if not tpl.user:
        base["size"] = float(cfg.get("font_size", 8))
        base["fill"] = solid(cfg.get("color", "#FFFFFF"))
        base["bold"] = bool(cfg.get("bold", False))
        if cfg.get("stroke"):
            base["strokes"] = [{"content": {"solid": {"alpha": 1.0, "color": hex_to_rgb(cfg.get("stroke_color", "#000000"))}},
                                "width": float(cfg.get("stroke_width", 0.08))}]
            mat["border_width"] = float(cfg.get("stroke_width", 0.08))
            mat["check_flag"] = int(mat.get("check_flag", 7)) | 8
        mat["font_size"] = base["size"]
        mat["text_color"] = cfg.get("color", "#FFFFFF")
        mat["alignment"] = 1
    if hl_style is None:
        hl_style = copy.deepcopy(base)
        hl_style["fill"] = solid(cfg.get("highlight_color", "#FFD400"))
    # rangos en unidades UTF-16 (así los mide CapCut)
    runs, pos = [], 0
    for s, e in spans:
        if s > pos:
            runs.append((pos, s, False))
        runs.append((s, e, True))
        pos = e
    if pos < len(text):
        runs.append((pos, len(text), False))
    out_styles = []
    for s, e, is_hl in runs:
        st = copy.deepcopy(hl_style if is_hl else base)
        st["range"] = [utf16_len(text[:s]), utf16_len(text[:e])]
        out_styles.append(st)
    content["text"] = text
    content["styles"] = out_styles
    mat["content"] = json.dumps(content, ensure_ascii=False)
    for k in ("words", "current_words"):
        if isinstance(mat.get(k), dict):
            mat[k] = {"start_time": [], "end_time": [], "text": []}
    if "recognize_task_id" in mat:
        mat["recognize_task_id"] = ""
    ed.add_mat("texts", mat)

    seg = copy.deepcopy(tpl.seg)
    seg["id"] = new_id()
    seg["material_id"] = mat["id"]
    if tpl.user:
        seg["extra_material_refs"] = ed.clone_refs(tpl.seg.get("extra_material_refs", []), d_us)
    else:
        anim = copy.deepcopy(DEFAULTS["sticker_animation"])
        anim["id"] = new_id()
        ed.add_mat("material_animations", anim)
        seg["extra_material_refs"] = [anim["id"]]
        seg["clip"] = copy.deepcopy(seg.get("clip") or {})
        seg["clip"]["transform"] = {"x": 0.0, "y": float(cfg.get("position_y", 0.0))}
    seg["target_timerange"] = {"start": a_us, "duration": d_us}
    seg["common_keyframes"] = []
    _pack(tracks, a_us, ed, "text", None)["segments"].append(seg)


def _pack(tracks: list[dict], start: int, ed: DraftEditor, kind: str, like: dict | None, name: str = "") -> dict:
    """Primera pista libre en ese momento (si dos sonidos se solapan, van en pistas distintas)."""
    for tr in tracks:
        last = tr["segments"][-1]["target_timerange"] if tr["segments"] else None
        if not last or last["start"] + last["duration"] <= start:
            return tr
    tr = ed.new_track(kind, like=like, name=name)
    tracks.append(tr)
    return tr


def _audio_segment(ed: DraftEditor, tpl: Template | None, main_seg: dict, path: Path, src_us: int, start_us: int,
                   dur_us: int, volume: float, file_dur_us: int, fade: tuple[int, int] | None = None) -> dict:
    if tpl:
        mat = copy.deepcopy(tpl.mat)
        seg = copy.deepcopy(tpl.seg)
        seg["extra_material_refs"] = ed.clone_refs(tpl.seg.get("extra_material_refs", []),
                                                   only=AUDIO_REF_CATEGORIES)
    else:
        mat = {"app_id": 0, "category_id": "", "category_name": "local", "check_flag": 3,
               "copyright_limit_type": "none", "effect_id": "", "formula_id": "", "source_platform": 0,
               "type": "extract_music", "wave_points": []}
        seg = copy.deepcopy(main_seg)
        seg["clip"] = None
        seg["hdr_settings"] = None
        seg["extra_material_refs"] = ed.clone_refs(main_seg.get("extra_material_refs", []),
                                                   only=AUDIO_REF_CATEGORIES)
    mat.update({"id": new_id(), "path": str(path), "name": path.name, "duration": file_dur_us,
                "music_id": new_id(), "local_material_id": ed.register(path, "audio", file_dur_us)})
    ed.add_mat("audios", mat)
    if fade and (fade[0] or fade[1]):
        f = {"id": new_id(), "fade_in_duration": fade[0], "fade_out_duration": fade[1], "fade_type": 0,
             "type": "audio_fade"}
        ed.add_mat("audio_fades", f)
        seg["extra_material_refs"].append(f["id"])
    seg.update({"id": new_id(), "material_id": mat["id"], "volume": volume, "last_nonzero_volume": volume or 1.0,
                "speed": 1.0, "common_keyframes": [],
                "source_timerange": {"start": src_us, "duration": dur_us},
                "target_timerange": {"start": start_us, "duration": dur_us}})
    return seg


def place_audio(ed, tracks, tpl, main_seg, path: Path, t: float, volume: float, total_us: int, media_info) -> None:
    info = media_info(path)
    start_us = ed.us_at(ed.frame(t))
    file_us = int(info.duration * 1e6)
    dur_us = min(file_us, total_us - start_us)
    if dur_us <= 0:
        return
    tr = _pack(tracks, start_us, ed, "audio", None, name="Efectos")
    tr["segments"].append(_audio_segment(ed, tpl, main_seg, path, 0, start_us, dur_us, volume, file_us))


def place_music(ed, tracks, tpl, main_seg, job, tl, music: dict, cfg: dict, total_us: int, media_info) -> None:
    path = job.path(music["file"])
    info = media_info(path)
    file_us = int(info.duration * 1e6)
    start_us = ed.us_at(ed.frame(tl.map_src(job.at(music)))) if ("phrase" in music or "at" in music) else 0
    end_us = ed.us_at(ed.frame(tl.map_src(job.at(music, "end")))) if "end" in music else total_us
    vol = float(music.get("volume", cfg.get("volume", 0.07)))
    fi = int(float(music.get("fade_in", cfg.get("fade_in", 1.5))) * 1e6)
    fo = int(float(music.get("fade_out", cfg.get("fade_out", 3.0))) * 1e6)
    tr = ed.new_track("audio", name="Música")
    t = start_us
    while t < end_us - 100_000 and file_us > 0:
        d = min(file_us, end_us - t)
        first, last = t == start_us, t + d >= end_us - 100_000
        tr["segments"].append(_audio_segment(ed, tpl, main_seg, path, 0, t, d, vol, file_us,
                                             fade=(fi if first else 0, fo if last else 0)))
        t += d
        if not music.get("loop", True):
            break
    tracks.append(tr)


def place_overlay(ed, job, tl, ov: dict, main_seg: dict, main_mat: dict, tracks: list[dict], total_us: int,
                  canvas: dict, media_info) -> None:
    path = job.path(ov["file"])
    if not path.exists():
        ed.report.warnings.append(f"B-roll no encontrado, se omite: {path}")
        return
    info = media_info(path)
    a = tl.map_src(job.at(ov))
    if "duration" in ov:
        b = a + float(ov["duration"])
    elif "phrase" in ov or "end" in ov:
        b = tl.map_src(job.at(ov, "end"))
    else:
        b = a + 4.0
    if info.kind == "video":
        b = min(b, a + info.duration - float(ov.get("from", 0)))
    start_us, dur_us = ed.span(a, min(b, total_us / 1e6))
    if dur_us < 200_000:
        return
    kind = info.kind if info.kind in ("video", "photo") else None
    if kind is None:
        ed.report.warnings.append(f"{path.name} no es vídeo ni imagen; se omite")
        return
    file_us = PHOTO_DURATION if kind == "photo" else int(info.duration * 1e6)
    mat = copy.deepcopy(main_mat)
    mat.update({"id": new_id(), "path": str(path), "material_name": path.name, "type": kind,
                "width": info.width, "height": info.height, "duration": file_us, "has_audio": info.has_audio,
                "local_material_id": ed.register(path, kind, file_us, info.width, info.height)})
    ed.add_mat("videos", mat)
    seg = copy.deepcopy(main_seg)
    seg.update({"id": new_id(), "material_id": mat["id"],
                "extra_material_refs": ed.clone_refs(main_seg.get("extra_material_refs", [])),
                "source_timerange": {"start": int(float(ov.get("from", 0)) * 1e6), "duration": dur_us},
                "target_timerange": {"start": start_us, "duration": dur_us},
                "volume": float(ov.get("volume", 0.0)), "common_keyframes": []})
    cw, ch = canvas.get("width", 1920), canvas.get("height", 1080)
    if ov.get("mode", "full") == "pip":
        scale, pos = float(ov.get("scale", 0.38)), ov.get("position", [0.55, 0.5])
    else:  # a pantalla completa, recortando lo que sobre (CapCut por defecto encaja sin recortar)
        mr, cr = (info.width / info.height) if info.height else 1, cw / ch
        scale, pos = max(cr / mr, mr / cr), [0.0, 0.0]
    seg["clip"] = {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                   "scale": {"x": scale, "y": scale}, "transform": {"x": float(pos[0]), "y": float(pos[1])}}
    if ov.get("zoom", True) and kind == "photo":  # efecto Ken Burns suave en las fotos
        seg["common_keyframes"] = [keyframes("UNIFORM_SCALE", [(0, scale), (dur_us, scale * 1.06)])]
    tr = _pack(tracks, start_us, ed, "video", None, name="B-roll")
    tr["segments"].append(seg)
    ed.report.overlays += 1
