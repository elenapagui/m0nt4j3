import numpy as np

from montaje import color as C


def _frames(tint=(1.0, 1.0, 1.0), gain=1.0):
    rng = np.random.default_rng(0)
    base = rng.uniform(0.1, 0.8, size=(60, 80, 3))
    base[:, :40] = base[:, :40].mean(2, keepdims=True)  # mitad gris: referencia de blanco
    return [np.clip(base * np.array(tint) * gain * 255, 0, 255).astype(np.uint8)]


def test_corrige_dominante_calida():
    st = C.stats(_frames(tint=(1.15, 1.0, 0.85)))
    corr = C.correction(st, "natural", 1.0)
    r, g, b = corr["wb_gains"]
    assert r < 1.0 < b


def test_sube_exposicion_si_esta_oscuro():
    corr = C.correction(C.stats(_frames(gain=0.5)), "natural", 1.0)
    assert corr["capcut"]["brightness"] > 0.1
    corr = C.correction(C.stats(_frames(gain=1.4)), "natural", 1.0)
    assert corr["capcut"]["brightness"] < 0


def test_lut_neutra_con_intensidad_cero(tmp_path):
    st = C.stats(_frames())
    corr = C.correction(st, "natural", 0.0)
    path = C.write_cube(tmp_path / "x.cube", C.grade_fn(corr, include_tone=True), size=5)
    lines = path.read_text().splitlines()
    assert "LUT_3D_SIZE 5" in lines
    values = np.array([[float(v) for v in ln.split()] for ln in lines[4:]])
    assert values.shape == (125, 3)
    g = np.linspace(0, 1, 5)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    expected = np.stack([r.ravel(), gg.ravel(), b.ravel()], 1)
    assert np.abs(values - expected).max() < 1e-3


def test_igualar_a_referencia_acerca_el_color():
    src = C.stats(_frames(tint=(0.9, 1.0, 1.15)))
    ref = C.stats(_frames(tint=(1.1, 1.0, 0.9)))
    corr = C.correction(src, "natural", 1.0, ref=ref)
    px = _frames(tint=(0.9, 1.0, 1.15))[0].reshape(-1, 3) / 255.0
    out = C.grade_fn(corr, include_tone=False)(px)
    warm_before = (px[:, 0] - px[:, 2]).mean()
    warm_after = (out[:, 0] - out[:, 2]).mean()
    assert warm_after > warm_before + 0.05


def test_lab_ida_y_vuelta():
    rgb = np.random.default_rng(1).uniform(0, 1, size=(200, 3))
    assert np.abs(C.lab_to_rgb(C.rgb_to_lab(rgb)) - rgb).max() < 1e-3
