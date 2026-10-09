#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LUT-y (.cube) – wtyczka GIMP 3.x (darmowa, GPL-3.0).

Nakłada kolorystyczne tablice LUT 3D/1D w formacie .cube, ma własną
bibliotekę LUT-ów, podgląd przed/po i suwak siły efektu. Działa offline.

Procedury:
  python-fu-lut-cube-apply   – Filtry → LUT → Zastosuj LUT (.cube)…
  python-fu-lut-cube-library – Filtry → LUT → Zarządzaj biblioteką…

Kod obliczeniowy jest w modułach obok (cube_parser, lut_apply, library),
które działają bez GIMP-a – testy: tests/test_lut_cube.py.
"""

import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PLUGIN_DIR)

import gi  # noqa: E402
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, GLib, GObject  # noqa: E402

PLUG_IN_BINARY = "lut-cube"
PROC_APPLY = "python-fu-lut-cube-apply"
PROC_LIBRARY = "python-fu-lut-cube-library"
MENU_PATH = "<Image>/Filters/LUT"


def _choice(items):
    choice = Gimp.Choice.new()
    for i, (nick, label) in enumerate(items):
        choice.add(nick, i, label, "")
    return choice


def _error(procedure, message):
    # GIMP sam pokazuje komunikat z GLib.Error.
    return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,
                                       GLib.Error(message))


def _success(procedure):
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)


def _params_from_config(config):
    import lut_apply
    return lut_apply.Params(config.get_property("strength") / 100.0,
                            config.get_property("color") / 100.0,
                            config.get_property("luminance") / 100.0,
                            config.get_property("interpolation"))


def _apply_noninteractive(procedure, image, drawables, config):
    """Wywołanie ze skryptu / „Powtórz”: wszystko z parametrów procedury."""
    import cube_parser
    import gimp_ops

    gfile = config.get_property("lut-file")
    path = gfile.get_path() if gfile is not None else None
    if not path or not os.path.isfile(path):
        raise gimp_ops.PluginError("Nie podano pliku .cube albo plik nie "
                                   "istnieje: %s" % (path or "(brak)"))
    if gimp_ops.needs_rgb_conversion(image):
        raise gimp_ops.PluginError(
            "Obraz jest %s – LUT działa na obrazach RGB. Zmień tryb: "
            "Obraz → Tryb → RGB." % gimp_ops.base_type_name(image))
    lut = cube_parser.parse_cube_file(path)
    layers = gimp_ops.target_layers(image, drawables)
    label = os.path.splitext(os.path.basename(path))[0]

    Gimp.progress_init("Nakładanie LUT-a %s…" % label)
    image.undo_group_start()
    try:
        for k, layer in enumerate(layers):
            def progress(f, k=k):
                Gimp.progress_update((k + f) / len(layers))
                return True
            gimp_ops.apply_to_layer(
                image, layer, lut, _params_from_config(config),
                config.get_property("output"),
                config.get_property("strength-as-opacity"), label, progress)
    finally:
        image.undo_group_end()
        Gimp.progress_end()


def _run_apply(procedure, run_mode, image, drawables, config, data):
    import cube_parser
    import gimp_ops

    if image is None:
        return _error(procedure, "Brak otwartego obrazu – otwórz zdjęcie "
                                 "i spróbuj ponownie.")
    try:
        if run_mode == Gimp.RunMode.INTERACTIVE:
            import ui
            result = ui.run_apply_dialog(PLUG_IN_BINARY, PLUGIN_DIR, image,
                                         drawables, config)
            if result is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL,
                                                   None)
        else:
            _apply_noninteractive(procedure, image, drawables, config)
    except (gimp_ops.PluginError, cube_parser.CubeError) as e:
        return _error(procedure, str(e))
    except Exception as e:  # noqa – żadnych niezłapanych wyjątków
        return _error(procedure, "Nieoczekiwany błąd wtyczki LUT: %s" % e)
    Gimp.displays_flush()
    return _success(procedure)


def _run_library(procedure, config, data):
    if config.get_property("run-mode") != Gimp.RunMode.INTERACTIVE:
        return _error(procedure, "Zarządzanie biblioteką wymaga okna "
                                 "(tryb interaktywny).")
    try:
        import ui
        ui.run_library_dialog(PLUG_IN_BINARY, PLUGIN_DIR)
    except Exception as e:  # noqa
        return _error(procedure, "Nieoczekiwany błąd biblioteki LUT: %s" % e)
    return _success(procedure)


class LutCube(Gimp.PlugIn):

    def do_query_procedures(self):
        return [PROC_APPLY, PROC_LIBRARY]

    def do_set_i18n(self, name):
        return False

    def do_create_procedure(self, name):
        Gegl.init(None)
        rw = GObject.ParamFlags.READWRITE

        if name == PROC_APPLY:
            p = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN,
                                        _run_apply, None)
            p.set_menu_label("Zastosuj LUT (.cube)…")
            p.set_documentation(
                "Nakłada kolorystyczny LUT 3D/1D z pliku .cube.",
                "Okno z biblioteką LUT-ów, podglądem przed/po i siłą efektu. "
                "Wywołanie bez okna: lut-file, strength (0–100), output "
                "(new-layer / in-place), interpolation (tetrahedral / "
                "trilinear), color, luminance, strength-as-opacity.", None)
            p.set_image_types("*")
            p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE
                                   | Gimp.ProcedureSensitivityMask.DRAWABLES)
            p.add_file_argument("lut-file", "Plik .cube", "LUT do nałożenia",
                                Gimp.FileChooserAction.OPEN, True, None, rw)
            p.add_double_argument("strength", "Siła (%)",
                                  "Siła efektu 0–100 %", 0.0, 100.0, 100.0, rw)
            p.add_choice_argument(
                "output", "Wynik", "Gdzie zapisać wynik",
                _choice([("new-layer", "Nowa warstwa z efektem"),
                         ("in-place", "Na aktywnej warstwie")]),
                "new-layer", rw)
            p.add_choice_argument(
                "interpolation", "Interpolacja", "Interpolacja w siatce LUT-a",
                _choice([("tetrahedral", "Tetraedralna"),
                         ("trilinear", "Trójliniowa")]),
                "tetrahedral", rw)
            p.add_double_argument("color", "Kolor (%)",
                                  "Ile zmiany koloru (barwy) przenieść",
                                  0.0, 100.0, 100.0, rw)
            p.add_double_argument("luminance", "Jasność (%)",
                                  "Ile zmiany jasności/kontrastu przenieść",
                                  0.0, 100.0, 100.0, rw)
            p.add_boolean_argument(
                "strength-as-opacity", "Siła jako krycie warstwy",
                "Nowa warstwa ma pełny efekt, a siła trafia do krycia",
                False, rw)

        elif name == PROC_LIBRARY:
            p = Gimp.Procedure.new(self, name, Gimp.PDBProcType.PLUGIN,
                                   _run_library, None)
            p.set_menu_label("Zarządzaj biblioteką…")
            p.set_documentation(
                "Biblioteka LUT-ów (.cube): import, nazwy, kategorie.",
                "Dodawanie, porządkowanie i podgląd LUT-ów bez obrazu.", None)
            p.add_enum_argument("run-mode", "Tryb uruchomienia",
                                "Tryb uruchomienia", Gimp.RunMode,
                                Gimp.RunMode.INTERACTIVE, rw)
        else:
            return None

        p.add_menu_path(MENU_PATH)
        p.set_attribution("Marek Zettel", "Marek Zettel (GPL-3.0)", "2026")
        return p


if __name__ == "__main__":
    Gimp.main(LutCube.__gtype__, sys.argv)
