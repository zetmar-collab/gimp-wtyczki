# -*- coding: utf-8 -*-
"""
Testy wtyczki LUT (.cube) bez GIMP-a: python -m pytest tests

Część testów korzysta z plików z folderu Luts/ (nie ma ich w publicznym
repo – wtedy są pomijane), z numpy i scipy (jeśli są zainstalowane) oraz
z ffmpeg (filtr lut3d) jako niezależnej referencji.
"""

import os
import pathlib
import random
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lut-cube"))

import cube_parser  # noqa: E402
import lut_apply  # noqa: E402
import library  # noqa: E402
from cube_parser import CubeError  # noqa: E402
from lut_apply import Params, TETRAHEDRAL, TRILINEAR  # noqa: E402

LUTS_DIR = ROOT / "Luts"
REAL_LUTS = sorted(LUTS_DIR.glob("*.cube")) if LUTS_DIR.is_dir() else []
HAVE_NUMPY = lut_apply.get_numpy() is not None
BACKENDS = [False] + ([True] if HAVE_NUMPY else [])   # use_numpy
INTERPS = [TETRAHEDRAL, TRILINEAR]


# --- pomocnicze -------------------------------------------------------------

def cube_text(n, fn, title="Test", domain=None, extra_header="", one_d=None):
    """Plik .cube z funkcji fn(r, g, b) -> (r, g, b) na siatce n (R najszybciej)."""
    lines = ["# komentarz", 'TITLE "%s"' % title]
    if one_d:
        lines.append("LUT_1D_SIZE %d" % len(one_d))
    if n:
        lines.append("LUT_3D_SIZE %d" % n)
    if domain:
        lines.append("DOMAIN_MIN %g %g %g" % domain[0])
        lines.append("DOMAIN_MAX %g %g %g" % domain[1])
    if extra_header:
        lines.append(extra_header)
    lines.append("")
    for row in one_d or []:
        lines.append("%.6f %.6f %.6f" % row)
    lo = domain[0] if domain else (0, 0, 0)
    hi = domain[1] if domain else (1, 1, 1)
    for b in range(n):
        for g in range(n):
            for r in range(n):
                x = [lo[k] + (hi[k] - lo[k]) * v / (n - 1)
                     for k, v in enumerate((r, g, b))]
                lines.append("%.6f %.6f %.6f" % fn(*x))
    return "\n".join(lines) + "\n"


def parse(text):
    return cube_parser.parse_cube_text(text, "test.cube")


def rgba_bytes(pixels):
    return struct.pack("<%df" % (4 * len(pixels)),
                       *[v for p in pixels for v in p])


def from_bytes(data):
    vals = struct.unpack("<%df" % (len(data) // 4), data)
    return [vals[i:i + 4] for i in range(0, len(vals), 4)]


def random_pixels(count, seed=1):
    rnd = random.Random(seed)
    return [(rnd.random(), rnd.random(), rnd.random(), rnd.random())
            for _ in range(count)]


identity = lambda r, g, b: (r, g, b)  # noqa: E731


# --- parser ----------------------------------------------------------------

@pytest.mark.skipif(not REAL_LUTS, reason="brak folderu Luts/")
def test_all_real_luts_parse():
    assert len(REAL_LUTS) == 49
    for path in REAL_LUTS:
        lut = cube_parser.parse_cube_file(str(path))
        assert lut.size_3d == 25 and lut.kind == "3D", path.name
        assert len(lut.table_3d) == 25 ** 3 * 3
        assert lut.domain_min_3d == (0, 0, 0) and lut.domain_max_3d == (1, 1, 1)
        assert lut.title.startswith("Zetmar")
        assert all(-0.5 < v < 1.5 for v in lut.table_3d), path.name


def test_header_comments_title_crlf_bom():
    text = cube_text(2, identity, title="Mój LUT").replace("\n", "\r\n")
    raw = b"\xef\xbb\xbf" + text.encode("utf-8")
    lut = cube_parser.parse_cube_text(raw.decode("utf-8-sig"))
    assert lut.title == "Mój LUT"
    assert lut.size_3d == 2 and lut.size_1d == 0


def test_comma_decimal_gives_readable_error():
    text = cube_text(2, identity).replace("1.000000 0.000000", "1,000000 0,000000", 1)
    with pytest.raises(CubeError, match="przecink"):
        parse(text)


def test_non_numeric_value():
    text = cube_text(2, identity).replace("1.000000", "abc", 1)
    with pytest.raises(CubeError, match="Wiersz"):
        parse(text)


def test_wrong_row_count():
    text = cube_text(3, identity)
    text = text.rsplit("\n", 2)[0] + "\n"           # usuwamy ostatni wiersz
    with pytest.raises(CubeError, match="zła liczba wierszy.*26.*27"):
        parse(text)


def test_missing_size():
    with pytest.raises(CubeError, match="LUT_3D_SIZE"):
        parse("TITLE \"x\"\n0 0 0\n")


def test_row_with_wrong_column_count():
    text = cube_text(2, identity).replace("0.000000 0.000000 0.000000",
                                          "0.0 0.0", 1)
    with pytest.raises(CubeError, match="3 liczby"):
        parse(text)


def test_1d_only():
    rows = [(v ** 2, v, 1 - v) for v in (0.0, 0.5, 1.0)]
    lut = parse(cube_text(0, None, one_d=rows))
    assert lut.kind == "1D" and lut.size_1d == 3
    r, g, b = lut_apply.lookup(lut, 0.25, 0.5, 0.75)
    assert r == pytest.approx(0.125)          # liniowo między 0 a 0.25
    assert g == pytest.approx(0.5)
    assert b == pytest.approx(0.25)


def test_1d_then_3d():
    rows = [(v * 0.5, v * 0.5, v * 0.5) for v in (0.0, 1.0)]  # 1D: x/2
    lut = parse(cube_text(2, lambda r, g, b: (b, g, r), one_d=rows))
    assert lut.kind == "1D+3D"
    r, g, b = lut_apply.lookup(lut, 0.8, 0.4, 0.2)
    assert (r, g, b) == pytest.approx((0.1, 0.2, 0.4))


def test_domain_other_than_0_1():
    dom = ((-0.5, -0.5, -0.5), (1.5, 1.5, 1.5))
    lut = parse(cube_text(5, identity, domain=dom))
    assert lut.domain_min_3d == (-0.5, -0.5, -0.5)
    for interp in INTERPS:
        assert lut_apply.lookup(lut, 0.3, 0.6, 0.9, interp) == \
            pytest.approx((0.3, 0.6, 0.9), abs=1e-6)


def test_input_range_keyword():
    lut = parse(cube_text(3, identity, extra_header="LUT_3D_INPUT_RANGE 0 2"))
    assert lut.domain_max_3d == (2.0, 2.0, 2.0)


def test_binary_cache_roundtrip(tmp_path):
    rows = [(0.0, 0.1, 0.2), (1.0, 0.9, 0.8)]
    lut = parse(cube_text(3, lambda r, g, b: (g, b, r), title="Ząb ś", one_d=rows))
    path = str(tmp_path / "x.lutc")
    cube_parser.save_binary(lut, path)
    back = cube_parser.load_binary(path)
    assert back.title == "Ząb ś" and back.kind == "1D+3D"
    assert list(back.table_3d) == list(lut.table_3d)
    assert list(back.table_1d) == list(lut.table_1d)
    with open(path, "r+b") as f:
        f.truncate(100)
    with pytest.raises(CubeError):
        cube_parser.load_binary(path)


# --- interpolacja ----------------------------------------------------------

@pytest.mark.parametrize("use_numpy", BACKENDS)
@pytest.mark.parametrize("interp", INTERPS)
@pytest.mark.parametrize("n", [2, 17])
def test_identity_lut_keeps_input(use_numpy, interp, n):
    lut = parse(cube_text(n, identity))
    pixels = random_pixels(500)
    out, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels),
                                  Params(interpolation=interp),
                                  use_numpy=use_numpy)
    for a, b in zip(pixels, from_bytes(out)):
        assert b == pytest.approx(a, abs=2e-6)


@pytest.mark.parametrize("interp", INTERPS)
def test_grid_points_exact(interp):
    rnd = random.Random(5)
    n = 5
    values = {}

    def fn(r, g, b):
        key = (round(r * 4), round(g * 4), round(b * 4))
        values[key] = (rnd.random(), rnd.random(), rnd.random())
        return values[key]

    lut = parse(cube_text(n, fn))
    for (r, g, b), expected in list(values.items())[::7]:
        got = lut_apply.lookup(lut, r / 4, g / 4, b / 4, interp)
        assert got == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_channel_order_r_fastest(use_numpy):
    """LUT zamieniający kanały: wyjście = (G, B, R) wejścia."""
    lut = parse(cube_text(9, lambda r, g, b: (g, b, r)))
    # Drugi wiersz danych to węzeł r=1, g=0, b=0 → wyjście (0, 0, 1/8).
    assert list(lut.table_3d[3:6]) == pytest.approx([0, 0, 1 / 8])
    pixels = [(0.2, 0.5, 0.9, 1.0), (1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.5)]
    out, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), Params(),
                                  use_numpy=use_numpy)
    got = from_bytes(out)
    assert got[0] == pytest.approx((0.5, 0.9, 0.2, 1.0), abs=1e-6)
    assert got[1] == pytest.approx((0.0, 0.0, 1.0, 1.0), abs=1e-6)
    assert got[2] == pytest.approx((0.0, 1.0, 0.0, 0.5), abs=1e-6)


def _warm_lut():
    return parse(cube_text(9, lambda r, g, b: (min(1, r * 1.2 + 0.05),
                                               g * 0.95, b * 0.7 + 0.02)))


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_strength_zero_is_original(use_numpy):
    data = rgba_bytes(random_pixels(300))
    out, _ = lut_apply.apply_rgba(_warm_lut(), data, Params(strength=0.0),
                                  use_numpy=use_numpy)
    assert out == data


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_strength_full_is_pure_lut_and_half_is_mix(use_numpy):
    lut = _warm_lut()
    pixels = from_bytes(rgba_bytes(random_pixels(300)))   # dokładnie float32
    full, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), Params(1.0),
                                   use_numpy=use_numpy)
    half, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), Params(0.5),
                                   use_numpy=use_numpy)
    for p, f, h in zip(pixels, from_bytes(full), from_bytes(half)):
        assert f[:3] == pytest.approx(lut_apply.lookup(lut, *p[:3]), abs=1e-5)
        assert h[:3] == pytest.approx([(a + b) / 2 for a, b in zip(p[:3], f[:3])],
                                      abs=1e-5)
        assert f[3] == p[3] and h[3] == p[3]          # alfa bez zmian


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_mask_weights(use_numpy):
    lut = _warm_lut()
    pixels = random_pixels(3)
    mask = struct.pack("<3f", 0.0, 0.5, 1.0)
    out, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), Params(), mask=mask,
                                  use_numpy=use_numpy)
    out = from_bytes(out)
    assert out[0] == pytest.approx(pixels[0], abs=1e-6)
    full = lut_apply.lookup(lut, *pixels[2][:3])
    assert out[2][:3] == pytest.approx(full, abs=1e-5)
    # Maska 0,5: połowa drogi w świetle liniowym (jak scalanie w GIMP-ie).
    dec, enc = lut_apply.srgb_decode, lut_apply.srgb_encode
    mid = [enc((dec(a) + dec(b)) / 2) for a, b in
           zip(pixels[1][:3], lut_apply.lookup(lut, *pixels[1][:3]))]
    assert out[1][:3] == pytest.approx(mid, abs=1e-5)


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_mask_blend_matches_gimp_merge(use_numpy):
    """Wartość zmierzona w GIMP 3.2.6: 0,502 → biel przez maskę 0,5 daje 0,804."""
    white = parse(cube_text(2, lambda r, g, b: (1.0, 1.0, 1.0)))
    out, _ = lut_apply.apply_rgba(white, rgba_bytes([(128 / 255,) * 3 + (1.0,)]),
                                  Params(), mask=struct.pack("<f", 0.5),
                                  use_numpy=use_numpy)
    assert from_bytes(out)[0][0] == pytest.approx(0.804, abs=0.002)


def _luma(p):
    return sum(k * v for k, v in zip(lut_apply.LUMA, p[:3]))


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_luminance_only_and_color_only(use_numpy):
    # Lekki LUT, żeby nic nie wychodziło poza 0–1 (bez przycięcia).
    lut = parse(cube_text(9, lambda r, g, b: (r * 0.8 + 0.1, g * 0.9 + 0.05,
                                              b * 0.6 + 0.1)))
    pixels = [(0.3, 0.5, 0.6, 1.0), (0.7, 0.2, 0.4, 1.0)]
    lum, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels),
                                  Params(color=0.0, luminance=1.0),
                                  use_numpy=use_numpy)
    col, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels),
                                  Params(color=1.0, luminance=0.0),
                                  use_numpy=use_numpy)
    for p, a, c in zip(pixels, from_bytes(lum), from_bytes(col)):
        full = lut_apply.lookup(lut, *p[:3])
        # Tylko jasność: przesunięcie równe we wszystkich kanałach.
        shift = [x - y for x, y in zip(a[:3], p[:3])]
        assert shift[0] == pytest.approx(shift[1], abs=1e-5)
        assert shift[1] == pytest.approx(shift[2], abs=1e-5)
        assert _luma(a) == pytest.approx(_luma(full), abs=1e-5)
        # Tylko kolor: jasność jak w oryginale.
        assert _luma(c) == pytest.approx(_luma(p), abs=1e-5)


@pytest.mark.parametrize("use_numpy", BACKENDS)
def test_out_of_range_lut_values_are_clamped(use_numpy):
    lut = parse(cube_text(2, lambda r, g, b: (r * 2 - 0.5, -0.3, 1.4)))
    out, _ = lut_apply.apply_rgba(lut, rgba_bytes([(0.9, 0.5, 0.5, 1.0)]),
                                  Params(), use_numpy=use_numpy)
    assert from_bytes(out)[0] == pytest.approx((1.0, 0.0, 1.0, 1.0))


def test_tetrahedral_neutral_axis_stays_neutral():
    """Na osi szarości interpolacja tetraedralna używa tylko węzłów szarych."""
    rnd = random.Random(3)

    def fn(r, g, b):
        if r == g == b:
            return (r, g, b)
        return (rnd.random(), rnd.random(), rnd.random())

    lut = parse(cube_text(5, fn))
    for v in (0.1, 0.33, 0.5, 0.77):
        assert lut_apply.lookup(lut, v, v, v, TETRAHEDRAL) == \
            pytest.approx((v, v, v), abs=1e-6)


@pytest.mark.skipif(not HAVE_NUMPY, reason="brak numpy")
@pytest.mark.parametrize("interp", INTERPS)
def test_numpy_matches_pure_python(interp):
    path = REAL_LUTS[0] if REAL_LUTS else None
    lut = (cube_parser.parse_cube_file(str(path)) if path
           else _warm_lut())
    pixels = random_pixels(2000, seed=9)
    pixels += [(v, v, v, 1.0) for v in (0.0, 0.25, 1.0)]
    mask = struct.pack("<%df" % len(pixels),
                       *[random.Random(i).random() for i in range(len(pixels))])
    params = Params(0.8, 0.6, 0.9, interp)
    a, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), params, mask, True)
    b, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), params, mask, False)
    for x, y in zip(from_bytes(a), from_bytes(b)):
        assert x == pytest.approx(y, abs=2e-5)


# --- niezależne referencje -------------------------------------------------

@pytest.mark.skipif(not HAVE_NUMPY, reason="brak numpy")
def test_trilinear_matches_scipy_map_coordinates():
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    import numpy as np
    lut = (cube_parser.parse_cube_file(str(REAL_LUTS[3])) if REAL_LUTS
           else _warm_lut())
    n = lut.size_3d
    grid = np.frombuffer(lut.table_3d, dtype=np.float32).reshape(n, n, n, 3)
    pixels = random_pixels(1000, seed=2)
    coords = np.array([[p[2], p[1], p[0]] for p in pixels]).T * (n - 1)  # b,g,r
    ref = np.stack([scipy_ndimage.map_coordinates(grid[..., c], coords, order=1)
                    for c in range(3)], axis=1)
    out, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels), Params(interpolation=TRILINEAR))
    got = np.array([p[:3] for p in from_bytes(out)])
    assert np.abs(got - np.clip(ref, 0, 1)).max() < 1e-5


def _ffmpeg():
    return shutil.which("ffmpeg")


def _write_ppm16(path, w, h, pixels):
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n65535\n" % (w, h))
        f.write(struct.pack(">%dH" % (3 * w * h),
                            *[int(round(v * 65535)) for p in pixels for v in p[:3]]))


def _read_ppm16(path):
    with open(path, "rb") as f:
        data = f.read()
    parts = data.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    vals = struct.unpack(">%dH" % (3 * w * h), parts[3][:6 * w * h])
    return [tuple(v / 65535 for v in vals[i:i + 3]) for i in range(0, len(vals), 3)]


@pytest.mark.skipif(not _ffmpeg(), reason="brak ffmpeg")
@pytest.mark.parametrize("interp", INTERPS)
def test_matches_ffmpeg_lut3d(tmp_path, interp):
    """Porównanie z filtrem ffmpeg lut3d (niezależna implementacja)."""
    if REAL_LUTS:
        cube = REAL_LUTS[0]
    else:
        cube = tmp_path / "warm.cube"
        cube.write_text(cube_text(9, lambda r, g, b: (r ** 0.8, g, b * 0.9)))
    lut = cube_parser.parse_cube_file(str(cube))
    w, h = 64, 48
    pixels = random_pixels(w * h, seed=4)
    pixels[:5] = [(0, 0, 0, 1), (1, 1, 1, 1), (0.5, 0.5, 0.5, 1),
                  (1, 0, 0, 1), (0.2, 0.7, 0.4, 1)]
    src, dst = tmp_path / "in.ppm", tmp_path / "out.ppm"
    _write_ppm16(src, w, h, pixels)
    # ffmpeg potrzebuje ścieżki LUT-a bez znaków specjalnych filtra.
    local_cube = tmp_path / "lut.cube"
    shutil.copyfile(cube, local_cube)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", "in.ppm", "-vf",
                    "lut3d=file=lut.cube:interp=%s" % interp, "-pix_fmt",
                    "rgb48be", "out.ppm"], cwd=tmp_path, check=True)
    ref = _read_ppm16(dst)
    out, _ = lut_apply.apply_rgba(lut, rgba_bytes(pixels),
                                  Params(interpolation=interp))
    worst = max(abs(a - b) for p, r in zip(from_bytes(out), ref)
                for a, b in zip(p[:3], r))
    assert worst < 2.5 / 65535 + 1e-4, worst


# --- biblioteka --------------------------------------------------------------

def _make_cubes(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, name in enumerate(names):
        p = folder / name
        p.write_text(cube_text(2, lambda r, g, b, k=k: (r, g, min(1, b + k / 100)),
                               title=name), encoding="utf-8")
        paths.append(str(p))
    return paths


def test_library_import_dedup_errors_and_reload(tmp_path):
    src = tmp_path / "źródło z ąę"
    names = ["Zetmar - Cyfrowy Przyjaciel - 001 Faded Matinee.cube",
             "Zetmar - Cyfrowy Przyjaciel - 002 Dusty Projector.cube"]
    paths = _make_cubes(src, names)
    (src / "pod").mkdir()
    shutil.copyfile(paths[0], src / "pod" / "kopia.CUBE")        # duplikat
    (src / "pod" / "zepsuty.cube").write_text("LUT_3D_SIZE 2\n0 0 0\n")
    (src / "notatka.txt").write_text("nie LUT")

    lib = library.Library(str(tmp_path / "bib"))
    assert lib.is_empty()
    res = lib.add_folder(str(src))
    assert len(res.added) == 2
    assert len(res.duplicates) == 1
    assert len(res.errors) == 1 and "zepsuty" in res.errors[0][0]
    assert "Pominięto duplikaty" in res.summary()

    ids = lib.query()
    assert [lib.display_name(i) for i in ids] == ["001 Faded Matinee",
                                                  "002 Dusty Projector"]
    # Pliki skopiowane do biblioteki – oryginał można usunąć.
    shutil.rmtree(src)
    lut = lib.load_lut(ids[0])
    assert lut.size_3d == 2
    assert os.path.isfile(os.path.join(lib.cache_dir, ids[0] + ".lutc"))

    lib.settings["strip_prefix"] = False
    assert lib.display_name(ids[0]).startswith("Zetmar - Cyfrowy")
    lib.settings["strip_prefix"] = True

    lib.rename(ids[1], "Mój ulubiony")
    lib.set_favorite(ids[1], True)
    lib.add_category("Ciepłe")
    lib.set_category(ids[0], "Ciepłe")
    lib.mark_used(ids[0])

    again = library.Library(str(tmp_path / "bib"))       # odczyt z dysku
    assert again.display_name(ids[1]) == "Mój ulubiony"
    assert again.query()[0] == ids[1]                     # ulubione na górze
    assert again.query(category="Ciepłe") == [ids[0]]
    assert again.query(favorites_only=True) == [ids[1]]
    assert again.query(text="faded") == [ids[0]]
    assert again.load_lut(ids[0]).size_3d == 2            # z cache binarnego
    assert again.recent == [ids[0]]


def test_library_categories_and_remove(tmp_path):
    paths = _make_cubes(tmp_path / "src", ["a.cube", "b.cube", "c.cube"])
    lib = library.Library(str(tmp_path / "bib"))
    ids = lib.add_files(paths).added
    lib.add_category("Kino")
    with pytest.raises(library.LibraryError):
        lib.add_category("Kino")
    lib.set_category(ids[0], "Kino")
    lib.rename_category("Kino", "Film")
    assert lib.entries[ids[0]]["category"] == "Film"
    lib.remove_category("Film")
    assert lib.entries[ids[0]]["category"] == ""

    file_a = lib.path(ids[0])
    lib.remove(ids[0], delete_file=False)
    assert os.path.isfile(file_a) and ids[0] not in lib.entries
    file_b = lib.path(ids[1])
    lib.remove(ids[1], delete_file=True)
    assert not os.path.exists(file_b)
    # Ten sam plik, który usunięto tylko z listy, da się dodać ponownie.
    assert len(lib.add_files([paths[0]]).added) == 1


def test_library_sort_by_date(tmp_path):
    paths = _make_cubes(tmp_path / "src", ["b.cube", "a.cube"])
    lib = library.Library(str(tmp_path / "bib"))
    ids = lib.add_files(paths).added
    lib.entries[ids[0]]["added"] = "2026-01-01T00:00:00"
    lib.entries[ids[1]]["added"] = "2026-02-01T00:00:00"
    assert lib.query(sort="date") == [ids[1], ids[0]]
    assert lib.query(sort="name") == [ids[1], ids[0]]       # "a" < "b"
    lib.entries[ids[1]]["added"] = "2025-01-01T00:00:00"
    assert lib.query(sort="date") == [ids[0], ids[1]]


def test_library_same_filename_different_content(tmp_path):
    a = _make_cubes(tmp_path / "x", ["lut.cube"])[0]
    b = tmp_path / "y" / "lut.cube"
    b.parent.mkdir()
    b.write_text(cube_text(2, lambda r, g, b: (g, r, b)))
    lib = library.Library(str(tmp_path / "bib"))
    ids = lib.add_files([a, str(b)]).added
    assert sorted(lib.entries[i]["file"] for i in ids) == ["lut (2).cube",
                                                           "lut.cube"]


def test_library_corrupted_index(tmp_path):
    root = tmp_path / "bib"
    root.mkdir()
    (root / "index.json").write_text("{to nie json")
    lib = library.Library(str(root))
    assert lib.is_empty() and "uszkodzony" in lib.load_warning


def test_suggested_startup_dir(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    home = tmp_path / "home"
    (home / "Pictures" / "LUTS").mkdir(parents=True)
    assert library.suggested_startup_dir({}, str(plugin), str(home)) == \
        str(home / "Pictures" / "LUTS")
    hinted = tmp_path / "moje LUT-y ąę"
    hinted.mkdir()
    (plugin / "startup-dir.txt").write_text(str(hinted), encoding="utf-8")
    assert library.suggested_startup_dir({}, str(plugin), str(home)) == str(hinted)
    assert library.suggested_startup_dir({"startup_dir": str(home)},
                                         str(plugin), str(home)) == str(home)


def test_sample_image_size():
    import sample_image
    data = sample_image.make(40, 30)
    assert len(data) == 40 * 30 * 16
