import copy
import json
from argparse import Namespace

import pytest

from montaje.capcut import builder
from montaje.capcut.store import Project, find_project
from montaje.capcut.validate import validate
from montaje.cli import cmd_montar
from montaje.util import MontajeError, read_json, write_json

from .conftest import NESTED, fake_media_info, load_fixture_draft


def _build(job, draft=None, meta=None):
    draft = draft or load_fixture_draft()
    return builder.build(draft, meta, job, job.timeline(), job.style(),
                         sound=lambda n: job.dir / f"{n}.wav", media_info=fake_media_info)


def _full_plan(job):
    job.plan.update({
        "hook": {"phrases": ["F013"]},
        "chapters": [{"phrase": "F009", "title": "1. El tiempo"}, {"phrase": "F013", "title": "3. La libertad"}],
        "titles": [{"phrase": "F012", "text": "80% en cosas innecesarias"},
                   {"phrase": "F013", "text": "Libertad"}],
        "highlights": ["libertad"],
        "zooms": [{"phrase": "F015", "kind": "push"}],
        "sfx": [{"phrase": "F015", "sound": "impacto"}],
        "overlays": [{"phrase": "F005", "file": "broll.jpg"}],
        "music": {"file": "musica.mp3"},
        "color": {"mode": "auto", "capcut": {"brightness": 0.1, "contrast": 0.05, "saturation": -0.1}},
    })


def test_montaje_completo_es_valido(job):
    _full_plan(job)
    draft, meta, rep = _build(job, meta=read_json(json_meta()))
    errors, _ = validate(draft, check_files=False)
    assert errors == []
    kinds = [t["type"] for t in draft["tracks"]]
    assert kinds[0] == "video" and "audio" in kinds and kinds.count("text") >= 2
    assert rep.clips > 5 and rep.captions > 5 and rep.titles == 4 and rep.overlays == 1 and rep.music == 3
    main = draft["tracks"][0]["segments"]
    # cada corte clona el clip del usuario: mismo material, refs nuevas y propias
    assert len({s["material_id"] for s in main}) == 1
    refs = [r for s in main for r in s["extra_material_refs"]]
    assert len(refs) == len(set(refs))
    # color en todos los cortes
    assert all({k["property_type"] for k in s["common_keyframes"]} >= {"KFTypeBrightness", "KFTypeSaturation"}
               for s in main)
    # el B-roll queda registrado como medio importado y enlazado
    photo = next(m for m in draft["materials"]["videos"] if m["type"] == "photo")
    reg = {e["id"]: e for g in meta["draft_materials"] for e in g["value"]}
    assert photo["local_material_id"] in reg and reg[photo["local_material_id"]]["file_Path"].endswith("broll.jpg")
    # el audio nunca lleva «clip» (CapCut se cierra)
    assert all(s["clip"] is None for t in draft["tracks"] if t["type"] == "audio" for s in t["segments"])
    assert draft["duration"] == max(s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                                    for t in draft["tracks"] for s in t["segments"])


def json_meta():
    from .conftest import FIXTURE
    return FIXTURE / "draft_meta_info.json"


def test_zoom_mantiene_el_ancla():
    s, tx, ty = builder.zoomed(2.0, {"x": 0.1, "y": -0.2}, [0.0, 0.3], 1.0)
    assert (s, tx, ty) == (2.0, 0.1, -0.2)
    s, tx, ty = builder.zoomed(2.0, {"x": 0.1, "y": -0.2}, [0.0, 0.3], 1.5)
    # el punto ancla (0, 0.3) de la pantalla sigue en el mismo sitio
    p = ((0.0 - 0.1) / 2.0, (0.3 + 0.2) / 2.0)  # coordenadas del ancla en el clip original
    assert abs(tx + s * p[0] - 0.0) < 1e-9 and abs(ty + s * p[1] - 0.3) < 1e-9


def _with_templates(draft):
    """Añade al borrador textos SUBTITULO (con una palabra en amarillo + animación) y TITULO."""
    tt = next(t for t in draft["tracks"] if t["type"] == "text")
    seg = tt["segments"][0]
    mat = next(m for m in draft["materials"]["texts"] if m["id"] == seg["material_id"])
    content = json.loads(mat["content"])
    st = content["styles"][0]
    yellow = copy.deepcopy(st)
    yellow["fill"] = {"content": {"render_type": "solid", "solid": {"color": [1, 0.8, 0]}}}
    st["range"], yellow["range"] = [0, 4], [4, 9]
    content.update(text="SUBTITULO", styles=[st, yellow])
    mat["content"] = json.dumps(content)
    anim = next(m for m in draft["materials"]["material_animations"] if m["id"] in seg["extra_material_refs"])
    anim["animations"] = [{"id": "x", "type": "in", "duration": 500000, "start": 0},
                          {"id": "y", "type": "out", "duration": 500000, "start": 2500000}]
    seg["clip"]["transform"]["y"] = -0.66
    title_seg = copy.deepcopy(seg)
    title_seg["id"] = "TITLE-SEG"
    title_mat = copy.deepcopy(mat)
    title_mat["id"] = "TITLE-MAT"
    c2 = json.loads(title_mat["content"])
    c2.update(text="TITULO", styles=[dict(st, range=[0, 6], size=30)])
    title_mat["content"] = json.dumps(c2)
    title_seg["material_id"] = "TITLE-MAT"
    title_seg["extra_material_refs"] = []
    title_seg["target_timerange"] = {"start": 20_000_000, "duration": 2_000_000}
    draft["materials"]["texts"].append(title_mat)
    tt["segments"].append(title_seg)
    return draft


def test_usa_tus_moldes_de_texto(job):
    _full_plan(job)
    draft, _, rep = _build(job, draft=_with_templates(load_fixture_draft()))
    assert validate(draft, check_files=False)[0] == []
    assert any("caption (tu proyecto)" in t for t in rep.templates)
    texts = {m["id"]: m for m in draft["materials"]["texts"]}
    all_text = [json.loads(m["content"])["text"] for m in texts.values()]
    assert "SUBTITULO" not in all_text and "TITULO" not in all_text  # los moldes se retiran
    caps = [s for t in draft["tracks"] if t["type"] == "text" for s in t["segments"]
            if json.loads(texts[s["material_id"]]["content"])["styles"][0].get("size") == 15]
    assert caps and all(s["clip"]["transform"]["y"] == -0.66 for s in caps)  # posición del molde
    # palabra resaltada con el estilo amarillo del molde
    lib = next(m for m in texts.values() if json.loads(m["content"])["text"] == "fue la libertad")
    styles = json.loads(lib["content"])["styles"]
    assert any(s["fill"]["content"]["solid"]["color"] == [1, 0.8, 0] for s in styles)
    # animación de salida recolocada al final de cada subtítulo
    anims = {m["id"]: m for m in draft["materials"]["material_animations"]}
    for s in caps:
        a = next(anims[r] for r in s["extra_material_refs"] if r in anims)
        out = next(x for x in a["animations"] if x["type"] == "out")
        assert out["start"] + out["duration"] == s["target_timerange"]["duration"]
    titles = [json.loads(texts[s["material_id"]]["content"]) for t in draft["tracks"] if t["type"] == "text"
              for s in t["segments"]]
    assert any(t["text"] == "1. El tiempo" and t["styles"][0]["size"] == 30 for t in titles)


def _montar(job, **kw):
    args = Namespace(plan=str(job.plan_path), proyecto="Mi video", simular=False, forzar=False, plantilla=None)
    for k, v in kw.items():
        setattr(args, k, v)
    cmd_montar(args)


@pytest.fixture
def ready(job, drafts, monkeypatch):
    """Proyecto cuyo vídeo apunta al del plan, sin ffmpeg (sonidos y medios simulados)."""
    import montaje.capcut.builder as b
    import montaje.sfx as sfx

    for rel in [NESTED, "draft_content.json", "template-2.tmp"]:
        p = drafts / "Mi video" / rel
        d = read_json(p)
        d["materials"]["videos"][0]["path"] = str(job.source)
        write_json(p, d)
    monkeypatch.setattr(b, "probe", fake_media_info)
    monkeypatch.setattr(sfx, "find_sound", lambda n: job.dir / "broll.jpg")
    original_build = b.build
    monkeypatch.setattr(b, "build", lambda *a, **k: original_build(*a, **dict(k, media_info=fake_media_info)))
    return job


def test_escribe_todos_los_espejos_y_conserva_ids(ready, drafts, capsys):
    _full_plan(ready)
    ready.save()
    _montar(ready)
    proj = find_project("Mi video")
    ids = set()
    for p in proj.docs:
        d = read_json(p)
        ids.add(d["id"])
        assert validate(d, check_files=False)[0] == []
        assert len(d["tracks"][0]["segments"]) > 5
    assert ids == {"C1EFCDF2-B885-48ff-A2F8-C33A6C4F4A52"}
    assert (proj.backup_dir() / "original" / NESTED).exists()
    # volver a montar parte del original, no del resultado anterior
    _montar(ready)
    d = read_json(proj.primary)
    assert len([s for s in d["tracks"][0]["segments"]]) == len(ready.timeline().clips)


def test_detecta_retoques_y_conserva_capas_globales(ready, drafts):
    _montar(ready)
    proj = find_project("Mi video")
    # 1) añades una capa de ajuste/filtro en CapCut: se conserva al rehacer
    d = read_json(proj.primary)
    d["tracks"].append({"id": "ADJ-TRACK", "type": "filter", "segments": [
        {"id": "ADJ-SEG", "material_id": "ADJ-MAT", "extra_material_refs": [],
         "target_timerange": {"start": 0, "duration": d["duration"]}}]})
    d["materials"].setdefault("effects", []).append({"id": "ADJ-MAT", "type": "filter"})
    for p in proj.docs:
        write_json(p, d)
    _montar(ready)
    d = read_json(find_project("Mi video").primary)
    assert any(s["id"] == "ADJ-SEG" for t in d["tracks"] for s in t["segments"])
    assert any(m["id"] == "ADJ-MAT" for m in d["materials"]["effects"])
    # 2) mueves un corte a mano: no se pisa sin --forzar
    d["tracks"][0]["segments"][1]["target_timerange"]["start"] += 1_000_000
    for p in find_project("Mi video").docs:
        write_json(p, d)
    with pytest.raises(MontajeError, match="retoques"):
        _montar(ready)
    _montar(ready, forzar=True)


def test_deshacer(ready, drafts):
    original = read_json(drafts / "Mi video" / NESTED)
    _montar(ready)
    Project(drafts / "Mi video").restore("original")
    assert read_json(drafts / "Mi video" / NESTED) == original


def test_video_equivocado(job):
    d = load_fixture_draft()
    d["materials"]["videos"][0]["path"] = "/x/otro.mp4"
    d["materials"]["videos"][0]["duration"] = 10_000_000
    with pytest.raises(MontajeError, match="no está"):
        _build(job, draft=d)


def test_moldes_desde_proyecto_plantilla(job):
    _full_plan(job)
    tpl = _with_templates(load_fixture_draft())
    draft, _, rep = builder.build(load_fixture_draft(), None, job, job.timeline(), job.style(),
                                  sound=lambda n: job.dir / f"{n}.wav", media_info=fake_media_info,
                                  template_draft=tpl)
    assert validate(draft, check_files=False)[0] == []
    texts = [json.loads(m["content"]) for m in draft["materials"]["texts"]]
    assert any(t["text"] == "1. El tiempo" and t["styles"][0]["size"] == 30 for t in texts)
    assert "SUBTITULO" not in [t["text"] for t in texts]
    # las animaciones del molde se han copiado a los subtítulos y los moldes no dejan restos
    anims = draft["materials"]["material_animations"]
    used = {r for t in draft["tracks"] for s in t["segments"] for r in s["extra_material_refs"]}
    assert all(a["id"] in used for a in anims)


def test_proyecto_nuevo_con_el_mismo_nombre_no_usa_la_copia_vieja(ready, drafts):
    _montar(ready)
    proj = find_project("Mi video")
    old_backup = proj.backup_dir()
    meta = read_json(proj.meta_path)
    meta["draft_id"] = "OTRO-PROYECTO"
    write_json(proj.meta_path, meta)
    # el «nuevo» proyecto tiene otro contenido original
    d = read_json(proj.primary)
    d["tracks"][0]["segments"] = d["tracks"][0]["segments"][:1]
    d["tracks"][0]["segments"][0]["target_timerange"]["duration"] = 89_300_000
    d["tracks"][0]["segments"][0]["source_timerange"] = {"start": 0, "duration": 89_300_000}
    for p in proj.docs:
        write_json(p, d)
    Project(drafts / "Mi video").original_snapshot()
    assert any(p.name.startswith("Mi video__anterior_") for p in old_backup.parent.iterdir())
    assert (old_backup / "proyecto_id.txt").read_text() == "OTRO-PROYECTO"
