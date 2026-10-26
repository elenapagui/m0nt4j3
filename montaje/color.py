"""Corrección de color.

1. Se analizan fotogramas repartidos por el vídeo (exposición, contraste, saturación, balance).
2. Se calcula una corrección en dos partes:
   - Ajustes de CapCut (brillo, contraste, saturación): se escriben solos en cada corte.
   - Una LUT (.cube) con el balance de blancos + el «look» (o igualar a un vídeo de referencia):
     se carga en CapCut una vez (Ajustar → LUT) y se puede retocar o quitar.
3. Se genera una imagen de antes/después para revisarla (Claude puede verla).
También puede «hornear» todo el color en un archivo nuevo con ffmpeg.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .media import probe, run, sample_frames
from .util import MontajeError, clamp

LOOKS = {
    "natural": "Solo corrige: balance de blancos neutro y exposición.",
    "calido": "Tonos cálidos y acogedores (vlog, lifestyle).",
    "frio": "Tonos fríos y limpios (tecnología, estudio).",
    "cine": "Sombras ligeramente verdeazuladas, piel cálida, contraste suave en S.",
    "suave": "Negros levantados y contraste bajo, aspecto de película/pastel.",
    "vivo": "Colores más saturados y con más pegada (opinión, redes).",
}
LOOK_TARGETS = {  # (luminancia media objetivo, saturación objetivo)
    "natural": (0.45, 0.27), "calido": (0.46, 0.30), "frio": (0.45, 0.25),
    "cine": (0.42, 0.26), "suave": (0.47, 0.22), "vivo": (0.46, 0.34),
}

LUMA = np.array([0.2126, 0.7152, 0.0722])


# ---------------------------------------------------------------- análisis
def stats(frames: list[np.ndarray]) -> dict:
    px = np.concatenate([f.reshape(-1, 3) for f in frames]).astype(np.float64) / 255.0
    y = px @ LUMA
    mx, mn = px.max(1), px.min(1)
    sat_all = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0)
    lit = (y > 0.08) & (y < 0.95)  # en zonas muy oscuras la saturación es solo ruido
    sat = sat_all[lit] if lit.sum() > 500 else sat_all
    neutral = (sat_all < 0.25) & (y > 0.15) & (y < 0.85)
    ref = px[neutral] if neutral.sum() > 500 else px
    lab = rgb_to_lab(px)
    # saturación tal y como quedará tras corregir el balance de blancos (una dominante la «infla»)
    gray = ref.mean()
    wb = np.clip(1 + (gray / np.maximum(ref.mean(0), 1e-3) - 1) * 0.8, 0.85, 1.15)
    px_wb = np.clip(px * wb, 0, 1)
    mx2, mn2 = px_wb.max(1), px_wb.min(1)
    sat_wb = np.where(mx2 > 1e-6, (mx2 - mn2) / np.maximum(mx2, 1e-6), 0)
    sat_wb = sat_wb[lit] if lit.sum() > 500 else sat_wb
    return {
        "luma_mean": float(y.mean()),
        "luma_p2": float(np.percentile(y, 2)),
        "luma_p98": float(np.percentile(y, 98)),
        "sat_mean": float(sat.mean()),
        "sat_mean_wb": float(sat_wb.mean()),
        "neutral_rgb": [float(v) for v in ref.mean(0)],
        "warmth": float(ref[:, 0].mean() - ref[:, 2].mean()),
        "clipped_highlights": float((y > 0.98).mean()),
        "crushed_shadows": float((y < 0.02).mean()),
        "lab_mean": [float(v) for v in lab.mean(0)],
        "lab_std": [float(v) for v in lab.std(0)],
    }


def correction(st: dict, look: str = "natural", strength: float = 0.7, ref: dict | None = None,
               manual: dict | None = None) -> dict:
    if look not in LOOKS:
        raise MontajeError(f"Look desconocido «{look}». Opciones: {', '.join(LOOKS)}")
    t_luma, t_sat = LOOK_TARGETS[look]
    if ref:  # con referencia, los objetivos son los del vídeo que te gusta
        t_luma, t_sat = ref["luma_mean"], ref.get("sat_mean_wb", ref["sat_mean"])
    k = clamp(strength, 0.0, 1.0)
    rng = st["luma_p98"] - st["luma_p2"]
    adj = {
        "brightness": round(clamp((t_luma - st["luma_mean"]) * 2.0, -0.35, 0.35) * k, 3),
        "contrast": round(clamp((0.82 - rng) * 0.7, -0.2, 0.3) * k, 3),
        # quitar saturación apaga la piel: se baja poco y solo si sobra de verdad
        "saturation": round(clamp((t_sat - st.get("sat_mean_wb", st["sat_mean"])) * 1.6, -0.15, 0.35) * k, 3),
    }
    if st["clipped_highlights"] > 0.03 and adj["brightness"] > 0:  # no quemar más las luces
        adj["brightness"] = round(adj["brightness"] * 0.4, 3)
    r, g, b = st["neutral_rgb"]
    gray = (r + g + b) / 3
    # objetivo: casi neutro pero un pelín cálido (un blanco «perfecto» hace la piel fría y apagada)
    target = (gray * 1.015, gray, gray * 0.975)
    gains = [clamp(1 + (t / max(c, 1e-3) - 1) * 0.8 * k, 0.85, 1.15) for t, c in zip(target, (r, g, b))]
    if ref:
        gains = [1.0, 1.0, 1.0]  # el traspaso de color ya corrige el balance
    for key, v in (manual or {}).items():
        if v is not None and key in adj:
            adj[key] = round(clamp(float(v), -1, 1), 3)
    return {"capcut": adj, "wb_gains": [round(g, 4) for g in gains], "look": look, "strength": k,
            "transfer": {"src": st, "ref": ref, "amount": round(0.75 * k + 0.1, 3)} if ref else None}


# ---------------------------------------------------------------- transformaciones de color
def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
_MI = np.linalg.inv(_M)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def rgb_to_lab(rgb):
    xyz = srgb_to_linear(rgb) @ _M.T / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def lab_to_rgb(lab):
    fy = (lab[:, 0] + 16) / 116
    fx, fz = fy + lab[:, 1] / 500, fy - lab[:, 2] / 200
    f = np.stack([fx, fy, fz], 1)
    xyz = np.where(f > 0.2069, f ** 3, (f - 16 / 116) / 7.787) * _WHITE
    return linear_to_srgb(xyz @ _MI.T)


def apply_look(rgb, look: str, k: float):
    y = (rgb @ LUMA)[:, None]
    if look == "calido":
        rgb = rgb * (1 + k * np.array([0.04, 0.01, -0.05]))
        rgb = rgb + 0.015 * k * (1 - rgb)
    elif look == "frio":
        rgb = rgb * (1 + k * np.array([-0.04, 0.0, 0.05]))
    elif look == "cine":
        shadows = np.clip(1 - y * 2, 0, 1)
        highs = np.clip(y * 2 - 1, 0, 1)
        rgb = rgb + k * (shadows * np.array([-0.025, 0.01, 0.03]) + highs * np.array([0.03, 0.005, -0.03]))
        rgb = 0.5 + (rgb - 0.5) * (1 + 0.08 * k)
        y2 = (rgb @ LUMA)[:, None]
        rgb = y2 + (rgb - y2) * (1 - 0.06 * k)
    elif look == "suave":
        rgb = 0.04 * k + rgb * (1 - 0.07 * k)
        y2 = (rgb @ LUMA)[:, None]
        rgb = y2 + (rgb - y2) * (1 - 0.1 * k)
    elif look == "vivo":
        y2 = (rgb @ LUMA)[:, None]
        rgb = y2 + (rgb - y2) * (1 + 0.12 * k)
    return np.clip(rgb, 0, 1)


def apply_transfer(rgb, tr: dict):
    src, ref, amt = tr["src"], tr["ref"], tr["amount"]
    lab = rgb_to_lab(rgb)
    mu_s, sd_s = np.array(src["lab_mean"]), np.maximum(np.array(src["lab_std"]), 1e-3)
    mu_r, sd_r = np.array(ref["lab_mean"]), np.array(ref["lab_std"])
    ratio = np.clip(sd_r / sd_s, 0.7, 1.4)
    out = (lab - mu_s) * ratio + mu_r
    return np.clip(lab_to_rgb(lab + (out - lab) * amt), 0, 1)


def apply_capcut_approx(rgb, adj: dict):
    """Aproximación de los deslizadores de CapCut (solo para la vista previa y el horneado)."""
    b, c, s = adj.get("brightness", 0), adj.get("contrast", 0), adj.get("saturation", 0)
    rgb = np.clip(rgb, 0, 1) ** (2 ** (-b * 0.9))
    rgb = 0.5 + (rgb - 0.5) * (1 + c * 0.6)
    y = (rgb @ LUMA)[:, None]
    return np.clip(y + (rgb - y) * (1 + s), 0, 1)


def grade_fn(corr: dict, include_tone: bool):
    def fn(rgb):
        out = rgb * np.array(corr["wb_gains"])
        if corr.get("transfer"):
            out = apply_transfer(np.clip(out, 0, 1), corr["transfer"])
        out = apply_look(np.clip(out, 0, 1), corr["look"], corr["strength"])
        if include_tone:
            out = apply_capcut_approx(out, corr["capcut"])
        return np.clip(out, 0, 1)
    return fn


def write_cube(path: Path, fn, size: int = 33, title: str = "montaje") -> Path:
    g = np.linspace(0, 1, size)
    # en .cube el rojo es el índice que cambia más rápido
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([r.ravel(), gg.ravel(), b.ravel()], 1)
    out = fn(rgb)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


# ---------------------------------------------------------------- salidas
def before_after(frames: list[np.ndarray], fn, out: Path, n: int = 3) -> Path:
    """Imagen con n pares (antes | después) para revisar la corrección."""
    picks = [frames[int(i * (len(frames) - 1) / max(n - 1, 1))] for i in range(n)] if len(frames) > 1 else frames
    rows = []
    for f in picks:
        h, w, _ = f.shape
        after = (fn(f.reshape(-1, 3).astype(np.float64) / 255.0).reshape(h, w, 3) * 255).round().astype(np.uint8)
        sep = np.full((h, 4, 3), 255, np.uint8)
        rows.append(np.concatenate([f, sep, after], 1))
        rows.append(np.full((4, rows[-1].shape[1], 3), 255, np.uint8))
    img = np.concatenate(rows[:-1], 0)
    ppm = out.with_suffix(".ppm")
    with open(ppm, "wb") as fh:
        fh.write(f"P6 {img.shape[1]} {img.shape[0]} 255\n".encode())
        fh.write(img.tobytes())
    run(["ffmpeg", "-y", "-v", "error", "-i", str(ppm), "-vf", "scale=iw*2:-1:flags=neighbor", "-q:v", "3", str(out)])
    ppm.unlink(missing_ok=True)
    return out


def analyze_video(video: Path, count: int = 12):
    frames = sample_frames(video, count=count)
    return frames, stats(frames)


def bake(video: Path, lut: Path, out: Path) -> Path:
    """Aplica la LUT completa (con brillo/contraste/saturación) y guarda un vídeo nuevo."""
    info = probe(video)
    vcodec = ["-c:v", "hevc_videotoolbox", "-b:v", "60M", "-tag:v", "hvc1"] if _has_encoder("hevc_videotoolbox") \
        else ["-c:v", "libx264", "-crf", "14", "-preset", "medium"]
    run(["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(video), "-vf", f"lut3d=file='{lut}'",
         *vcodec, "-pix_fmt", "yuv420p", "-c:a", "copy", "-r", str(info.fps or 30), str(out)], capture=False)
    return out


def _has_encoder(name: str) -> bool:
    res = run(["ffmpeg", "-hide_banner", "-encoders"], check=False)
    return name in (res.stdout or "")
