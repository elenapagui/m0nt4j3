"""Todo lo que necesita ffmpeg/ffprobe: información, audio, silencios y fotogramas."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .util import MontajeError, run

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".flac", ".ogg"}


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    has_video: bool = False

    @property
    def kind(self) -> str:
        ext = self.path.suffix.lower()
        if ext in IMAGE_EXT:
            return "photo"
        if not self.has_video or ext in AUDIO_EXT:
            return "audio"
        return "video"


def probe(path: str | Path) -> MediaInfo:
    path = Path(path).expanduser()
    if not path.exists():
        raise MontajeError(f"No existe el archivo: {path}")
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]).stdout
    data = json.loads(out)
    info = MediaInfo(path=path.resolve(), duration=float(data.get("format", {}).get("duration") or 0))
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info.has_video:
            if st.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
            rotation = _rotation(st)
            if rotation in (90, 270):
                w, h = h, w
            info.width, info.height = w, h
            info.fps = _fps(st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1")
            if not info.duration and st.get("duration"):
                info.duration = float(st["duration"])
        elif st.get("codec_type") == "audio":
            info.has_audio = True
    if path.suffix.lower() in IMAGE_EXT:
        info.duration = 5.0
    return info


def _fps(rate: str) -> float:
    try:
        n, d = rate.split("/")
        return round(float(n) / float(d), 3) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    rot = stream.get("tags", {}).get("rotate")
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    try:
        return abs(int(float(rot or 0))) % 360
    except ValueError:
        return 0


def extract_audio(video: Path, wav: Path) -> Path:
    """Audio mono a 16 kHz: lo que piden Whisper y el detector de silencios."""
    wav.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    return wav


def detect_silences(audio: Path, noise_db: float = -35.0, min_dur: float = 0.3) -> list[tuple[float, float]]:
    res = run(["ffmpeg", "-v", "info", "-nostats", "-i", str(audio), "-af",
               f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"])
    starts, out = [], []
    for line in res.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            starts.append(max(0.0, float(m.group(1))))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and starts:
            out.append((starts.pop(), float(m.group(1))))
    if starts:  # silencio hasta el final
        out.append((starts.pop(), probe(audio).duration))
    merged: list[tuple[float, float]] = []
    for a, b in out:  # un chasquido de 0,1 s no parte un silencio en dos
        if merged and a - merged[-1][1] < 0.12:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def loudness_floor(audio: Path) -> float:
    """Umbral de silencio (dB) adaptado a la grabación: entre el ruido de fondo y la voz.

    Se mide el volumen en ventanas de 50 ms: el percentil 10 es el ruido de fondo y el 80, la voz.
    """
    import wave

    import numpy as np

    try:
        with wave.open(str(audio), "rb") as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, OSError):
        return -40.0
    n = sr // 20
    if len(x) < n * 20:
        return -40.0
    frames = x[: len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    floor, voice = np.percentile(db, 10), np.percentile(db, 80)
    return round(float(np.clip(floor + 0.4 * (voice - floor), -55.0, -28.0)), 1)


def grab_frame(video: Path, t: float, out: Path, width: int = 960) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
    return out


def sample_frames(video: Path, count: int = 12, size: tuple[int, int] = (192, 108)):
    """Devuelve una lista de fotogramas (numpy uint8 HxWx3) repartidos por el vídeo."""
    import numpy as np

    info = probe(video)
    w, h = size
    if info.height > info.width:  # vídeo vertical
        w, h = h, w
    frames = []
    if info.kind == "photo":
        times = [0.0]
    else:
        span = max(info.duration, 0.1)
        times = [span * (i + 0.5) / count for i in range(count)]
    for t in times:
        cmd = ["ffmpeg", "-v", "error"]
        if info.kind != "photo":
            cmd += ["-ss", f"{t:.3f}"]
        cmd += ["-i", str(video), "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        res = run_bytes(cmd)
        if len(res) == w * h * 3:
            frames.append(np.frombuffer(res, dtype=np.uint8).reshape(h, w, 3))
    if not frames:
        raise MontajeError(f"No he podido leer fotogramas de {video}")
    return frames


def run_bytes(cmd: list[str]) -> bytes:
    import subprocess

    try:
        return subprocess.run(cmd, capture_output=True, check=True).stdout
    except FileNotFoundError as e:
        raise MontajeError("No encuentro ffmpeg. Ejecuta `montaje doctor`.") from e
    except subprocess.CalledProcessError:
        return b""


def scene_cuts(video: Path, threshold: float = 0.3) -> list[float]:
    """Instantes donde cambia el plano (para analizar vídeos de referencia)."""
    res = run(["ffmpeg", "-v", "info", "-nostats", "-i", str(video), "-an", "-vf",
               f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], check=False)
    return [float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)", res.stderr)]


def contact_sheet(video: Path, out: Path, cols: int = 4, rows: int = 4) -> Path:
    info = probe(video)
    n = cols * rows
    every = max(info.duration / n, 0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
         f"fps=1/{every:.3f},scale=480:-2,tile={cols}x{rows}:padding=4:color=white", "-frames:v", "1",
         "-q:v", "3", str(out)])
    return out
