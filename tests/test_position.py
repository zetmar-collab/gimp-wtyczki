# -*- coding: utf-8 -*-
"""Testy czystych funkcji wtyczki (bez GIMP-a): pytest tests/"""

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "podpis-zdjecia" / "podpis-zdjecia.py"
_spec = importlib.util.spec_from_file_location("podpis_zdjecia", _PATH)
pz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pz)

W, H, w, h, M = 6000, 4000, 600, 100, 120


@pytest.mark.parametrize("position, expected", [
    ("top-left", (M, M)),
    ("top-center", ((W - w) // 2, M)),
    ("top-right", (W - w - M, M)),
    ("middle-left", (M, (H - h) // 2)),
    ("center", ((W - w) // 2, (H - h) // 2)),
    ("middle-right", (W - w - M, (H - h) // 2)),
    ("bottom-left", (M, H - h - M)),
    ("bottom-center", ((W - w) // 2, H - h - M)),
    ("bottom-right", (W - w - M, H - h - M)),
])
def test_all_nine_positions(position, expected):
    x, y, clamped = pz.compute_offset(W, H, w, h, position, M)
    assert (x, y) == expected
    assert clamped is False


def test_positions_list_complete():
    assert len(pz.POSITIONS) == 9
    assert pz.POSITION_IDS[-1] == "bottom-right"


def test_unknown_position():
    with pytest.raises(ValueError):
        pz.split_position("nowhere")


def test_clamp_when_layer_bigger_than_image():
    x, y, clamped = pz.compute_offset(100, 100, 300, 50, "bottom-right", 10)
    assert (x, y) == (0, 40)
    assert clamped is True


def test_margin_px_overrides_percent():
    assert pz.resolve_margin(6000, 4000, 50, 3.0) == 50


def test_margin_percent_of_shorter_side():
    assert pz.resolve_margin(6000, 4000, 0, 3.0) == 120
    assert pz.resolve_margin(800, 1200, 0, 2.5) == 20
    assert pz.resolve_margin(800, 1200, 0, 0.0) == 0


def test_font_size_percent_scales_with_image():
    big = pz.font_size_px(6000, "percent", 4.0, 48)
    small = pz.font_size_px(1200, "percent", 4.0, 48)
    assert big == pytest.approx(240.0)
    assert small == pytest.approx(48.0)
    assert big / small == pytest.approx(6000 / 1200)


def test_font_size_pixels():
    assert pz.font_size_px(6000, "pixels", 4.0, 72) == 72.0


@pytest.mark.parametrize("position, justify", [
    ("top-left", "left"), ("middle-left", "left"), ("bottom-left", "left"),
    ("top-center", "center"), ("center", "center"), ("bottom-center", "center"),
    ("top-right", "right"), ("middle-right", "right"), ("bottom-right", "right"),
])
def test_auto_justify(position, justify):
    assert pz.auto_justify(position) == justify


def test_scaled_size_keeps_aspect():
    assert pz.scaled_size(6000, 500, 250, 20.0) == (1200, 600)
    assert pz.scaled_size(1200, 500, 250, 20.0) == (240, 120)


def test_fit_size_no_change_when_fits():
    assert pz.fit_size(1000, 800, 200, 100, "bottom-right", 20) == (200, 100, False)


def test_fit_size_shrinks_to_image_with_margin():
    nw, nh, shrunk = pz.fit_size(1000, 800, 2000, 400, "top-left", 20)
    assert shrunk is True
    assert nw <= 1000 - 20 and nh <= 800 - 20
    assert nw / nh == pytest.approx(2000 / 400, rel=0.01)


def test_fit_size_center_ignores_margin():
    nw, nh, shrunk = pz.fit_size(1000, 800, 2000, 100, "center", 50)
    assert (nw, nh, shrunk) == (1000, 50, True)
