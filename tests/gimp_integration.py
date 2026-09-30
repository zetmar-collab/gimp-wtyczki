# -*- coding: utf-8 -*-
"""
Test integracyjny w prawdziwym GIMP-ie (bez okna). Uruchom:
  gimp-console-3.2.exe -i --batch-interpreter python-fu-eval ^
      -b "exec(open(r'C:\\...\\tests\\gimp_integration.py', encoding='utf-8').read())" --quit
Wymaga zainstalowanej wtyczki. Wynik w konsoli: PASS/FAIL dla każdego kroku.
"""
import os
import tempfile

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio

results = []


def check(name, cond, info=""):
    results.append(bool(cond))
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, info), flush=True)


def new_image(w, h, base=Gimp.ImageBaseType.RGB):
    img = Gimp.Image.new(w, h, base)
    ltype = {Gimp.ImageBaseType.RGB: Gimp.ImageType.RGB_IMAGE,
             Gimp.ImageBaseType.GRAY: Gimp.ImageType.GRAY_IMAGE}[base]
    bg = Gimp.Layer.new(img, "tło", w, h, ltype, 100, Gimp.LayerMode.NORMAL)
    img.insert_layer(bg, None, 0)
    return img, bg


def run(proc_name, img, drawables, **props):
    proc = Gimp.get_pdb().lookup_procedure(proc_name)
    cfg = proc.create_config()
    cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    cfg.set_property("image", img)
    cfg.set_core_object_array("drawables", drawables)
    for k, v in props.items():
        cfg.set_property(k.replace("_", "-"), v)
    res = proc.run(cfg)
    return res.index(0), (res.index(1) if res.length() > 1 else None)


def make_logo_png():
    """PNG 500×250 z przezroczystością (lewa połowa pełna, prawa przezroczysta)."""
    img = Gimp.Image.new(500, 250, Gimp.ImageBaseType.RGB)
    ly = Gimp.Layer.new(img, "logo", 500, 250, Gimp.ImageType.RGBA_IMAGE,
                        100, Gimp.LayerMode.NORMAL)
    img.insert_layer(ly, None, 0)
    ly.fill(Gimp.FillType.TRANSPARENT)
    img.select_rectangle(Gimp.ChannelOps.REPLACE, 0, 0, 250, 250)
    Gimp.context_set_foreground(Gegl.Color.new("blue"))
    ly.edit_fill(Gimp.FillType.FOREGROUND)
    Gimp.Selection.none(img)
    path = os.path.join(tempfile.gettempdir(), "podpis_test_logo.png")
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, Gio.File.new_for_path(path), None)
    img.delete()
    return path


OK = Gimp.PDBStatusType.SUCCESS
red = Gegl.Color.new("red")

# --- 2. Tekst na 6000×4000 --------------------------------------------------
img, bg = new_image(6000, 4000)
st, _ = run("python-fu-podpis-tekst", img, [bg], text="© Test", color=red,
            opacity=50.0, position="bottom-right")
top = img.get_layers()[0]
check("2 tekst: status SUCCESS", st == OK)
check("2 tekst: warstwa na górze jest tekstowa (edytowalna)", top.is_text_layer())
check("2 tekst: krycie 50", abs(top.get_opacity() - 50.0) < 0.01, top.get_opacity())
r, g, b, a = top.get_color().get_rgba()
check("2 tekst: kolor czerwony", r > 0.99 and g < 0.01 and b < 0.01, (r, g, b))
m = 120
_, ox, oy = top.get_offsets()
check("2 tekst: dół-prawo", ox == 6000 - top.get_width() - m and oy == 4000 - top.get_height() - m,
      (ox, oy, top.get_width(), top.get_height()))
check("2 tekst: nazwa 'Podpis'", top.get_name() == "Podpis", top.get_name())
check("2 tekst: rozmiar fontu = 4% z 6000", abs(top.get_font_size()[0] - 240) < 0.5,
      top.get_font_size())
big_h = top.get_height()
check("2 tekst: tło nietknięte, 2 warstwy", len(img.get_layers()) == 2)
img.delete()

# --- 3. Ten sam podpis na 1200×800 ------------------------------------------
img, bg = new_image(1200, 800)
st, _ = run("python-fu-podpis-tekst", img, [bg], text="© Test", color=red,
            opacity=50.0, position="bottom-right")
top = img.get_layers()[0]
ratio = big_h / float(top.get_height())
check("3 tekst 1200: proporcjonalnie mniejszy (~5x)", 4.5 < ratio < 5.5, ratio)
img.delete()

# --- 4. Dziewięć pozycji -----------------------------------------------------
W, H = 1200, 800
for pos in ["top-left", "top-center", "top-right", "middle-left", "center",
            "middle-right", "bottom-left", "bottom-center", "bottom-right"]:
    img, bg = new_image(W, H)
    run("python-fu-podpis-tekst", img, [bg], text="Abc", position=pos, margin=30)
    t = img.get_layers()[0]
    _, x, y = t.get_offsets()
    w, h = t.get_width(), t.get_height()
    row, col = ("middle", "center") if pos == "center" else pos.split("-")
    ex = {"left": 30, "center": (W - w) // 2, "right": W - w - 30}[col]
    ey = {"top": 30, "middle": (H - h) // 2, "bottom": H - h - 30}[row]
    just = t.get_justification()
    ej = {"left": Gimp.TextJustification.LEFT, "center": Gimp.TextJustification.CENTER,
          "right": Gimp.TextJustification.RIGHT}[col]
    check("4 pozycja %s" % pos, (x, y) == (ex, ey) and just == ej, (x, y, ex, ey, just))
    img.delete()

# --- 5. Krycie 0 i 100 -------------------------------------------------------
for op in (0.0, 100.0):
    img, bg = new_image(800, 600)
    st, _ = run("python-fu-podpis-tekst", img, [bg], text="x", opacity=op)
    check("5 krycie %d" % op, st == OK and len(img.get_layers()) == 2
          and abs(img.get_layers()[0].get_opacity() - op) < 0.01)
    img.delete()

# --- 6. Obraz: PNG, 20%, krycie 80, góra-lewo --------------------------------
logo = make_logo_png()
img, bg = new_image(3000, 2000)
st, err = run("python-fu-podpis-obraz", img, [bg], file=Gio.File.new_for_path(logo),
              scale_percent=20.0, opacity=80.0, position="top-left")
top = img.get_layers()[0]
_, ox, oy = top.get_offsets()
check("6 obraz: status SUCCESS", st == OK, err)
check("6 obraz: szerokość 20% (600) i proporcje", top.get_width() == 600 and top.get_height() == 300,
      (top.get_width(), top.get_height()))
check("6 obraz: offset = margines (60)", (ox, oy) == (60, 60), (ox, oy))
check("6 obraz: kanał alfa", top.has_alpha())
check("6 obraz: krycie 80", abs(top.get_opacity() - 80) < 0.01)
px = top.get_pixel(550, 150).get_rgba()
check("6 obraz: przezroczystość zachowana", px[3] < 0.01, px)
check("6 obraz: nazwa 'Podpis'", top.get_name() == "Podpis", top.get_name())
img.delete()

# obraz w skali szarości
img, bg = new_image(1000, 800, Gimp.ImageBaseType.GRAY)
st, err = run("python-fu-podpis-obraz", img, [bg], file=Gio.File.new_for_path(logo))
check("6b obraz na obrazie GRAY", st == OK and len(img.get_layers()) == 2, err)
img.delete()

# --- 9. Błędy ----------------------------------------------------------------
img, bg = new_image(800, 600)
st, err = run("python-fu-podpis-tekst", img, [bg], text="   \n ")
check("9 pusty tekst -> błąd, brak warstwy",
      st == Gimp.PDBStatusType.EXECUTION_ERROR and len(img.get_layers()) == 1, err)
st, err = run("python-fu-podpis-obraz", img, [bg],
              file=Gio.File.new_for_path(r"C:\nie\ma\takiego.png"))
# GIMP sam odrzuca nieistniejący plik (akcja OPEN) już przy walidacji PDB.
check("9 brak pliku -> błąd, brak warstwy",
      st in (Gimp.PDBStatusType.EXECUTION_ERROR, Gimp.PDBStatusType.CALLING_ERROR)
      and len(img.get_layers()) == 1, err)
bad = os.path.join(tempfile.gettempdir(), "podpis_zly.png")
with open(bad, "wb") as f:
    f.write(b"to nie jest png")
st, err = run("python-fu-podpis-obraz", img, [bg], file=Gio.File.new_for_path(bad))
check("9 uszkodzony plik -> błąd, brak warstwy",
      st == Gimp.PDBStatusType.EXECUTION_ERROR and len(img.get_layers()) == 1, err)
# undo group zamknięty: kolejne wywołanie działa normalnie
st, _ = run("python-fu-podpis-tekst", img, [bg], text="po błędzie")
check("9 po błędach kolejny podpis działa", st == OK and len(img.get_layers()) == 2)
img.delete()

img, bg = new_image(200, 100)
st, _ = run("python-fu-podpis-obraz", img, [bg], file=Gio.File.new_for_path(logo),
            scale_mode="original")
top = img.get_layers()[0]
check("9 logo większe niż zdjęcie -> zmniejszone, mieści się",
      st == OK and top.get_width() <= 200 and top.get_height() <= 100,
      (top.get_width(), top.get_height()))
st, _ = run("python-fu-podpis-tekst", img, [bg], text="Bardzo długi podpis",
            size_mode="pixels", font_size_px=400.0)
_, ox, oy = img.get_layers()[0].get_offsets()
check("9 tekst większy niż zdjęcie -> offset >= 0", st == OK and ox >= 0 and oy >= 0, (ox, oy))
img.delete()

# --- 10. Wiele warstw, grupa, brak zaznaczenia ------------------------------
img, bg = new_image(800, 600)
grp = Gimp.GroupLayer.new(img, "grupa")
img.insert_layer(grp, None, 0)
l2 = Gimp.Layer.new(img, "w grupie", 100, 100, Gimp.ImageType.RGBA_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
img.insert_layer(l2, grp, 0)
for label, dr in (("wiele warstw", [bg, l2]), ("grupa", [grp]), ("nic", [])):
    n = len(img.get_layers())
    st, err = run("python-fu-podpis-tekst", img, dr, text=label)
    top = img.get_layers()[0]
    check("10 %s -> podpis na górze, poza grupą" % label,
          st == OK and len(img.get_layers()) == n + 1 and top.get_parent() is None
          and top.is_text_layer(), err)
img.delete()

print("WYNIK: %d/%d PASS" % (sum(results), len(results)), flush=True)
