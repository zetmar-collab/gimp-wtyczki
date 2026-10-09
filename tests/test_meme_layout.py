# -*- coding: utf-8 -*-
"""Testy czystych funkcji Kreatora memów (bez GIMP-a): pytest tests/"""

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parent.parent / "meme-maker" / "meme-maker.py"
_spec = importlib.util.spec_from_file_location("meme_maker", _PATH)
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


def fake_measure(text, size):
    """Szerokość = liczba znaków najdłuższej linii × 0,5 rozmiaru."""
    lines = text.split("\n")
    return max(len(ln) for ln in lines) * size * 0.5, len(lines) * size * 1.2


# --- czcionki ---------------------------------------------------------------

def test_font_anton_first():
    names = ["Sans-serif Bold", "Impact Normalny", "Anton Regular"]
    assert mm.pick_font_name(names) == "Anton Regular"


def test_font_impact_when_no_anton():
    assert mm.pick_font_name(["Sans-serif Bold", "Impact Normalny"]) == "Impact Normalny"


def test_font_no_impact_falls_back_to_arial_black_then_sans():
    assert mm.pick_font_name(["Sans-serif Bold", "Arial Black"]) == "Arial Black"
    assert mm.pick_font_name(["Arial Normalny", "Sans-serif Bold"]) == "Sans-serif Bold"


def test_font_none_available():
    assert mm.pick_font_name(["Comic Sans"]) is None


# --- tekst ------------------------------------------------------------------

def test_prepare_text_polish_uppercase():
    assert mm.prepare_text("zażółć gęślą jaźń", True) == "ZAŻÓŁĆ GĘŚLĄ JAŹŃ"


def test_prepare_text_trims_and_keeps_inner_lines():
    assert mm.prepare_text("\r\n  ala \r\n\r\nma kota \n\n", False) == "ala\n\nma kota"


def test_prepare_text_empty():
    assert mm.prepare_text("  \n \n", True) == ""
    assert mm.prepare_text(None, True) == ""


def test_wrap_short_text_single_line():
    lines, fits = mm.wrap_text("KRÓTKO", 1000, lambda s: len(s) * 10)
    assert lines == ["KRÓTKO"] and fits


def test_wrap_long_text_multiple_lines():
    text = "kiedy w poniedziałek rano okazuje się że jest dopiero wtorek"
    lines, fits = mm.wrap_text(text, 200, lambda s: len(s) * 10)
    assert fits and len(lines) > 1
    assert all(len(ln) * 10 <= 200 for ln in lines)
    assert " ".join(lines) == text


def test_wrap_keeps_manual_newlines():
    lines, _ = mm.wrap_text("a\nb", 1000, len)
    assert lines == ["a", "b"]


def test_wrap_too_long_word_reports_not_fitting():
    lines, fits = mm.wrap_text("x KONSTANTYNOPOLITAŃCZYKOWIANECZKA", 50, lambda s: len(s) * 10)
    assert not fits


def test_fit_short_text_keeps_start_size():
    wrapped, size, fits = mm.fit_text("HEJ", 1000, 500, 100, fake_measure)
    assert (wrapped, size, fits) == ("HEJ", 100, True)


def test_fit_long_word_shrinks_font():
    wrapped, size, fits = mm.fit_text("NIEPRAWDOPODOBNIE", 400, 500, 100, fake_measure)
    assert fits and size < 100
    assert fake_measure(wrapped, size)[0] <= 400


def test_fit_long_text_wraps_and_respects_height():
    text = " ".join(["SŁOWO"] * 40)
    wrapped, size, fits = mm.fit_text(text, 600, 300, 100, fake_measure)
    w, h = fake_measure(wrapped, size)
    assert fits and w <= 600 and h <= 300 and "\n" in wrapped


def test_fit_impossible_stops_at_min_size():
    wrapped, size, fits = mm.fit_text("ABCDEFGHIJ" * 20, 10, 10, 100, fake_measure)
    assert not fits and size == mm.MIN_FONT_SIZE


# --- rozmiary i pozycje ------------------------------------------------------

def test_auto_font_size_proportional():
    assert mm.auto_font_size(1800) == 2 * mm.auto_font_size(900)
    assert mm.auto_font_size(10) == mm.MIN_FONT_SIZE


def test_outline_px():
    assert mm.outline_px(100, 4) == 4
    assert mm.outline_px(10, 4) == 1      # minimum 1 px
    assert mm.outline_px(100, 0) == 0     # bez obrysu


def test_margin_px():
    assert mm.margin_px(1000, 500, 4) == 20
    assert mm.margin_px(30, 20, 4) == 2   # bardzo mały obraz


def test_block_x_alignments():
    assert mm.block_x(20, 960, 300, "left") == 20
    assert mm.block_x(20, 960, 300, "center") == 20 + 330
    assert mm.block_x(20, 960, 300, "right") == 20 + 660
    assert mm.block_x(20, 100, 300, "center") == 0   # nie wychodzi poza obraz


def test_export_path():
    assert mm.export_path(r"C:\foto\kot.png", "jpg") == r"C:\foto\kot-mem.jpg"
    assert mm.export_path("/a/b.jpeg", "png") == "/a/b-mem.png"
