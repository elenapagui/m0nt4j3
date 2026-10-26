"""Línea de comandos: `montaje <orden>`. Ejecuta `montaje --help` para ver todas."""

from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
import time
from pathlib import Path

from . import __version__
from .util import MontajeError, fmt_time, info, read_json, slugify, warn, which, write_json

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "trabajos"


def out(data) -> None:
    """Resultado para Claude / para ti: JSON legible por la salida estándar."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ preparar
def cmd_preparar(a) -> None:
    from .analyze import analyze
    from .media import detect_silences, extract_audio, loudness_floor, probe
    from .plan import Job, load_style, new_plan
    from .transcribe import load_words_file, transcribe

    video = Path(a.video).expanduser().resolve()
    mi = probe(video)
    if not mi.has_audio:
        raise MontajeError("El vídeo no tiene audio: esta herramienta corta a partir de lo que dices.")
    load_style(a.estilo)
    job_dir = (Path(a.salida).expanduser() if a.salida else JOBS / slugify(video.stem)).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    plan_path = job_dir / "plan.json"
    if plan_path.exists() and not a.rehacer:
        raise MontajeError(f"Ya existe {plan_path}. Usa --rehacer para empezar de cero (se pierde la revisión).")
    wav = extract_audio(video, job_dir / "audio16k.wav")
    if a.palabras:
        tr = {"backend": "archivo", "model": "", "language": a.idioma, "words": load_words_file(Path(a.palabras))}
    else:
        tr = transcribe(wav, language=a.idioma, model=a.modelo)
    if not tr["words"]:
        raise MontajeError("La transcripción ha salido vacía. ¿Se oye bien la voz? Prueba otro --idioma.")
    noise = loudness_floor(wav)
    silences = detect_silences(wav, noise_db=noise, min_dur=0.25)
    tr["words"] = drop_hallucinations(tr["words"], silences)
    tr.update({"source": str(video), "duration": mi.duration, "silence_db": noise,
               "silences": [list(s) for s in silences]})
    write_json(job_dir / "transcripcion.json", tr, indent=None)
    analysis = analyze(tr["words"])
    write_json(plan_path, new_plan(video, analysis, a.estilo))
    job = Job(plan_path)
    review = job.write_review(analysis["maybe_fillers"])
    out({"trabajo": str(job_dir), "plan": str(plan_path), "revision": str(review),
         "video": {"duracion": fmt_time(mi.duration, False), "formato": f"{mi.width}x{mi.height}", "fps": mi.fps},
         "palabras": len(tr["words"]), "frases": len(analysis["phrases"]),
         "propuesta": job.summary(), "posibles_muletillas": analysis["maybe_fillers"],
         "siguiente_paso": "Revisa revision.md y ajusta plan.json (keep, capítulos, títulos, zooms…)."})


def drop_hallucinations(words: list[dict], silences: list[tuple[float, float]]) -> list[dict]:
    """Whisper a veces «oye» palabras en los silencios (sobre todo el texto del prompt). Fuera."""
    def in_silence(w):
        mid = (w["s"] + w["e"]) / 2
        return any(a + 0.05 < mid < b - 0.05 for a, b in silences)
    kept = [w for w in words if not (w.get("p", 1.0) < 0.3 and in_silence(w))]
    if len(kept) < len(words):
        info(f"Descartadas {len(words) - len(kept)} palabras «oídas» en silencios (alucinaciones de Whisper).")
    return kept


# ------------------------------------------------------------------ revisar / frases
def cmd_revisar(a) -> None:
    from .plan import Job

    job = Job(a.plan)
    review = job.write_review()
    out({"revision": str(review), "resumen": job.summary()})


def _ids(text: str | None) -> list[str]:
    out_ = []
    for part in (text or "").replace(" ", "").split(","):
        if "-" in part and part.upper().startswith("F"):
            a_, b_ = part.upper().split("-")
            out_ += [f"F{i:03d}" for i in range(int(a_[1:]), int(b_.lstrip("F")) + 1)]
        elif part:
            out_.append(part.upper())
    return out_


def cmd_frases(a) -> None:
    from .plan import Job

    job = Job(a.plan)
    for pid in _ids(a.quitar):
        ph = job.phrase(pid)
        ph["keep"] = False
        ph["reason"] = a.motivo or ph.get("reason") or "descartada en revisión"
    for pid in _ids(a.mantener):
        ph = job.phrase(pid)
        ph["keep"] = True
        ph.pop("reason", None)
    cut = set(job.plan.get("cut_words", []))
    cut |= {int(i) for i in (a.cortar_palabras or "").split(",") if i.strip()}
    cut -= {int(i) for i in (a.recuperar_palabras or "").split(",") if i.strip()}
    job.plan["cut_words"] = sorted(cut)
    job.save()
    job.validate()
    job.write_review()
    out({"resumen": job.summary()})


# ------------------------------------------------------------------ color
def cmd_color(a) -> None:
    from . import color as C
    from .plan import Job

    job = None
    if Path(a.objetivo).expanduser().suffix.lower() == ".json" or Path(a.objetivo).expanduser().is_dir():
        job = Job(a.objetivo)
        video, out_dir = job.source, job.dir
        style_color = job.style().get("color", {})
    else:
        video = Path(a.objetivo).expanduser().resolve()
        out_dir = JOBS / slugify(video.stem)
        style_color = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    look = a.look or style_color.get("look", "natural")
    strength = a.intensidad if a.intensidad is not None else style_color.get("strength", 0.7)
    frames, st = C.analyze_video(video)
    ref = None
    if a.referencia:
        _, ref = C.analyze_video(Path(a.referencia).expanduser())
    corr = C.correction(st, look, strength, ref, {"brightness": a.brillo, "contrast": a.contraste,
                                                   "saturation": a.saturacion})
    lut = C.write_cube(out_dir / "color.cube", C.grade_fn(corr, include_tone=False), title=f"montaje {look}")
    full = C.write_cube(out_dir / "color_completo.cube", C.grade_fn(corr, include_tone=True),
                        title=f"montaje {look} completo")
    ba = C.before_after(frames, C.grade_fn(corr, include_tone=True), out_dir / "color_antes_despues.jpg")
    result = {
        "analisis": {k: round(v, 3) if isinstance(v, float) else v for k, v in st.items()
                     if k in ("luma_mean", "luma_p2", "luma_p98", "sat_mean", "sat_mean_wb", "warmth", "clipped_highlights",
                              "crushed_shadows")},
        "ajustes_capcut": {k: f"{v:+.2f} (≈{v * 50:+.0f} en CapCut)" for k, v in corr["capcut"].items()},
        "balance_blancos": corr["wb_gains"], "look": look, "intensidad": strength,
        "lut": str(lut), "lut_completa": str(full), "antes_despues": str(ba),
    }
    if job:
        job.plan["color"] = {"mode": a.modo, "capcut": corr["capcut"], "lut": str(lut), "lut_completa": str(full),
                             "look": look, "strength": strength, "referencia": a.referencia}
        job.save()
        result["plan_actualizado"] = str(job.plan_path)
    if a.hornear:
        dest = video.with_name(f"{video.stem}_color{video.suffix if video.suffix.lower() in ('.mp4', '.mov') else '.mp4'}")
        info(f"Horneando el color en {dest} (puede tardar)…")
        C.bake(video, full, dest)
        result["video_con_color"] = str(dest)
        result["aviso"] = ("Importa este archivo en CapCut en lugar del original. En el plan pon color.mode = "
                           "'hornear' para no aplicar los ajustes dos veces.")
        if job:
            job.plan["color"]["mode"] = "hornear"
            job.save()
    out(result)


# ------------------------------------------------------------------ referencia / fotograma
def cmd_referencia(a) -> None:
    from .preview import analyze_reference

    video = Path(a.video).expanduser().resolve()
    out_dir = Path(a.salida).expanduser() if a.salida else ROOT / "referencias" / slugify(video.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = analyze_reference(video, out_dir)
    write_json(out_dir / "referencia.json", res)
    res["guardado_en"] = str(out_dir / "referencia.json")
    out(res)


def cmd_fotograma(a) -> None:
    from .media import grab_frame
    from .plan import Job

    target = Path(a.objetivo).expanduser()
    if target.suffix.lower() == ".json" or target.is_dir():
        job = Job(target)
        video, dest_dir = job.source, job.dir
    else:
        video, dest_dir = target.resolve(), JOBS / slugify(target.stem)
    dest = Path(a.salida).expanduser() if a.salida else dest_dir / f"fotograma_{a.t:.1f}.jpg"
    out({"imagen": str(grab_frame(video, a.t, dest))})


# ------------------------------------------------------------------ CapCut
def cmd_proyectos(a) -> None:
    from .capcut.store import capcut_running, default_drafts_dir, list_projects

    projects = list_projects()
    out({"carpeta": str(default_drafts_dir()), "capcut_abierto": capcut_running(),
         "proyectos": [{"nombre": p.name, "carpeta": p.dir.name, "duracion": fmt_time(p.duration(), False),
                        "version_capcut": p.app_version(), "abierto": p.locked,
                        "modificado": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.primary.stat().st_mtime))}
                       for p in projects[:a.limite]]})


def _carry_over(new: dict, current: dict, foreign: list, total_us: int) -> int:
    """Capas globales que añadiste en CapCut (ajustes, filtros, efectos) se conservan al rehacer."""
    carried = 0
    tracks = {t["id"]: t for t in new["tracks"]}
    mats_new = new.setdefault("materials", {})
    for tr, seg in foreign:
        if tr.get("type") in ("video", "audio", "text"):
            continue
        dest = tracks.get(tr["id"])
        if dest is None:
            dest = {k: copy.deepcopy(v) for k, v in tr.items() if k != "segments"}
            dest["segments"] = []
            new["tracks"].append(dest)
            tracks[dest["id"]] = dest
        s = copy.deepcopy(seg)
        s["target_timerange"] = {"start": 0, "duration": total_us}
        dest["segments"].append(s)
        wanted = {seg.get("material_id")} | set(seg.get("extra_material_refs") or [])
        for cat, items in (current.get("materials") or {}).items():
            if isinstance(items, list):
                for m in items:
                    if isinstance(m, dict) and m.get("id") in wanted:
                        mats_new.setdefault(cat, []).append(copy.deepcopy(m))
        carried += 1
    return carried


TEMPLATE_PROJECT_NAMES = ("plantilla montaje", "plantilla")


def _template_project(name: str | None, current):
    """Proyecto de CapCut con tus moldes de texto (por defecto, el llamado «Plantilla montaje»)."""
    from .capcut.store import find_project, list_projects
    from .util import normalize

    if name:
        return find_project(name)
    for p in list_projects():
        if normalize(p.name) in TEMPLATE_PROJECT_NAMES and p.dir != current.dir:
            return p
    return None


def cmd_montar(a) -> None:
    from .capcut import builder
    from .capcut.store import capcut_running, find_project
    from .capcut.validate import validate
    from .plan import Job
    from .sfx import find_sound

    job = Job(a.plan)
    proj = find_project(a.proyecto)
    if not a.simular:
        if capcut_running() and not a.forzar:
            raise MontajeError("CapCut está abierto. Ciérralo del todo (Cmd+Q) y repite: si no, CapCut "
                               "sobrescribe los cambios al guardar.")
        if proj.locked and not a.forzar:
            raise MontajeError("El proyecto está abierto en CapCut (.locked). Ciérralo y repite.")
    modified, foreign = proj.changes_since_last_build()
    blocking = [(t, s) for t, s in foreign if t.get("type") in ("video", "audio", "text")]
    if (modified or blocking) and not a.forzar:
        what = []
        if modified:
            what.append("has movido o borrado partes del montaje")
        if blocking:
            what.append(f"has añadido {len(blocking)} clips/textos/audios a mano")
        raise MontajeError(
            "Desde el último montaje " + " y ".join(what) + " en CapCut. Si vuelvo a montar se pierden esos "
            "retoques (quedan en la copia de seguridad). Repite con --forzar si es lo que quieres.")
    proj.original_snapshot()
    base = proj.load_original()
    meta_snap = proj.original_snapshot() / "draft_meta_info.json"
    meta = read_json(meta_snap) if meta_snap.exists() else proj.meta
    style = job.style()
    tl = job.timeline()
    tpl_proj = _template_project(a.plantilla, proj)
    draft, new_meta, rep = builder.build(base, meta, job, tl, style, sound=find_sound,
                                         template_draft=tpl_proj.load() if tpl_proj else None)
    carried = _carry_over(draft, proj.load(), foreign, draft["duration"]) if foreign else 0
    errors, vwarn = validate(draft)
    rep.warnings += vwarn[:10]
    if errors:
        raise MontajeError("El montaje generado no pasa las comprobaciones; no he escrito nada:\n  - "
                           + "\n  - ".join(errors[:20]))
    result = {
        "proyecto": proj.name, "version_capcut": proj.app_version(),
        "duracion_final": fmt_time(rep.duration_s, False), "clips": rep.clips, "subtitulos": rep.captions,
        "titulos": rep.titles, "efectos_sonido": rep.sfx, "broll": rep.overlays, "musica": rep.music,
        "moldes": rep.templates, "proyecto_plantilla": tpl_proj.name if tpl_proj else None,
        "capas_conservadas": carried, "avisos": rep.warnings,
    }
    if a.simular:
        result["simulacion"] = "No se ha escrito nada."
        out(result)
        return
    snap = proj.snapshot(time.strftime("%Y%m%d-%H%M%S"))
    written = proj.write(draft, new_meta, {"plan": str(job.plan_path)})
    result.update({"escrito": written, "copia_de_seguridad": str(snap),
                   "siguiente_paso": "Abre CapCut y el proyecto. Si algo no cuadra: `montaje deshacer`."})
    out(result)


def cmd_deshacer(a) -> None:
    from .capcut.store import capcut_running, find_project

    proj = find_project(a.proyecto)
    if capcut_running() and not a.forzar:
        raise MontajeError("Cierra CapCut antes de deshacer.")
    restored = proj.restore(a.copia)
    out({"proyecto": proj.name, "restaurado_desde": a.copia, "archivos": restored})


# ------------------------------------------------------------------ salidas sin CapCut
def cmd_previsualizar(a) -> None:
    from .plan import Job
    from .preview import render_preview

    job = Job(a.plan)
    dest = Path(a.salida).expanduser() if a.salida else job.dir / "previsualizacion.mp4"
    lut = None
    col = job.plan.get("color") or {}
    if col.get("lut_completa") and col.get("mode") != "hornear":
        lut = Path(col["lut_completa"])
    info("Renderizando vista previa (sin zooms ni efectos de sonido)…")
    render_preview(job, dest, height=a.alto, captions=not a.sin_subtitulos, lut=lut)
    out({"video": str(dest)})


def cmd_srt(a) -> None:
    from .captions import caption_chunks, to_srt
    from .plan import Job

    job = Job(a.plan)
    chunks = caption_chunks(job.timeline(), job.style()["captions"])
    dest = Path(a.salida).expanduser() if a.salida else job.dir / "subtitulos.srt"
    dest.write_text(to_srt(chunks), encoding="utf-8")
    out({"srt": str(dest), "subtitulos": len(chunks)})


def cmd_capitulos(a) -> None:
    from .plan import Job
    from .preview import chapters_text

    job = Job(a.plan)
    text = chapters_text(job)
    (job.dir / "capitulos.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def cmd_sonidos(a) -> None:
    from .sfx import SOUNDS_DIR, generate_all

    files = generate_all(force=a.regenerar)
    out({"carpeta": str(SOUNDS_DIR), "generados": [f.name for f in files],
         "nota": "Puedes sustituirlos dejando tus propios archivos con el mismo nombre."})


def cmd_estilos(a) -> None:
    from .color import LOOKS
    from .plan import STYLES_DIR, list_styles

    out({"estilos": list_styles(), "carpeta": str(STYLES_DIR), "looks_de_color": LOOKS})


def cmd_doctor(a) -> None:
    from .capcut.store import capcut_running, default_drafts_dir, list_projects
    from .transcribe import available_backend

    checks = {"sistema": f"{platform.system()} {platform.machine()}", "python": sys.version.split()[0],
              "montaje": __version__}
    checks["ffmpeg"] = which("ffmpeg") or "FALTA → brew install ffmpeg"
    checks["ffprobe"] = which("ffprobe") or "FALTA → brew install ffmpeg"
    checks["transcripcion"] = available_backend() or "FALTA → ./instalar.sh"
    try:
        import numpy  # noqa: F401
        checks["numpy"] = "ok"
    except ImportError:
        checks["numpy"] = "FALTA → ./instalar.sh"
    d = default_drafts_dir()
    checks["carpeta_capcut"] = str(d) if d.exists() else f"NO ENCONTRADA ({d})"
    if d.exists():
        try:
            pr = list_projects()
            checks["proyectos_capcut"] = len(pr)
            if pr:
                checks["version_capcut"] = pr[0].app_version()
        except MontajeError as e:
            checks["proyectos_capcut"] = str(e)
    checks["capcut_abierto"] = capcut_running()
    out(checks)


# ------------------------------------------------------------------ argparse
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="montaje", description="Edita vídeos largos a cámara en CapCut con ayuda de Claude.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("preparar", help="Transcribe el vídeo y propone los cortes (crea plan.json y revision.md)")
    s.add_argument("video")
    s.add_argument("--estilo", default="opinion", help="opinion | vlog | sobrio | ruta a un .json")
    s.add_argument("--idioma", default="es")
    s.add_argument("--modelo", default="turbo", help="turbo (recomendado) | large | medium | small")
    s.add_argument("--palabras", help="Usar una transcripción ya hecha (JSON con tiempos por palabra)")
    s.add_argument("--salida", help="Carpeta de trabajo (por defecto trabajos/<nombre-del-video>)")
    s.add_argument("--rehacer", action="store_true")
    s.set_defaults(func=cmd_preparar)

    s = sub.add_parser("revisar", help="Valida plan.json, regenera revision.md y muestra el resumen")
    s.add_argument("plan")
    s.set_defaults(func=cmd_revisar)

    s = sub.add_parser("frases", help="Quitar/mantener frases o palabras sin editar el JSON a mano")
    s.add_argument("plan")
    s.add_argument("--quitar", help="F012,F013 o rangos F020-F025")
    s.add_argument("--mantener")
    s.add_argument("--motivo")
    s.add_argument("--cortar-palabras", help="Índices de palabra separados por comas")
    s.add_argument("--recuperar-palabras")
    s.set_defaults(func=cmd_frases)

    s = sub.add_parser("color", help="Analiza y corrige el color (LUT + ajustes de CapCut + antes/después)")
    s.add_argument("objetivo", help="plan.json (o carpeta de trabajo) o directamente un vídeo")
    s.add_argument("--look", help="natural | calido | frio | cine | suave | vivo")
    s.add_argument("--intensidad", type=float, help="0 a 1 (por defecto la del estilo)")
    s.add_argument("--referencia", help="Vídeo cuyo color quieres imitar")
    s.add_argument("--brillo", type=float, help="Forzar brillo (-1 a 1)")
    s.add_argument("--contraste", type=float)
    s.add_argument("--saturacion", type=float)
    s.add_argument("--modo", default="auto", choices=["auto", "lut", "ninguno"],
                   help="auto: brillo/contraste/saturación en cada corte; lut: solo la LUT; ninguno")
    s.add_argument("--hornear", action="store_true", help="Crear un vídeo nuevo con el color aplicado")
    s.set_defaults(func=cmd_color)

    s = sub.add_parser("referencia", help="Analiza un vídeo cuya edición te gusta (ritmo, color, hoja de contactos)")
    s.add_argument("video")
    s.add_argument("--salida")
    s.set_defaults(func=cmd_referencia)

    s = sub.add_parser("fotograma", help="Extrae un fotograma (para revisar encuadre o color)")
    s.add_argument("objetivo", help="plan.json o vídeo")
    s.add_argument("--t", type=float, default=5.0, help="Segundo del vídeo original")
    s.add_argument("--salida")
    s.set_defaults(func=cmd_fotograma)

    s = sub.add_parser("proyectos", help="Lista los proyectos de CapCut")
    s.add_argument("--limite", type=int, default=20)
    s.set_defaults(func=cmd_proyectos)

    s = sub.add_parser("montar", help="Escribe el montaje en un proyecto de CapCut (con copia de seguridad)")
    s.add_argument("plan")
    s.add_argument("--proyecto", required=True, help="Nombre del proyecto en CapCut")
    s.add_argument("--plantilla", help="Proyecto de CapCut con tus moldes de texto (por defecto «Plantilla montaje»)")
    s.add_argument("--simular", action="store_true", help="Calcula todo pero no escribe")
    s.add_argument("--forzar", action="store_true")
    s.set_defaults(func=cmd_montar)

    s = sub.add_parser("deshacer", help="Restaura el proyecto de CapCut desde una copia")
    s.add_argument("--proyecto", required=True)
    s.add_argument("--copia", default="original", help="«original» o el nombre de una copia con fecha")
    s.add_argument("--forzar", action="store_true")
    s.set_defaults(func=cmd_deshacer)

    s = sub.add_parser("previsualizar", help="Render rápido con ffmpeg para revisar el montaje sin CapCut")
    s.add_argument("plan")
    s.add_argument("--salida")
    s.add_argument("--alto", type=int, default=540)
    s.add_argument("--sin-subtitulos", action="store_true")
    s.set_defaults(func=cmd_previsualizar)

    s = sub.add_parser("srt", help="Exporta los subtítulos en .srt (tiempos del montaje final)")
    s.add_argument("plan")
    s.add_argument("--salida")
    s.set_defaults(func=cmd_srt)

    s = sub.add_parser("capitulos", help="Capítulos para la descripción de YouTube")
    s.add_argument("plan")
    s.set_defaults(func=cmd_capitulos)

    s = sub.add_parser("sonidos", help="Genera los efectos de sonido básicos en sonidos/")
    s.add_argument("--regenerar", action="store_true")
    s.set_defaults(func=cmd_sonidos)

    s = sub.add_parser("estilos", help="Lista los estilos y looks de color disponibles")
    s.set_defaults(func=cmd_estilos)

    s = sub.add_parser("doctor", help="Comprueba que todo está instalado")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
    except MontajeError as e:
        warn(str(e))
        return 1
    except KeyboardInterrupt:
        warn("Cancelado.")
        return 130
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
