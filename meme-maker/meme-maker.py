#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kreator memów – wtyczka GIMP 3.x (darmowa, GPL-3.0).

Ze zwykłego zdjęcia robi mema: tekst górny i dolny z obrysem, opcjonalny
pasek na tekst nad lub pod zdjęciem. Działa w pełni offline.

Procedury:
  python-fu-meme-maker  – Filtry → Memy → Stwórz mema…
  python-fu-meme-export – Filtry → Memy → Eksportuj mema…

Funkcje obliczeniowe (wybór czcionki, zawijanie, rozmiary) są czystym
Pythonem i nie wymagają GIMP-a – testy: tests/test_meme_layout.py.
"""

import os
import re
import shutil
import sys

# ---------------------------------------------------------------------------
# Czyste funkcje (bez GIMP-a) – testowane pytest-em
# ---------------------------------------------------------------------------

# Kolejność wyboru czcionki w trybie „automatycznie”.
# (rodzina, nazwy, którymi GIMP może ją nazywać – także po polsku)
FONT_PRIORITY = [
    ("Anton", ["Anton Regular", "Anton"]),
    ("Impact", ["Impact Normalny", "Impact Regular", "Impact"]),
    ("Arial Black", ["Arial Black Normalny", "Arial Black Regular",
                     "Arial Black"]),
    ("Sans Bold", ["Sans-serif Bold", "Sans Bold"]),
]

BUNDLED_FONT_FILE = "Anton-Regular.ttf"

# Ile wysokości obrazu może zająć jeden blok tekstu (górny lub dolny).
MAX_BLOCK_HEIGHT = 0.30
# Najmniejszy rozmiar czcionki przy auto-dopasowaniu (px).
MIN_FONT_SIZE = 8.0


def pick_font_name(available_names, priority=FONT_PRIORITY):
    """
    Zwraca nazwę pierwszej dostępnej czcionki z listy priorytetów
    albo None, jeśli żadnej nie ma. available_names – nazwy czcionek GIMP-a.
    """
    available = set(available_names)
    for _family, names in priority:
        for name in names:
            if name in available:
                return name
    return None


def auto_font_size(image_w):
    """Domyślny (startowy) rozmiar czcionki: proporcjonalny do szerokości."""
    return max(MIN_FONT_SIZE, image_w / 9.0)


def prepare_text(text, uppercase):
    """Normalizuje tekst: końce linii, puste linie na brzegach, WIELKIE LITERY."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(lines)
    return text.upper() if uppercase else text


def wrap_text(text, max_width, measure):
    """
    Zawija tekst słowami, żeby każda linia miała szerokość <= max_width.
    measure(linia) -> szerokość w px. Ręczne podziały linii są zachowane.
    Zwraca (lista_linii, czy_wszystko_się_zmieściło). Słowo dłuższe niż
    max_width zostaje w osobnej linii (wtedy drugi element to False).
    """
    out = []
    fits = True
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            out.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = line + " " + word
            if measure(candidate) <= max_width:
                line = candidate
            else:
                out.append(line)
                line = word
        out.append(line)
    for line in out:
        if line and measure(line) > max_width:
            fits = False
    return out, fits


def fit_text(text, max_width, max_height, start_size, measure, min_size=MIN_FONT_SIZE):
    """
    Auto-dopasowanie: najpierw zawija tekst, a jeśli nadal się nie mieści
    (za szerokie słowo albo za wysoki blok), zmniejsza czcionkę o 10 %.
    measure(tekst, rozmiar) -> (szerokość, wysokość) w px.
    Zwraca (zawinięty_tekst, rozmiar, zmieścił_się).
    """
    size = float(start_size)
    while True:
        lines, fits = wrap_text(text, max_width,
                                lambda s: measure(s, size)[0])
        wrapped = "\n".join(lines)
        _w, h = measure(wrapped, size)
        if fits and h <= max_height:
            return wrapped, size, True
        if size <= min_size:
            return wrapped, size, False
        size = max(min_size, size * 0.9)


def outline_px(font_size, outline_percent):
    """Grubość obrysu w px (% rozmiaru czcionki); 0 = bez obrysu."""
    if outline_percent <= 0:
        return 0
    return max(1, int(round(font_size * outline_percent / 100.0)))


def margin_px(image_w, image_h, margin_percent):
    """Margines od krawędzi: % krótszego boku obrazu, min. 2 px."""
    return max(2, int(round(min(image_w, image_h) * margin_percent / 100.0)))


def block_x(area_x, area_w, block_w, align):
    """Pozioma pozycja bloku tekstu w obszarze (left / center / right)."""
    if align == "left":
        x = area_x
    elif align == "right":
        x = area_x + area_w - block_w
    else:
        x = area_x + (area_w - block_w) // 2
    return int(max(0, x))


def export_path(image_path, fmt):
    """Domyślna ścieżka eksportu: obok oryginału, z dopiskiem „-mem”."""
    base, _ext = os.path.splitext(image_path)
    return base + "-mem." + ("jpg" if fmt == "jpg" else "png")


# ---------------------------------------------------------------------------
# Część GIMP-owa
# ---------------------------------------------------------------------------

try:
    import gi
    gi.require_version("Gimp", "3.0")
    gi.require_version("GimpUi", "3.0")
    gi.require_version("Gegl", "0.4")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gimp, GimpUi, Gegl, Gio, GLib, GObject, Gtk
    HAVE_GIMP = True
except (ImportError, ValueError):
    HAVE_GIMP = False

PLUG_IN_BINARY = "meme-maker"
PROC_MEME = "python-fu-meme-maker"
PROC_EXPORT = "python-fu-meme-export"
MENU_PATH = "<Image>/Filters/Memy"
DEFAULT_COLORS = {"text-color": "white", "outline-color": "black"}


class MemeError(Exception):
    """Błąd z komunikatem dla użytkownika (po polsku)."""


if HAVE_GIMP:

    # --- drobne pomocnicze ---------------------------------------------------

    def _choice(items):
        choice = Gimp.Choice.new()
        for i, (nick, label) in enumerate(items):
            choice.add(nick, i, label, "")
        return choice

    def _str_values(*values):
        return Gimp.ValueArray.new_from_values(
            [GObject.Value(GObject.TYPE_STRING, v) for v in values])

    def _error(procedure, message):
        # GIMP sam pokazuje komunikat z GLib.Error.
        return procedure.new_return_values(
            Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(message))

    # --- czcionki ------------------------------------------------------------

    def _available_font_names():
        names = []
        for family, _ in FONT_PRIORITY:
            first = family.split()[0]
            names += [f.get_name() for f in Gimp.fonts_get_list(re.escape(first))]
        return names

    def _install_bundled_font():
        """
        Jeśli Antona nie ma w systemie, kopiuje dołączony plik TTF do folderu
        czcionek GIMP-a (…\\GIMP\\3.x\\fonts) i odświeża listę czcionek.
        """
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fonts", BUNDLED_FONT_FILE)
        if not os.path.isfile(src):
            return False
        dest_dir = os.path.join(Gimp.directory(), "fonts")
        dest = os.path.join(dest_dir, BUNDLED_FONT_FILE)
        try:
            if not os.path.isfile(dest):
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copyfile(src, dest)
            Gimp.fonts_refresh()
        except OSError:
            return False
        return True

    def _auto_font():
        """Czcionka wg kolejności Anton → Impact → Arial Black → Sans Bold."""
        names = _available_font_names()
        name = pick_font_name(names)
        if name is None or not name.startswith("Anton"):
            if _install_bundled_font():
                names = _available_font_names()
                name = pick_font_name(names) or name
        if name is not None:
            font = Gimp.Font.get_by_name(name)
            if font is not None:
                return font
        return Gimp.context_get_font()

    def _resolve_font(config):
        if config.get_property("font-mode") == "custom":
            font = config.get_property("font")
            if font is not None:
                return font
        font = _auto_font()
        if font is None:
            raise MemeError("Nie znaleziono żadnej czcionki. Zainstaluj "
                            "czcionkę Anton (jest w paczce z wtyczką).")
        return font

    def _measure(font):
        def measure(text, size):
            ok, w, h, _asc, _desc = Gimp.text_get_extents_font(text, size, font)
            if not ok:
                raise MemeError("Nie udało się zmierzyć tekstu tą czcionką.")
            return w, h
        return measure

    # --- budowanie mema ------------------------------------------------------

    def _layout(text, font, config, area_w, max_h):
        """Zwraca (zawinięty_tekst, rozmiar) po auto-dopasowaniu."""
        size = config.get_property("font-size")
        start = size if size > 0 else auto_font_size(area_w)
        wrapped, size, fits = fit_text(text, area_w, max_h, start, _measure(font))
        return wrapped, size, fits

    def _make_text_layer(image, parent, text, font, size, color, align, name):
        """Tworzy warstwę tekstową na górze grupy `parent`."""
        layer = Gimp.TextLayer.new(image, text, font, size, Gimp.Unit.pixel())
        if layer is None:
            raise MemeError("Nie udało się utworzyć warstwy tekstowej.")
        image.insert_layer(layer, parent, 0)
        layer.set_name(name)
        layer.set_color(color)
        layer.set_antialias(True)
        layer.set_justification({
            "left": Gimp.TextJustification.LEFT,
            "center": Gimp.TextJustification.CENTER,
            "right": Gimp.TextJustification.RIGHT,
        }[align])
        return layer

    def _add_outline(image, parent, text_layer, outline, outline_color):
        """
        Obrys jako osobna warstwa pod tekstem: zaznaczenie z kanału alfa
        tekstu, powiększone o `outline` px i wypełnione kolorem obrysu.
        """
        if outline <= 0:
            return None
        W, H = image.get_width(), image.get_height()
        stroke = Gimp.Layer.new(image, text_layer.get_name() + " – obrys",
                                W, H, Gimp.ImageType.RGBA_IMAGE, 100,
                                Gimp.LayerMode.NORMAL)
        position = image.get_item_position(text_layer) + 1
        image.insert_layer(stroke, parent, position)
        stroke.fill(Gimp.FillType.TRANSPARENT)
        image.select_item(Gimp.ChannelOps.REPLACE, text_layer)
        Gimp.Selection.grow(image, outline)
        Gimp.context_set_foreground(outline_color)
        stroke.edit_fill(Gimp.FillType.FOREGROUND)
        Gimp.Selection.none(image)
        return stroke

    def _add_bar(image, side, bar_h, bar_color):
        """Powiększa płótno o pasek nad/pod zdjęciem i wypełnia go kolorem."""
        W, H = image.get_width(), image.get_height()
        offset_y = bar_h if side == "top" else 0
        image.resize(W, H + bar_h, 0, offset_y)
        bar = Gimp.Layer.new(image, "Mem – pasek", W, H + bar_h,
                             Gimp.ImageType.RGBA_IMAGE, 100,
                             Gimp.LayerMode.NORMAL)
        # Pasek na samym dole stosu – zdjęcie zostaje nad nim, nietknięte.
        image.insert_layer(bar, None, len(image.get_layers()))
        Gimp.context_set_foreground(bar_color)
        bar.fill(Gimp.FillType.FOREGROUND)
        return bar

    def _build_meme(image, config, warnings, state):
        """
        Buduje mema. `state` zapisuje, co zostało dodane (grupa, pasek,
        powiększenie płótna), żeby po błędzie dało się to wycofać.
        """
        top_text = prepare_text(config.get_property("top-text"),
                                config.get_property("uppercase"))
        bottom_text = prepare_text(config.get_property("bottom-text"),
                                   config.get_property("uppercase"))
        if not top_text and not bottom_text:
            raise MemeError("Oba pola tekstu są puste – wpisz tekst górny "
                            "lub dolny.")

        font = _resolve_font(config)
        align = config.get_property("align")
        color = config.get_property("text-color")
        outline_color = config.get_property("outline-color")
        bar_side = config.get_property("bar")
        bar_white = config.get_property("bar-color") == "white"

        W, H = image.get_width(), image.get_height()
        m = margin_px(W, H, config.get_property("margin-percent"))
        area_w = max(1, W - 2 * m)
        max_h = max(1, int(H * MAX_BLOCK_HEIGHT))

        group = Gimp.GroupLayer.new(image, "Mem")
        image.insert_layer(group, None, 0)
        state["group"] = group

        created = []   # (warstwa_tekstu, strona, obrys_px, w_pasku)
        for side, text in (("top", top_text), ("bottom", bottom_text)):
            if not text:
                continue
            in_bar = (side == bar_side)
            wrapped, size, fits = _layout(text, font, config, area_w, max_h)
            if not fits:
                warnings.append("Tekst %s jest bardzo długi – nie zmieścił się "
                                "w całości nawet po zmniejszeniu czcionki."
                                % ("górny" if side == "top" else "dolny"))
            if in_bar:
                # Styl „biały pasek”: kontrastowy tekst, bez obrysu.
                tcolor = Gegl.Color.new("black" if bar_white else "white")
                outline = 0
            else:
                tcolor = color
                outline = outline_px(size, config.get_property("outline-percent"))
            name = "Tekst górny" if side == "top" else "Tekst dolny"
            layer = _make_text_layer(image, group, wrapped, font, size,
                                     tcolor, align, name)
            created.append((layer, side, outline, in_bar))

        # Pasek na tekst: wysokość wg tekstu, który ma się w nim znaleźć.
        bar_h = 0
        if bar_side in ("top", "bottom"):
            bar_layers = [c for c in created if c[3]]
            if bar_layers:
                bar_h = bar_layers[0][0].get_height() + 2 * m
            else:
                warnings.append("Pasek pominięty – brak tekstu %s."
                                % ("górnego" if bar_side == "top" else "dolnego"))
            if bar_h:
                state["resize"] = (W, H, -bar_h if bar_side == "top" else 0)
                state["bar"] = _add_bar(
                    image, bar_side, bar_h,
                    Gegl.Color.new("white" if bar_white else "black"))

        # Rozmieszczenie (po ewentualnym powiększeniu płótna).
        W, H = image.get_width(), image.get_height()
        photo_top = bar_h if bar_side == "top" else 0
        photo_bottom = H - (bar_h if bar_side == "bottom" else 0)
        for layer, side, outline, in_bar in created:
            lw, lh = layer.get_width(), layer.get_height()
            x = block_x(m, area_w, lw, align)
            if in_bar:
                if side == "top":
                    y = (bar_h - lh) // 2
                else:
                    y = photo_bottom + (bar_h - lh) // 2
            elif side == "top":
                y = photo_top + m
            else:
                y = photo_bottom - lh - m
            layer.set_offsets(x, int(max(0, y)))
            _add_outline(image, group, layer, outline, outline_color)

        if not config.get_property("editable"):
            # Spłaszczenie: jedna warstwa „Mem” z widoku, warstwy pomocnicze
            # znikają, oryginalne zdjęcie zostaje pod spodem bez zmian.
            flat = Gimp.Layer.new_from_visible(image, image, "Mem")
            image.insert_layer(flat, None, 0)
            image.remove_layer(group)
            if state.get("bar") is not None:
                image.remove_layer(state["bar"])
            flat.set_name("Mem")   # dopiero teraz nazwa „Mem” jest wolna
            return flat
        return group

    def _cleanup(image, state):
        """Po błędzie usuwa wszystko, co wtyczka zdążyła dodać."""
        Gimp.Selection.none(image)
        for key in ("group", "bar"):
            item = state.get(key)
            if item is not None and item.is_valid():
                image.remove_layer(item)
        if "resize" in state:
            w, h, offset_y = state["resize"]
            image.resize(w, h, 0, offset_y)

    def _run_meme_dialog(procedure, config):
        GimpUi.init(PLUG_IN_BINARY)
        dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config,
                                        title="Stwórz mema")
        dialog.get_widget("top-text", Gtk.TextView)
        dialog.get_widget("bottom-text", Gtk.TextView)
        dialog.set_sensitive_if_in("font", None, "font-mode",
                                   _str_values("custom"), True)
        dialog.set_sensitive_if_in("bar-color", None, "bar",
                                   _str_values("top", "bottom"), True)
        dialog.fill(["top-text", "bottom-text", "font-mode", "font",
                     "font-size", "text-color", "outline-color",
                     "outline-percent", "uppercase", "align", "margin-percent",
                     "bar", "bar-color", "editable"])
        ok = dialog.run()
        dialog.destroy()
        return ok

    def _fix_default_colors(config):
        """
        GIMP 3.2 nie przekazuje domyślnych wartości argumentów-kolorów –
        przychodzi kolor w pełni przezroczysty. Taki kolor nie ma sensu dla
        tekstu, więc traktujemy go jako „nieustawiony” i wstawiamy domyślny.
        """
        for name, default in DEFAULT_COLORS.items():
            color = config.get_property(name)
            if color is None or color.get_rgba()[3] == 0:
                config.set_property(name, Gegl.Color.new(default))

    def _execute_meme(procedure, run_mode, image, config):
        if image is None:
            return _error(procedure, "Brak otwartego obrazu – otwórz zdjęcie "
                                     "i spróbuj ponownie.")
        _fix_default_colors(config)
        if run_mode == Gimp.RunMode.INTERACTIVE:
            if not _run_meme_dialog(procedure, config):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL,
                                                   None)

        warnings = []
        state = {}
        Gimp.context_push()
        image.undo_group_start()
        try:
            try:
                _build_meme(image, config, warnings, state)
            except Exception:
                _cleanup(image, state)
                raise
        except MemeError as e:
            return _error(procedure, str(e))
        except Exception as e:  # noqa – żadnych niezłapanych wyjątków
            return _error(procedure, "Nieoczekiwany błąd Kreatora memów: %s" % e)
        finally:
            image.undo_group_end()
            Gimp.context_pop()
        Gimp.displays_flush()
        if warnings:
            Gimp.message("\n".join(warnings))
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)

    # --- eksport -------------------------------------------------------------

    def _export(image, config):
        gfile = config.get_property("file")
        if gfile is None or not gfile.get_path():
            raise MemeError("Nie wybrano pliku docelowego.")
        fmt = config.get_property("format")
        path = gfile.get_path()
        ext = os.path.splitext(path)[1].lower()
        wanted = (".jpg", ".jpeg") if fmt == "jpg" else (".png",)
        if ext not in wanted:
            path = os.path.splitext(path)[0] + wanted[0]
        gfile = Gio.File.new_for_path(path)

        # Eksport z kopii – oryginalny obraz i jego warstwy zostają bez zmian.
        dup = image.duplicate()
        try:
            if dup.get_base_type() == Gimp.ImageBaseType.INDEXED:
                dup.convert_rgb()
            if fmt == "jpg":
                dup.flatten()
            else:
                dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)

            pdb = Gimp.get_pdb()
            proc = pdb.lookup_procedure(
                "file-jpeg-export" if fmt == "jpg" else "file-png-export")
            cfg = proc.create_config()
            cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            cfg.set_property("image", dup)
            cfg.set_property("file", gfile)
            if fmt == "jpg":
                cfg.set_property("quality",
                                 config.get_property("quality") / 100.0)
            else:
                cfg.set_property("compression",
                                 config.get_property("png-compression"))
            res = proc.run(cfg)
            if res.index(0) != Gimp.PDBStatusType.SUCCESS:
                raise MemeError("Nie udało się zapisać pliku:\n%s" % path)
        finally:
            dup.delete()
        return path

    def _run_export_dialog(procedure, config):
        GimpUi.init(PLUG_IN_BINARY)
        dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config,
                                        title="Eksportuj mema")
        dialog.set_sensitive_if_in("quality", None, "format",
                                   _str_values("jpg"), True)
        dialog.set_sensitive_if_in("png-compression", None, "format",
                                   _str_values("png"), True)
        dialog.fill(["file", "format", "quality", "png-compression"])
        ok = dialog.run()
        dialog.destroy()
        return ok

    def _execute_export(procedure, run_mode, image, config):
        if image is None:
            return _error(procedure, "Brak otwartego obrazu do eksportu.")
        if run_mode == Gimp.RunMode.INTERACTIVE:
            # Podpowiedź pliku: obok oryginału, z dopiskiem „-mem”.
            src = image.get_file()
            if src is not None and src.get_path():
                config.set_property("file", Gio.File.new_for_path(
                    export_path(src.get_path(), config.get_property("format"))))
            if not _run_export_dialog(procedure, config):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL,
                                                   None)
        try:
            path = _export(image, config)
        except MemeError as e:
            return _error(procedure, str(e))
        except Exception as e:  # noqa
            return _error(procedure, "Nieoczekiwany błąd eksportu: %s" % e)
        if run_mode == Gimp.RunMode.INTERACTIVE:
            Gimp.message("Zapisano mema:\n%s" % path)
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)

    # --- rejestracja ---------------------------------------------------------

    class MemeMaker(Gimp.PlugIn):

        def do_query_procedures(self):
            return [PROC_MEME, PROC_EXPORT]

        def do_set_i18n(self, name):
            return False

        def do_create_procedure(self, name):
            Gegl.init(None)
            rw = GObject.ParamFlags.READWRITE

            if name == PROC_MEME:
                p = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN,
                                            self.run_meme, None)
                p.set_menu_label("Stwórz mema…")
                p.set_documentation(
                    "Robi mema ze zdjęcia: tekst górny i dolny z obrysem.",
                    "Dodaje tekst górny i dolny (z obrysem jako osobną "
                    "warstwą) oraz opcjonalny pasek nad lub pod zdjęciem. "
                    "Oryginalna warstwa zostaje nietknięta.", None)
                p.add_string_argument("top-text", "Tekst górny",
                                      "Tekst u góry (może mieć kilka linii)",
                                      "", rw)
                p.add_string_argument("bottom-text", "Tekst dolny",
                                      "Tekst u dołu (może mieć kilka linii)",
                                      "", rw)
                p.add_choice_argument(
                    "font-mode", "Czcionka",
                    "Automatycznie: Anton → Impact → Arial Black → Sans Bold",
                    _choice([("auto", "Automatycznie (Anton → Impact → "
                                      "Arial Black → Sans Bold)"),
                             ("custom", "Wybrana poniżej")]),
                    "auto", rw)
                p.add_font_argument("font", "Własna czcionka",
                                    "Czcionka używana w trybie „Wybrana poniżej”",
                                    False, None, True, rw)
                p.add_double_argument(
                    "font-size", "Rozmiar czcionki (px, 0 = auto)",
                    "0 = automatycznie, proporcjonalnie do szerokości obrazu",
                    0.0, 2000.0, 0.0, rw)
                p.add_color_argument("text-color", "Kolor tekstu",
                                     "Kolor liter", False,
                                     Gegl.Color.new(DEFAULT_COLORS["text-color"]), rw)
                p.add_color_argument("outline-color", "Kolor obrysu",
                                     "Kolor obrysu liter", False,
                                     Gegl.Color.new(DEFAULT_COLORS["outline-color"]), rw)
                p.add_double_argument(
                    "outline-percent", "Grubość obrysu (% wysokości liter)",
                    "Grubość obrysu jako % rozmiaru czcionki; 0 = bez obrysu",
                    0.0, 20.0, 4.0, rw)
                p.add_boolean_argument("uppercase", "WIELKIE LITERY",
                                       "Zamień tekst na wielkie litery",
                                       True, rw)
                p.add_choice_argument(
                    "align", "Wyrównanie", "Wyrównanie tekstu",
                    _choice([("center", "Do środka"), ("left", "Do lewej"),
                             ("right", "Do prawej")]),
                    "center", rw)
                p.add_double_argument(
                    "margin-percent", "Margines (% krótszego boku)",
                    "Odstęp tekstu od krawędzi zdjęcia",
                    0.0, 20.0, 4.0, rw)
                p.add_choice_argument(
                    "bar", "Pasek na tekst",
                    "Pasek nad lub pod zdjęciem – trafia do niego tekst "
                    "z tej strony",
                    _choice([("none", "Brak"),
                             ("top", "Nad zdjęciem (tekst górny)"),
                             ("bottom", "Pod zdjęciem (tekst dolny)")]),
                    "none", rw)
                p.add_choice_argument(
                    "bar-color", "Kolor paska", "Kolor paska na tekst",
                    _choice([("white", "Biały (czarny tekst)"),
                             ("black", "Czarny (biały tekst)")]),
                    "white", rw)
                p.add_boolean_argument(
                    "editable", "Edytowalne warstwy tekstowe",
                    "Włączone: tekst zostaje edytowalny. Wyłączone: mem "
                    "spłaszczony do jednej warstwy nad zdjęciem.",
                    True, rw)

            elif name == PROC_EXPORT:
                p = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN,
                                            self.run_export, None)
                p.set_menu_label("Eksportuj mema…")
                p.set_documentation(
                    "Zapisuje mema do JPG lub PNG.",
                    "Eksportuje widoczny obraz do JPG (z wybraną jakością) "
                    "lub PNG. Obraz w GIMP-ie zostaje bez zmian.", None)
                p.add_file_argument("file", "Plik", "Gdzie zapisać mema",
                                    Gimp.FileChooserAction.SAVE, True, None, rw)
                p.add_choice_argument(
                    "format", "Format", "Format pliku",
                    _choice([("jpg", "JPG"), ("png", "PNG")]), "jpg", rw)
                p.add_int_argument("quality", "Jakość JPG (%)",
                                   "Jakość JPG 1–100", 1, 100, 90, rw)
                p.add_int_argument("png-compression", "Kompresja PNG (0–9)",
                                   "Stopień kompresji PNG (bezstratny)",
                                   0, 9, 6, rw)
            else:
                return None

            p.set_image_types("*")
            p.set_sensitivity_mask(
                Gimp.ProcedureSensitivityMask.DRAWABLE
                | Gimp.ProcedureSensitivityMask.DRAWABLES
                | Gimp.ProcedureSensitivityMask.NO_DRAWABLES)
            p.add_menu_path(MENU_PATH)
            p.set_attribution("Marek Zettel", "Marek Zettel (GPL-3.0)", "2026")
            return p

        def run_meme(self, procedure, run_mode, image, drawables, config, data):
            return _execute_meme(procedure, run_mode, image, config)

        def run_export(self, procedure, run_mode, image, drawables, config, data):
            return _execute_export(procedure, run_mode, image, config)


if __name__ == "__main__":
    Gimp.main(MemeMaker.__gtype__, sys.argv)
