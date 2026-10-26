"""Vista previa con ffmpeg (sin CapCut) y análisis de vídeos de referencia."""

from __future__ import annotations

import statistics
from pathlib import Path

from .captions import caption_chunks, to_srt
from .media import contact_sheet, probe, run, scene_cuts
from .util import fmt_time, warn


def _select_expr(ranges: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges) or "0"


def render_preview(job, out: Path, height: int = 540, captions: bool = True, lut: Path | None = None) -> Path:
    """Renderiza el montaje en baja calidad para revisarlo rápido (sin zooms ni sonidos)."""
    tl = job.timeline()
    style = job.style()
    sections = [[(c.src_start, c.src_end) for c in tl.clips if c.section == sec] for sec in ("hook", "main")]
    sections = [s for s in sections if s]
    parts, labels = [], []
    n = len(sections)
    parts.append(f"[0:v]split={n}" + "".join(f"[vi{i}]" for i in range(n)))
    parts.append(f"[0:a]asplit={n}" + "".join(f"[ai{i}]" for i in range(n)))
    for i, rng in enumerate(sections):
        e = _select_expr(rng)
        parts.append(f"[vi{i}]select='{e}',setpts=N/FRAME_RATE/TB[v{i}]")
        parts.append(f"[ai{i}]aselect='{e}',asetpts=N/SR/TB[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    vf = []
    if lut and lut.exists():
        vf.append(f"lut3d=file='{lut}'")
    vf.append(f"scale=-2:{height}")
    srt = None
    if captions and style["captions"].get("enabled", True):
        srt = out.with_suffix(".srt")
        srt.write_text(to_srt(caption_chunks(tl, style["captions"])), encoding="utf-8")
        vf.append(f"subtitles='{srt}':force_style='Fontsize=18,Outline=2,MarginV=24'")
    parts.append("".join(labels) + f"concat=n={n}:v=1:a=1[cv][ca]")
    parts.append(f"[cv]{','.join(vf)}[outv]")
    script = out.with_suffix(".filtro.txt")
    script.write_text(";\n".join(parts), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(job.source), "-filter_complex_script", str(script),
         "-map", "[outv]", "-map", "[ca]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-c:a", "aac", "-b:a", "128k", str(out)], capture=False)
    script.unlink(missing_ok=True)
    return out


def analyze_reference(video: Path, out_dir: Path) -> dict:
    """Mide el ritmo y el color de un vídeo cuya edición te gusta."""
    from .color import analyze_video

    info = probe(video)
    cuts = scene_cuts(video)
    bounds = [0.0] + cuts + [info.duration]
    shots = [b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0.05]
    _, st = analyze_video(video, count=16)
    sheet = contact_sheet(video, out_dir / f"{video.stem}_hoja.jpg")
    res = {
        "archivo": str(video),
        "duracion": fmt_time(info.duration, False),
        "formato": f"{info.width}x{info.height} @ {info.fps}fps",
        "cortes_por_minuto": round(len(cuts) / max(info.duration / 60, 1e-6), 1),
        "plano_medio_s": round(statistics.mean(shots), 2) if shots else None,
        "plano_mediana_s": round(statistics.median(shots), 2) if shots else None,
        "color": {k: round(v, 4) if isinstance(v, float) else v for k, v in st.items()},
        "hoja_de_contactos": str(sheet),
    }
    return res


def chapters_text(job) -> str:
    """Capítulos para YouTube: el primero en 00:00 y cada uno de al menos 10 segundos."""
    tl = job.timeline()
    chs = sorted((tl.map_src(job.at(c)), c.get("title", "")) for c in job.plan.get("chapters") or [])
    if not chs:
        return ""
    if chs[0][0] < 10:
        chs[0] = (0.0, chs[0][1])
    else:
        chs.insert(0, (0.0, "Intro"))
    lines, last = [], None
    for t, title in chs:
        if last is not None and t - last < 10:
            warn(f"Capítulo «{title}» a menos de 10 s del anterior: YouTube lo ignoraría, lo omito.")
            continue
        lines.append(f"{fmt_time(t, False)} {title}")
        last = t
    return "\n".join(lines)
