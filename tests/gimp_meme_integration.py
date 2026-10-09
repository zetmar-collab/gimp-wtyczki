# -*- coding: utf-8 -*-
"""
Test integracyjny Kreatora memów w prawdziwym GIMP-ie (bez okna). Uruchom:
  gimp-console-3.2.exe -i --batch-interpreter python-fu-eval ^
      -b "exec(open(r'C:\\...\\tests\\gimp_meme_integration.py', encoding='utf-8').read())" --quit
Wymaga zainstalowanej wtyczki. Podglądy PNG trafiają do %TEMP%\\meme_test.
"""
import os
import tempfile

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio

OUT = os.path.join(tempfile.gettempdir(), "meme_test")
os.makedirs(OUT, exist_ok=True)
OK = Gimp.PDBStatusType.SUCCESS
results = []


def check(name, cond, info=""):
    results.append(bool(cond))
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, info), flush=True)


def photo(w, h):
    """„Zdjęcie”: gradient, żeby było widać, czy piksele się nie zmieniły."""
    img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
    bg = Gimp.Layer.new(img, "zdjęcie", w, h, Gimp.ImageType.RGB_IMAGE, 100,
                        Gimp.LayerMode.NORMAL)
    img.insert_layer(bg, None, 0)
    Gimp.context_set_foreground(Gegl.Color.new("#3a6ea5"))
    Gimp.context_set_background(Gegl.Color.new("#e0a030"))
    bg.edit_gradient_fill(Gimp.GradientType.LINEAR, 0, False, 1, 0, True,
                          0, 0, w, h)
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


def save_preview(img, name):
    dup = img.duplicate()
    dup.flatten()
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup,
                   Gio.File.new_for_path(os.path.join(OUT, name)), None)
    dup.delete()


def texts(img):
    group = [l for l in img.get_layers() if l.get_name() == "Mem"][0]
    return group, [l for l in group.get_children() if l.is_text_layer()]


def inside(layer, img):
    _, x, y = layer.get_offsets()
    return (x >= 0 and y >= 0 and x + layer.get_width() <= img.get_width()
            and y + layer.get_height() <= img.get_height())


MEME = "python-fu-meme-maker"
PL = "zażółć gęślą jaźń"

# --- 3 rozmiary × krótki / długi / polskie znaki ----------------------------
LONG = ("kiedy szef mówi że to tylko mała poprawka a potem okazuje się "
        "że trzeba przepisać cały system od nowa")
for label, (w, h) in (("poziome", (1600, 900)), ("pionowe", (900, 1600)),
                      ("małe", (120, 80))):
    for tlabel, top, bottom in (("krótki", "Hej", "Ho"),
                                ("długi", LONG, LONG),
                                ("polskie", PL, "Łódź ćma")):
        img, bg = photo(w, h)
        px_before = bg.get_pixel(w // 2, h // 2).get_rgba()
        st, err = run(MEME, img, [bg], top_text=top, bottom_text=bottom)
        tag = "%s/%s" % (label, tlabel)
        check(tag + ": SUCCESS", st == OK, err)
        if st != OK:
            img.delete()
            continue
        group, tl = texts(img)
        check(tag + ": 2 warstwy tekstowe + 2 obrysy w grupie",
              len(tl) == 2 and len(group.get_children()) == 4,
              [c.get_name() for c in group.get_children()])
        check(tag + ": tekst w obrazie", all(inside(t, img) for t in tl),
              [(t.get_offsets()[1:], t.get_width(), t.get_height()) for t in tl])
        check(tag + ": wielkie litery", tl[0].get_text() == tl[0].get_text().upper())
        check(tag + ": czcionka Anton", tl[0].get_font().get_name().startswith("Anton"),
              tl[0].get_font().get_name())
        check(tag + ": zdjęcie nietknięte",
              bg.is_valid() and bg.get_pixel(w // 2, h // 2).get_rgba() == px_before
              and img.get_layers()[-1] == bg)
        if tlabel == "polskie":
            check(tag + ": polskie znaki", "ZAŻÓŁĆ GĘŚLĄ JAŹŃ" in
                  tl[0].get_text().replace("\n", " ") or
                  "ZAŻÓŁĆ GĘŚLĄ JAŹŃ" in tl[1].get_text().replace("\n", " "),
                  [t.get_text() for t in tl])
        if tlabel == "długi" and label != "małe":
            check(tag + ": długi tekst zawinięty", "\n" in tl[0].get_text())
        save_preview(img, "%s_%s.png" % (label, tlabel))
        img.delete()

# --- obrys: grubość i kolejność ---------------------------------------------
img, bg = photo(1000, 600)
run(MEME, img, [bg], top_text="OBRYS", outline_percent=10.0, font_size=100.0)
group, tl = texts(img)
kids = group.get_children()
stroke = kids[kids.index(tl[0]) + 1] if tl[0] in kids else None
check("obrys pod tekstem", stroke is not None and not stroke.is_text_layer()
      and "obrys" in stroke.get_name(), [k.get_name() for k in kids])
_, tx, ty = tl[0].get_offsets()
# Piksel tuż nad górną krawędzią warstwy tekstu (w obrębie obrysu 10 px)
# – warstwa obrysu powinna tam coś mieć, a tekst nie.
check("obrys rozszerza kształt liter",
      stroke is not None and any(stroke.get_pixel(x, y).get_rgba()[3] > 0.5
                                 for x in range(tx, tx + tl[0].get_width(), 3)
                                 for y in range(ty - 8, ty + 40, 4)))
check("rozmiar ręczny 100 px", abs(tl[0].get_font_size()[0] - 100) < 0.5)
check("domyślny kolor tekstu biały", tl[0].get_color().get_rgba()[:4] == (1.0, 1.0, 1.0, 1.0),
      tl[0].get_color().get_rgba())
# Piksel obrysu: z dala od tekstu nic, przy literach czarny i nieprzezroczysty.
opaque = [stroke.get_pixel(x, y).get_rgba() for x in range(tx, tx + tl[0].get_width(), 3)
          for y in range(ty, ty + tl[0].get_height(), 3)]
opaque = [p for p in opaque if p[3] > 0.99]
check("domyślny kolor obrysu czarny", opaque and all(p[0] < 0.01 and p[1] < 0.01
                                                   and p[2] < 0.01 for p in opaque))
img.delete()

img, bg = photo(1000, 600)
run(MEME, img, [bg], top_text="KOLOR", text_color=Gegl.Color.new("red"))
_, tl = texts(img)
r, g, b, a = tl[0].get_color().get_rgba()
check("własny kolor tekstu (czerwony)", r > 0.99 and g < 0.01 and b < 0.01, (r, g, b, a))
img.delete()

img, bg = photo(1000, 600)
run(MEME, img, [bg], top_text="BEZ OBRYSU", outline_percent=0.0)
group, tl = texts(img)
check("obrys 0 -> brak warstwy obrysu", len(group.get_children()) == 1)
img.delete()

# --- wyrównanie --------------------------------------------------------------
for align in ("left", "center", "right"):
    img, bg = photo(1000, 600)
    run(MEME, img, [bg], top_text="ABC", align=align, margin_percent=5.0)
    _, tl = texts(img)
    _, x, _ = tl[0].get_offsets()
    m, w = 30, tl[0].get_width()
    exp = {"left": m, "center": m + (940 - w) // 2, "right": 1000 - m - w}[align]
    check("wyrównanie %s" % align, x == exp, (x, exp))
    img.delete()

# --- pasek nad zdjęciem ------------------------------------------------------
img, bg = photo(1000, 600)
st, err = run(MEME, img, [bg], top_text="Kiedy kod działa za pierwszym razem",
              bottom_text="dół", bar="top", bar_color="white", uppercase=False)
group, tl = texts(img)
_, bx, by = bg.get_offsets()
bar_h = img.get_height() - 600
top = [t for t in tl if t.get_name() == "Tekst górny"][0]
r, g, b, a = top.get_color().get_rgba()
check("pasek: SUCCESS", st == OK, err)
check("pasek: płótno wyższe, zdjęcie przesunięte w dół", bar_h > 0 and (bx, by) == (0, bar_h),
      (img.get_height(), bx, by))
check("pasek: biały na dole stosu", img.get_layers()[-1].get_name() == "Mem – pasek"
      and img.get_layers()[-1].get_pixel(5, 5).get_rgba()[:3] == (1.0, 1.0, 1.0))
check("pasek: tekst górny czarny, w pasku", r < 0.01 and top.get_offsets()[2] +
      top.get_height() <= bar_h, (r, g, b, top.get_offsets()))
check("pasek: bez WIELKICH LITER", top.get_text().startswith("Kiedy"))
check("pasek: tekst w pasku bez obrysu", len(group.get_children()) == 3)
save_preview(img, "pasek_gora.png")
img.delete()

img, bg = photo(1000, 600)
st, err = run(MEME, img, [bg], bottom_text="czarny pasek", bar="bottom",
              bar_color="black")
check("pasek dolny czarny", st == OK and img.get_height() > 600
      and bg.get_offsets()[1:] == (0, 0), err)
save_preview(img, "pasek_dol.png")
img.delete()

# --- spłaszczenie -----------------------------------------------------------
img, bg = photo(1000, 600)
st, err = run(MEME, img, [bg], top_text="płasko", bottom_text="mem",
              editable=False, bar="top")
names = [l.get_name() for l in img.get_layers()]
check("spłaszczony: warstwa Mem + nietknięte zdjęcie", st == OK and names == ["Mem", "zdjęcie"]
      and not img.get_layers()[0].is_text_layer(), names)
img.delete()

# --- własna czcionka (np. brak Impacta – wybór ręczny) ----------------------
img, bg = photo(1000, 600)
sans = Gimp.Font.get_by_name("Sans-serif Bold")
st, err = run(MEME, img, [bg], top_text="sans", font_mode="custom", font=sans)
_, tl = texts(img)
check("własna czcionka Sans-serif Bold", st == OK and tl[0].get_font().get_name() == "Sans-serif Bold",
      tl[0].get_font().get_name() if st == OK else err)
img.delete()

# --- błędy ------------------------------------------------------------------
img, bg = photo(400, 300)
st, err = run(MEME, img, [bg], top_text="  \n ", bottom_text="")
check("pusty tekst -> błąd, obraz bez zmian",
      st == Gimp.PDBStatusType.EXECUTION_ERROR and len(img.get_layers()) == 1
      and img.get_height() == 300, err)
st, _ = run(MEME, img, [bg], top_text="po błędzie")
check("po błędzie kolejny mem działa", st == OK and len(img.get_layers()) == 2)
img.delete()

# --- eksport ----------------------------------------------------------------
img, bg = photo(800, 500)
run(MEME, img, [bg], top_text="eksport", bottom_text="test")
n_layers = len(img.get_layers())
jpg_lo = os.path.join(OUT, "eksport_q30.jpg")
jpg_hi = os.path.join(OUT, "eksport_q95.jpg")
png = os.path.join(OUT, "eksport.png")
for path, fmt, q in ((jpg_lo, "jpg", 30), (jpg_hi, "jpg", 95), (png, "png", 90)):
    if os.path.exists(path):
        os.remove(path)
    st, err = run("python-fu-meme-export", img, [bg],
                  file=Gio.File.new_for_path(path), format=fmt, quality=q)
    check("eksport %s q%d" % (fmt, q), st == OK and os.path.getsize(path) > 0, err)
check("eksport: jakość wpływa na rozmiar", os.path.getsize(jpg_lo) < os.path.getsize(jpg_hi),
      (os.path.getsize(jpg_lo), os.path.getsize(jpg_hi)))
check("eksport: obraz w GIMP-ie bez zmian", len(img.get_layers()) == n_layers)
wrong = os.path.join(OUT, "zle_rozszerzenie.png")
st, _ = run("python-fu-meme-export", img, [bg], file=Gio.File.new_for_path(wrong),
            format="jpg")
check("eksport: rozszerzenie poprawione na .jpg",
      st == OK and os.path.exists(os.path.join(OUT, "zle_rozszerzenie.jpg")))
img.delete()

print("WYNIK: %d/%d PASS" % (sum(results), len(results)), flush=True)
