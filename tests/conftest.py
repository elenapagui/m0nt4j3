import copy
import json
import shutil
from pathlib import Path

import pytest

from montaje.media import MediaInfo
from montaje.util import read_json, write_json

FIXTURE = Path(__file__).parent / "fixtures" / "capcut-8.7"
NESTED = "Timelines/C1EFCDF2-B885-48ff-A2F8-C33A6C4F4A52/draft_content.json"
SOURCE_DURATION = 89.3  # duración del vídeo del borrador de ejemplo

SENTENCES = [
    "Hola a todos, bienvenidos al canal.",
    "Hoy quiero hablaros de por qué dejé mi trabajo",
    "Hoy quiero hablaros de por qué dejé mi trabajo en una multinacional.",
    "Eh, la verdad es que no fue fácil.",
    "Durante cinco años trabajé allí y aprendí muchísimo.",
    "Pero había algo que no encajaba.",
    "Perdón, otra vez.",
    "Pero había algo que no encajaba conmigo.",
    "El primer motivo fue el tiempo.",
    "Trabajaba diez horas al día y no tenía vida.",
    "Em, el segundo motivo fue el dinero, aunque parezca raro.",
    "Ganaba bien, pero gastaba el 80% en cosas que no necesitaba.",
    "Y el tercer motivo, el más importante, fue la libertad.",
    "Quería decidir yo qué hacer con mis días.",
    "Así que un lunes entré en la oficina y presenté mi dimisión.",
    "Si te ha gustado, suscríbete y nos vemos en el próximo vídeo.",
]


def fake_words() -> list[dict]:
    words, t = [], 0.8
    for i, s in enumerate(SENTENCES):
        for tok in s.split():
            d = 0.12 + 0.045 * len(tok)
            words.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3), "p": 0.95})
            t += d + 0.08
        t += 1.6 if i in (1, 5) else 0.9
    return words


def fake_media_info(path: Path) -> MediaInfo:
    p = Path(path)
    if p.suffix in (".jpg", ".png"):
        return MediaInfo(path=p, duration=5.0, width=1080, height=1350, has_video=True)
    if p.suffix in (".mp4", ".mov"):
        return MediaInfo(path=p, duration=12.0, width=1920, height=1080, fps=30, has_video=True, has_audio=True)
    return MediaInfo(path=p, duration=20.0 if "musica" in p.name else 0.6, has_audio=True)


@pytest.fixture
def words():
    return fake_words()


@pytest.fixture
def job(tmp_path, words):
    """Carpeta de trabajo con transcripción + plan, como la deja `montaje preparar`."""
    from montaje.analyze import analyze
    from montaje.plan import Job, new_plan

    video = tmp_path / "test-montaggio-C0148.MP4"
    video.write_bytes(b"")
    jd = tmp_path / "trabajo"
    jd.mkdir()
    write_json(jd / "transcripcion.json", {"words": words, "duration": SOURCE_DURATION, "silences": []})
    write_json(jd / "plan.json", new_plan(video, analyze(words), "opinion"))
    for name in ("musica.mp3", "broll.jpg"):
        (jd / name).write_bytes(b"")
    return Job(jd / "plan.json")


@pytest.fixture
def drafts(tmp_path, monkeypatch):
    """Carpeta de proyectos de CapCut falsa con el borrador real de CapCut 8.7."""
    d = tmp_path / "drafts"
    shutil.copytree(FIXTURE, d / "Mi video")
    meta = read_json(d / "Mi video" / "draft_meta_info.json")
    meta["draft_name"] = "Mi video"
    write_json(d / "Mi video" / "draft_meta_info.json", meta)
    monkeypatch.setenv("CAPCUT_DRAFTS_DIR", str(d))
    monkeypatch.setenv("MONTAJE_COPIAS", str(tmp_path / "copias"))
    return d


def load_fixture_draft() -> dict:
    return copy.deepcopy(json.loads((FIXTURE / NESTED).read_text()))
