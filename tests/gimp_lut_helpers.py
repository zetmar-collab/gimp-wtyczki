# -*- coding: utf-8 -*-
"""
Wspólne dane dla testów wtyczki LUT uruchamianych w GIMP-ie
(gimp_lut_integration.py, gimp_lut_ui.py).

Testy uruchamiaj z folderu repozytorium (albo ustaw LUT_CUBE_REPO).
LUT-y: folder Luts/ w repo, jeśli jest (nie ma go w publicznym repo),
inaczej kilka syntetycznych LUT-ów generowanych w katalogu tymczasowym.
"""
import os
import sys
import tempfile

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio  # noqa: E402

REPO = os.environ.get("LUT_CUBE_REPO") or os.getcwd()
if not os.path.isdir(os.path.join(REPO, "lut-cube")):
    raise SystemExit("Uruchom test z folderu repozytorium albo ustaw LUT_CUBE_REPO.")
sys.path.insert(0, os.path.join(REPO, "lut-cube"))

import library  # noqa: E402
import sample_image  # noqa: E402

TMP = os.path.join(tempfile.gettempdir(), "lut-cube-testy")

# Syntetyczne „looki”: (nazwa, funkcja r, g, b -> r, g, b)
_SYNTHETIC = [
    ("Ciepły", lambda r, g, b: (min(1, r * 1.1 + 0.04), g, b * 0.8)),
    ("Zimny", lambda r, g, b: (r * 0.85, g, min(1, b * 1.1 + 0.05))),
    ("Wyblakły", lambda r, g, b: (0.1 + r * 0.8, 0.1 + g * 0.8, 0.12 + b * 0.75)),
    ("Kontrast", lambda r, g, b: tuple(min(1, max(0, (v - 0.5) * 1.3 + 0.5))
                                      for v in (r, g, b))),
    ("Czarno-biały", lambda r, g, b: (0.3 * r + 0.59 * g + 0.11 * b,) * 3),
    ("Sepia", lambda r, g, b: (min(1, 0.39 * r + 0.77 * g + 0.19 * b),
                               min(1, 0.35 * r + 0.69 * g + 0.17 * b),
                               0.27 * r + 0.53 * g + 0.13 * b)),
    ("Zielone cienie", lambda r, g, b: (r, min(1, g + 0.06 * (1 - g)), b)),
    ("Odwrócony", lambda r, g, b: (1 - r, 1 - g, 1 - b)),
]


def _write_cube(path, title, fn, n=17):
    with open(path, "w", encoding="utf-8") as f:
        f.write('TITLE "%s"\nLUT_3D_SIZE %d\n' % (title, n))
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    out = fn(r / (n - 1), g / (n - 1), b / (n - 1))
                    f.write("%.6f %.6f %.6f\n" % out)


def luts_dir():
    """Folder z plikami .cube do testów."""
    real = os.path.join(REPO, "Luts")
    if os.path.isdir(real) and library.find_cube_files(real, False):
        return real
    folder = os.path.join(TMP, "luts")
    os.makedirs(folder, exist_ok=True)
    for k, (name, fn) in enumerate(_SYNTHETIC, 1):
        path = os.path.join(folder, "%s%03d %s.cube"
                            % (library.DEFAULT_PREFIX, k, name))
        if not os.path.exists(path):
            _write_cube(path, name, fn)
    return folder


def cube_files():
    return library.find_cube_files(luts_dir(), False)


def make_test_images():
    """Obrazy testowe (plazma + obrazek testowy): poziomy, pionowy, mały,
    z alfą, 16-bitowy. Zwraca folder."""
    folder = os.path.join(TMP, "obrazy")
    os.makedirs(folder, exist_ok=True)
    specs = [("poziome.png", 2400, 1600, False, None),
             ("pionowe.png", 1200, 1800, False, None),
             ("male.png", 40, 30, False, None),
             ("alfa.png", 1600, 1000, True, None),
             ("16bit.png", 1600, 1067, False, Gimp.Precision.U16_NON_LINEAR)]
    for name, w, h, alpha, prec in specs:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            continue
        img = (Gimp.Image.new_with_precision(w, h, Gimp.ImageBaseType.RGB, prec)
               if prec else Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB))
        t = Gimp.ImageType.RGBA_IMAGE if alpha else Gimp.ImageType.RGB_IMAGE
        layer = Gimp.Layer.new(img, "zdjęcie", w, h, t, 100, Gimp.LayerMode.NORMAL)
        img.insert_layer(layer, None, 0)
        f = Gimp.DrawableFilter.new(layer, "gegl:plasma", "")
        cfg = f.get_config()
        cfg.set_property("width", w)
        cfg.set_property("height", h)
        cfg.set_property("turbulence", 0.6)
        f.update()
        layer.merge_filter(f)
        sw, sh = min(w, 720) // 2, min(h, 480) // 2
        buf = layer.get_buffer()
        buf.set(Gegl.Rectangle.new(w - sw - 10 if w > sw + 10 else 0,
                                   10 if h > sh + 10 else 0, sw, sh),
                "R'G'B'A float", sample_image.make(sw, sh))
        buf.flush()
        if alpha:
            img.select_ellipse(Gimp.ChannelOps.REPLACE, 0, 0, w // 3, h // 2)
            layer.edit_clear()
            Gimp.Selection.none(img)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img,
                       Gio.File.new_for_path(path), None)
        img.delete()
    return folder
