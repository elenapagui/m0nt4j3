"""Dónde guarda CapCut los proyectos y cómo leerlos/escribirlos sin romperlos.

Un proyecto de CapCut es una carpeta con uno o varios JSON que describen la misma
línea de tiempo («espejos»). Según la versión, la app lee:
  - draft_info.json (macOS) o draft_content.json (Windows) en la raíz,
  - Timelines/<main_timeline_id>/draft_info.json en las versiones nuevas (8.7+/9.x).
Escribimos el mismo contenido en todos los espejos legibles para que dé igual cuál lea.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from ..util import MontajeError, normalize, read_json, write_json

DOC_NAMES = ("draft_info.json", "draft_content.json", "template-2.tmp")


def default_drafts_dir() -> Path:
    env = os.environ.get("CAPCUT_DRAFTS_DIR")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if platform.system() == "Windows":
        return home / "AppData/Local/CapCut/User Data/Projects/com.lveditor.draft"
    return home / "Movies/CapCut/User Data/Projects/com.lveditor.draft"


def backups_root() -> Path:
    return Path(os.environ.get("MONTAJE_COPIAS", Path.home() / "Movies/Montaje/copias")).expanduser()


def capcut_running() -> bool:
    try:
        if platform.system() == "Darwin":
            res = subprocess.run(["pgrep", "-if", "CapCut.app/Contents/MacOS"], capture_output=True, text=True)
            return res.returncode == 0 and bool(res.stdout.strip())
        if platform.system() == "Windows":
            res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"], capture_output=True, text=True)
            return "capcut.exe" in res.stdout.lower()
    except FileNotFoundError:
        pass
    return False


def _parse_doc(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None  # p. ej. espejos cifrados de JianYing
    return data if isinstance(data, dict) and "tracks" in data and "materials" in data else None


def segment_ids(draft: dict) -> list[str]:
    return [s["id"] for tr in draft.get("tracks", []) for s in tr.get("segments", []) if s.get("id")]


def fingerprint(draft: dict, ids: set[str] | None = None) -> str:
    """Huella de los tiempos de los segmentos (de todos, o solo de los ids indicados).

    Sirve para saber si alguien ha retocado el montaje en CapCut: abrir y cerrar CapCut
    reescribe el archivo pero no cambia esta huella.
    """
    parts = []
    for tr in draft.get("tracks", []):
        for s in tr.get("segments", []):
            if ids is None or s.get("id") in ids:
                r = s.get("target_timerange") or {}
                parts.append((s.get("id"), r.get("start", 0), r.get("duration", 0)))
    return hashlib.sha1(json.dumps(sorted(parts)).encode()).hexdigest()[:16]


def foreign_segments(draft: dict, ids: set[str]) -> list[tuple[dict, dict]]:
    """(pista, segmento) que no creó montaje: lo que has añadido tú en CapCut."""
    return [(tr, s) for tr in draft.get("tracks", []) for s in tr.get("segments", []) if s.get("id") not in ids]


class Project:
    def __init__(self, path: Path):
        self.dir = Path(path)
        self.meta_path = self.dir / "draft_meta_info.json"
        self.meta = read_json(self.meta_path) if self.meta_path.exists() else None
        self.docs = self._find_docs()
        if not self.docs:
            raise MontajeError(f"La carpeta {self.dir} no parece un proyecto de CapCut legible")
        self.primary = self.docs[0]

    @property
    def name(self) -> str:
        return (self.meta or {}).get("draft_name") or self.dir.name

    @property
    def locked(self) -> bool:
        return (self.dir / ".locked").exists()

    def _find_docs(self) -> list[Path]:
        nested: list[Path] = []
        pointer = self.dir / "Timelines" / "project.json"
        if pointer.exists():
            try:
                main_id = read_json(pointer).get("main_timeline_id")
            except (OSError, json.JSONDecodeError):
                main_id = None
            if main_id and (self.dir / "Timelines" / main_id).is_dir():
                nested = [self.dir / "Timelines" / main_id / n for n in DOC_NAMES]
        root = [self.dir / n for n in DOC_NAMES]
        # el primero de la lista es el que se lee; se escriben todos los legibles
        return [p for p in nested + root if p.exists() and _parse_doc(p) is not None]

    def load(self) -> dict:
        return _parse_doc(self.primary)  # type: ignore[return-value]

    def app_version(self) -> str:
        d = self.load()
        for key in ("last_modified_platform", "platform"):
            v = (d.get(key) or {}).get("app_version")
            if v:
                return v
        return "?"

    def duration(self) -> float:
        return (self.load().get("duration") or 0) / 1e6

    def relative(self, p: Path) -> str:
        return str(p.relative_to(self.dir))

    # ---------- copias de seguridad ----------
    def backup_dir(self) -> Path:
        return backups_root() / self.dir.name

    def snapshot(self, label: str) -> Path:
        dest = self.backup_dir() / label
        dest.mkdir(parents=True, exist_ok=True)
        for p in self.docs + ([self.meta_path] if self.meta_path.exists() else []):
            target = dest / self.relative(p)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        pointer = self.dir / "Timelines" / "project.json"
        if pointer.exists():
            (dest / "Timelines").mkdir(exist_ok=True)
            shutil.copy2(pointer, dest / "Timelines" / "project.json")
        return dest

    def identity(self) -> str:
        return str((self.meta or {}).get("draft_id") or self.load().get("id") or "")

    def original_snapshot(self) -> Path:
        """La primera copia: el proyecto tal y como lo dejaste antes del primer montaje."""
        dest = self.backup_dir() / "original"
        id_file = self.backup_dir() / "proyecto_id.txt"
        if dest.exists() and id_file.exists() and id_file.read_text().strip() != self.identity():
            # es otro proyecto con el mismo nombre de carpeta: se aparta la copia antigua
            old = self.backup_dir().with_name(f"{self.dir.name}__anterior_{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.move(str(self.backup_dir()), str(old))
        if not (dest / self.relative(self.primary)).exists():
            self.snapshot("original")
            id_file.write_text(self.identity())
        return dest

    def load_original(self) -> dict:
        snap = self.original_snapshot() / self.relative(self.primary)
        data = _parse_doc(snap)
        if data is None:
            raise MontajeError(f"La copia original está dañada: {snap}")
        return data

    def state_path(self) -> Path:
        return self.backup_dir() / "estado.json"

    def state(self) -> dict:
        p = self.state_path()
        return read_json(p) if p.exists() else {}

    def changes_since_last_build(self) -> tuple[bool, list[tuple[dict, dict]]]:
        """(¿has retocado lo que montamos?, segmentos que has añadido tú)."""
        st = self.state()
        if not st.get("segment_ids"):
            return False, []
        ids = set(st["segment_ids"])
        current = self.load()
        return fingerprint(current, ids) != st.get("fingerprint"), foreign_segments(current, ids)

    # ---------- escritura ----------
    def write(self, draft: dict, meta: dict | None, extra_state: dict | None = None) -> list[str]:
        written = []
        for p in self.docs:
            original = _parse_doc(p) or {}
            doc = copy.deepcopy(draft)
            if "id" in original:  # cada espejo conserva su propio id
                doc["id"] = original["id"]
            if "update_time" in doc:
                doc["update_time"] = int(time.time() * 1e6)
            _write_compact(p, doc)
            written.append(self.relative(p))
        if meta is not None and self.meta_path.exists():
            meta = copy.deepcopy(meta)
            if "tm_duration" in meta:
                meta["tm_duration"] = draft.get("duration", meta["tm_duration"])
            if "tm_draft_modified" in meta:
                meta["tm_draft_modified"] = int(time.time() * 1e6)
            _write_compact(self.meta_path, meta)
            written.append(self.relative(self.meta_path))
        ids = segment_ids(draft)
        state = {"fingerprint": fingerprint(draft, set(ids)), "segment_ids": ids,
                 "written_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        state.update(extra_state or {})
        write_json(self.state_path(), state)
        return written

    def restore(self, label: str = "original") -> list[str]:
        src = self.backup_dir() / label
        if not src.exists():
            raise MontajeError(f"No hay copia «{label}» de este proyecto en {self.backup_dir()}")
        restored = []
        for f in src.rglob("*"):
            if f.is_file():
                target = self.dir / f.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                restored.append(str(f.relative_to(src)))
        if self.state_path().exists():
            self.state_path().unlink()
        return restored


def _write_compact(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".montaje-tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def list_projects(drafts_dir: Path | None = None) -> list[Project]:
    drafts_dir = drafts_dir or default_drafts_dir()
    if not drafts_dir.exists():
        raise MontajeError(
            f"No encuentro la carpeta de proyectos de CapCut: {drafts_dir}\n"
            "Abre CapCut una vez, crea un proyecto y ciérralo. Si tus proyectos están en otro sitio "
            "(CapCut → Ajustes → Ubicación de borradores), indícalo con CAPCUT_DRAFTS_DIR."
        )
    projects = []
    for d in sorted(drafts_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir() and not d.name.startswith("."):
            try:
                projects.append(Project(d))
            except MontajeError:
                continue
    return projects


def find_project(name: str, drafts_dir: Path | None = None) -> Project:
    p = Path(name).expanduser()
    if p.is_dir():
        return Project(p)
    target = normalize(name)
    projects = list_projects(drafts_dir)
    exact = [pr for pr in projects if normalize(pr.name) == target or normalize(pr.dir.name) == target]
    if len(exact) == 1:
        return exact[0]
    partial = [pr for pr in projects if target in normalize(pr.name)]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(f"«{pr.name}»" for pr in projects[:15])
    if not exact and not partial:
        raise MontajeError(f"No encuentro el proyecto «{name}» en CapCut. Proyectos: {names}")
    raise MontajeError(f"Hay varios proyectos que encajan con «{name}». Escribe el nombre completo: {names}")
