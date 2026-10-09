# -*- coding: utf-8 -*-
"""
Operacje na obrazie GIMP-a: odczyt pikseli do podglądu, sprawdzenie obrazu
i nałożenie LUT-a na warstwę (z cofaniem, zaznaczeniem i paskiem postępu).

Piksele czytamy i zapisujemy w formacie babl „R'G'B'A float” – wartości
perceptualne (gamma sRGB), niezależnie od tego, czy obraz jest w precyzji
liniowej czy perceptualnej, 8-, 16- czy 32-bitowej. Zapis idzie przez bufor
cienia (shadow) i merge_shadow(): GIMP sam przelicza wynik na precyzję
obrazu, uwzględnia miękkie zaznaczenie i zapisuje krok cofania.
"""

import math
import os

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl  # noqa: E402

import lut_apply  # noqa: E402

FORMAT = "R'G'B'A float"
STRIP_PIXELS = 1 << 21          # ok. 2 Mpx na pasek przy zapisie (32 MB)


class PluginError(Exception):
    """Błąd z komunikatem dla użytkownika (po polsku)."""


class Cancelled(Exception):
    """Użytkownik przerwał nakładanie LUT-a."""


def library_root():
    """Folder biblioteki w katalogu konfiguracji GIMP-a użytkownika."""
    return os.path.join(Gimp.directory(), "lut-cube")


# --- sprawdzanie obrazu --------------------------------------------------------

def needs_rgb_conversion(image):
    return image.get_base_type() != Gimp.ImageBaseType.RGB


def base_type_name(image):
    return {Gimp.ImageBaseType.GRAY: "w skali szarości",
            Gimp.ImageBaseType.INDEXED: "indeksowany"}.get(
                image.get_base_type(), "")


def profile_info(image):
    """
    (czy_sRGB, nazwa_profilu). LUT-y .cube zakładają wartości sRGB; GIMP
    podaje wtyczce wartości w przestrzeni obrazu, bez przeliczania barw.
    """
    profile = image.get_effective_color_profile()
    label = profile.get_label() if profile is not None else "sRGB"
    srgb = Gimp.ColorProfile.new_rgb_srgb()
    if profile is None or profile.is_equal(srgb):
        return True, label
    low = label.lower()
    return ("srgb" in low and "linear" not in low and "liniow" not in low), label


def convert_to_srgb(image):
    """Konwersja obrazu do wbudowanego sRGB (jeden krok cofania)."""
    if not image.convert_color_profile(Gimp.ColorProfile.new_rgb_srgb(),
                                       Gimp.ColorRenderingIntent.PERCEPTUAL,
                                       True):
        raise PluginError("Nie udało się skonwertować obrazu do sRGB.")


def target_layers(image, drawables):
    """Warstwy, na które można nałożyć LUT (bez grup, masek i kanałów)."""
    layers, skipped = [], []
    for d in drawables or []:
        if d.is_layer() and not d.is_group():
            layers.append(d)
        else:
            skipped.append(d.get_name())
    if not layers:
        if skipped:
            raise PluginError(
                "Wybrany element (%s) nie jest zwykłą warstwą – LUT można "
                "nałożyć tylko na warstwę z pikselami, nie na grupę, maskę "
                "ani kanał." % ", ".join(skipped))
        raise PluginError("Nie wybrano żadnej warstwy – zaznacz warstwę "
                          "w oknie Warstwy.")
    return layers


# --- odczyt pikseli -------------------------------------------------------------

def _get(drawable, x, y, w, h, scale=1.0, fmt=FORMAT):
    rect = Gegl.Rectangle.new(x, y, w, h)
    return drawable.get_buffer().get(rect, scale, fmt, Gegl.AbyssPolicy.CLAMP)


def selection_mask(image, layer, x, y, w, h, scale=1.0):
    """
    Maska zaznaczenia (float 0–1) dla prostokąta we współrzędnych warstwy
    (w skali `scale`) albo None, gdy nic nie jest zaznaczone.
    """
    if Gimp.Selection.is_empty(image):
        return None
    _ok, ox, oy = layer.get_offsets()
    sel = image.get_selection()
    rect = Gegl.Rectangle.new(int(round(ox * scale)) + x,
                              int(round(oy * scale)) + y, w, h)
    return sel.get_buffer().get(rect, scale, "Y float", Gegl.AbyssPolicy.NONE)


def read_scaled(image, layer, max_side):
    """
    Zmniejszona kopia warstwy do podglądu: (piksele, maska, szer., wys., skala).
    GEGL skaluje przy odczycie (szybko, także dla 24 Mpx).
    """
    W, H = layer.get_width(), layer.get_height()
    scale = min(1.0, float(max_side) / max(W, H))
    w = max(1, int(math.floor(W * scale)))
    h = max(1, int(math.floor(H * scale)))
    data = _get(layer, 0, 0, w, h, scale)
    mask = selection_mask(image, layer, 0, 0, w, h, scale)
    return data, mask, w, h, scale


def read_crop(image, layer, x, y, w, h):
    """Wycinek w pełnej rozdzielczości (podgląd 100 %)."""
    x = max(0, min(x, layer.get_width() - w))
    y = max(0, min(y, layer.get_height() - h))
    w = min(w, layer.get_width())
    h = min(h, layer.get_height())
    return (_get(layer, x, y, w, h), selection_mask(image, layer, x, y, w, h),
            x, y, w, h)


# --- nakładanie ----------------------------------------------------------------

def layer_name(lut_label, strength_percent):
    return "LUT: %s %d%%" % (lut_label, int(round(strength_percent)))


def apply_to_layer(image, layer, lut, params, output="new-layer",
                   strength_as_opacity=False, label="LUT", progress=None):
    """
    Nakłada LUT na warstwę. output: "new-layer" (kopia nad warstwą) albo
    "in-place". progress(ułamek) -> False przerywa (rzuca Cancelled).
    Zwraca warstwę z wynikiem. Wywołujący otwiera grupę cofania.
    """
    ok, x, y, w, h = layer.mask_intersect()
    if not ok or w <= 0 or h <= 0:
        raise PluginError("Zaznaczenie nie obejmuje warstwy „%s” – nie ma "
                          "czego zmieniać." % layer.get_name())

    pixel_params = params
    if output == "new-layer" and strength_as_opacity:
        pixel_params = lut_apply.Params(1.0, params.color, params.luminance,
                                        params.interpolation)

    target = layer
    if output == "new-layer":
        target = layer.copy()
        image.insert_layer(target, layer.get_parent(),
                           image.get_item_position(layer))
        target.set_name(layer_name(label, params.strength * 100))
        target.set_mode(Gimp.LayerMode.NORMAL)
        target.set_opacity(params.strength * 100 if strength_as_opacity
                           else 100.0)

    try:
        _process(layer, target, x, y, w, h, lut, pixel_params, progress)
    except BaseException:
        target.free_shadow()
        if target is not layer:
            image.remove_layer(target)
        raise
    return target


def _process(source, target, x, y, w, h, lut, params, progress):
    src = source.get_buffer()
    shadow = target.get_shadow_buffer()
    rows = max(1, STRIP_PIXELS // w)
    cache = None
    for y0 in range(y, y + h, rows):
        if progress is not None and progress((y0 - y) / float(h)) is False:
            raise Cancelled()
        sh = min(rows, y + h - y0)
        rect = Gegl.Rectangle.new(x, y0, w, sh)
        data = src.get(rect, 1.0, FORMAT, Gegl.AbyssPolicy.NONE)
        out, cache = lut_apply.apply_rgba(lut, data, params, cache=cache)
        shadow.set(rect, FORMAT, out)
    shadow.flush()
    if progress is not None and progress(1.0) is False:
        raise Cancelled()
    # Scalenie z cieniem: zaznaczenie (miękkie) + krok cofania.
    target.merge_shadow(True)
    target.update(x, y, w, h)
