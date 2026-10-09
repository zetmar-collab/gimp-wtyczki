# -*- coding: utf-8 -*-
"""
Wbudowany obrazek testowy do miniatur i podglądu bez otwartego zdjęcia:
tęcza barw z przejściem do bieli i czerni, pasek szarości oraz plamy
w typowych odcieniach skóry. Generowany w kodzie (bez plików graficznych).
"""

import colorsys
from array import array

# Odcienie skóry (sRGB 0–1): od bardzo jasnej do ciemnej.
SKIN_TONES = [
    (0.98, 0.87, 0.78), (0.94, 0.78, 0.66), (0.87, 0.67, 0.53),
    (0.76, 0.55, 0.41), (0.60, 0.41, 0.29), (0.42, 0.28, 0.20),
]


def make(width=192, height=128):
    """Zwraca bytes RGBA float32 (wartości perceptualne sRGB)."""
    px = array("f")
    rainbow_h = int(height * 0.6)
    gray_h = int(height * 0.15)
    skin_w = width / len(SKIN_TONES)
    for y in range(height):
        for x in range(width):
            if y < rainbow_h:
                hue = x / width
                t = y / max(1, rainbow_h - 1)          # góra jasna, dół ciemny
                light = 0.85 - 0.7 * t
                r, g, b = colorsys.hls_to_rgb(hue, light, 0.75)
            elif y < rainbow_h + gray_h:
                r = g = b = x / max(1, width - 1)
            else:
                r, g, b = SKIN_TONES[min(len(SKIN_TONES) - 1, int(x / skin_w))]
            px.extend((r, g, b, 1.0))
    return px.tobytes()
