from montaje.analyze import analyze, build_phrases, is_filler
from montaje.captions import caption_chunks, highlight_spans
from montaje.timeline import apply_zooms, build_timeline


def test_muletillas():
    for w in ["eh", "Eh,", "ehm", "mmm", "em", "Emm..."]:
        assert is_filler(w), w
    for w in ["e", "el", "este", "en", "me", "ha"]:
        assert not is_filler(w), w


def test_tomas_repetidas_y_comentarios(words):
    a = analyze(words)
    by_text = {p["text"]: p for p in a["phrases"]}
    assert not by_text["Hoy quiero hablaros de por qué dejé mi trabajo"]["keep"]
    assert by_text["Hoy quiero hablaros de por qué dejé mi trabajo en una multinacional."]["keep"]
    assert not by_text["Perdón, otra vez."]["keep"]
    assert not by_text["Pero había algo que no encajaba."]["keep"]
    assert by_text["Pero había algo que no encajaba conmigo."]["keep"]
    # estructuras paralelas («el primer motivo… el segundo motivo…») NO son repeticiones
    assert by_text["El primer motivo fue el tiempo."]["keep"]
    cut_words = [words[i]["w"] for i in a["cut_words"]]
    assert cut_words == ["Eh,", "Em,"]


def test_frases_cubren_todas_las_palabras(words):
    phrases = build_phrases(words)
    assert phrases[0]["w"][0] == 0 and phrases[-1]["w"][1] == len(words)
    for a, b in zip(phrases, phrases[1:]):
        assert a["w"][1] == b["w"][0]


def _timeline(job, **cuts):
    style = job.style()
    style["cuts"].update(cuts)
    tl = build_timeline(job.plan, job.words, [], style["cuts"], job.duration)
    apply_zooms(tl, job.plan, style["zoom"])
    return tl


def test_linea_de_tiempo_sin_huecos_ni_solapes(job):
    tl = _timeline(job)
    t = 0.0
    for c in tl.clips:
        assert abs(c.tl_start - t) < 1e-6
        assert c.src_end > c.src_start
        t = c.tl_end
    main = tl.main_clips()
    for a, b in zip(main, main[1:]):
        assert b.src_start >= a.src_end - 1e-6  # el orden del original se respeta
    # las palabras cortadas no aparecen
    kept = {wi for c in tl.clips for wi in c.words}
    assert not kept & set(job.plan["cut_words"])


def test_gancho_va_primero_y_no_desplaza_el_mapa(job):
    f13 = job.phrase("F013")
    job.plan["hook"] = {"phrases": ["F013"]}
    tl = _timeline(job)
    assert tl.clips[0].section == "hook"
    assert abs(tl.clips[0].src_start - f13["start"]) < 0.3
    # map_src sigue apuntando a la aparición «normal» de la frase, no al gancho
    assert tl.map_src(f13["start"]) > tl.clips[0].tl_end


def test_map_src_monotono(job):
    tl = _timeline(job)
    times = [tl.map_src(x / 2) for x in range(0, 180)]
    assert times == sorted(times)


def test_zoom_alterno_y_de_enfasis(job):
    job.plan["zooms"] = [{"phrase": "F015", "kind": "push", "scale": 1.2}]
    tl = _timeline(job)
    levels = {c.zoom for c in tl.clips}
    assert levels >= {1.0, 1.12}
    pushed = [c for c in tl.clips if c.push]
    assert pushed and pushed[-1].push[1] > pushed[0].push[0]


def test_subtitulos(job):
    tl = _timeline(job)
    cfg = job.style()["captions"]
    chunks = caption_chunks(tl, cfg)
    assert chunks
    for a, b in zip(chunks, chunks[1:]):
        assert a["end"] <= b["start"] + 1e-6
    for ch in chunks:
        assert len(ch["text"]) <= cfg["max_chars"] + 10
        assert not ch["text"].split()[-1].lower() in {"en", "de", "la", "el", "y"}
        assert "," not in ch["text"] and "." not in ch["text"]


def test_resaltados():
    text = "gastaba el 80% en la libertad financiera"
    spans = highlight_spans(text, ["libertad financiera"], numbers=True)
    assert [text[a:b] for a, b in spans] == ["80%", "libertad financiera"]
