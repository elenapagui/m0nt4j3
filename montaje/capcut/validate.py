"""Comprobaciones de integridad antes de escribir un proyecto (si algo falla, no se escribe)."""

from __future__ import annotations

import json
from pathlib import Path

from ..util import utf16_len


def validate(draft: dict, check_files: bool = True) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    mats: dict[str, tuple[str, dict]] = {}
    for cat, items in (draft.get("materials") or {}).items():
        if not isinstance(items, list):
            continue
        for m in items:
            if not isinstance(m, dict) or "id" not in m:
                continue
            if m["id"] in mats:
                errors.append(f"material duplicado: {m['id']} ({cat})")
            mats[m["id"]] = (cat, m)
    seg_ids: set[str] = set()
    fps = float(draft.get("fps") or 30)
    max_end = 0
    for ti, tr in enumerate(draft.get("tracks", [])):
        segs = sorted(tr.get("segments", []), key=lambda s: s["target_timerange"]["start"])
        prev_end = -1
        for s in segs:
            sid = s.get("id")
            if sid in seg_ids:
                errors.append(f"segmento duplicado: {sid}")
            seg_ids.add(sid)
            r = s["target_timerange"]
            if r["duration"] <= 0:
                errors.append(f"pista {ti}: segmento {sid} con duración {r['duration']}")
            if r["start"] < prev_end:
                errors.append(f"pista {ti} ({tr.get('type')}): solape en {r['start'] / 1e6:.3f}s")
            prev_end = r["start"] + r["duration"]
            max_end = max(max_end, prev_end)
            if s.get("material_id") not in mats:
                errors.append(f"segmento {sid}: material {s.get('material_id')} no existe")
                continue
            for ref in s.get("extra_material_refs") or []:
                if ref not in mats:
                    errors.append(f"segmento {sid}: referencia {ref} no existe")
            cat, m = mats[s["material_id"]]
            src = s.get("source_timerange")
            if cat in ("videos", "audios") and src:
                if src["start"] < 0 or src["start"] + src["duration"] > m.get("duration", 0) + 2_000:
                    errors.append(f"segmento {sid}: pide {(src['start'] + src['duration']) / 1e6:.2f}s de un "
                                  f"archivo de {m.get('duration', 0) / 1e6:.2f}s")
                if check_files and m.get("path") and not Path(m["path"]).exists():
                    warnings.append(f"no encuentro el archivo {m['path']}")
            if tr.get("type") == "audio" and s.get("clip") is not None:
                errors.append(f"segmento de audio {sid} con «clip» (CapCut se cierra al abrirlo)")
            if tr.get("type") == "video":
                frame = 1e6 / fps
                off = (r["start"] / frame) % 1
                if min(off, 1 - off) > 0.02:
                    warnings.append(f"segmento {sid} no empieza en un fotograma exacto")
            if cat == "texts":
                try:
                    content = json.loads(m.get("content") or "{}")
                except json.JSONDecodeError:
                    errors.append(f"texto {m['id']}: contenido no es JSON")
                    continue
                n = utf16_len(content.get("text", ""))
                pos = 0
                for st in content.get("styles", []):
                    a, b = st.get("range", [0, 0])
                    if a != pos or b < a or b > n:
                        errors.append(f"texto «{content.get('text', '')[:30]}»: rangos de estilo incoherentes")
                        break
                    pos = b
                if content.get("styles") and pos != n:
                    errors.append(f"texto «{content.get('text', '')[:30]}»: los estilos no cubren todo el texto")
    if draft.get("duration", 0) < max_end:
        errors.append(f"duración del proyecto ({draft.get('duration')}) menor que el final del último elemento ({max_end})")
    return errors, warnings
