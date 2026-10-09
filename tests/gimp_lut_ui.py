# -*- coding: utf-8 -*-
"""
Test okna wtyczki LUT w procesie GIMP-a – okno budowane poza ekranem
(Gtk.OffscreenWindow), akcje wywoływane tak jak przez przyciski/klawisze.
Uruchom:
  gimp-console-3.2.exe -i --batch-interpreter python-fu-eval ^
      -b "exec(open(r'C:\\...\\tests\\gimp_lut_ui.py', encoding='utf-8').read())" --quit

Biblioteka testowa powstaje w katalogu tymczasowym (prawdziwa biblioteka
użytkownika nie jest ruszana). Zrzuty okna trafiają do docs/lut-cube/,
zestawienie wszystkich LUT-ów do docs/lut-cube/local/ (poza repo).
"""
import os
import shutil
import sys
import tempfile
import time

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gimp, GimpUi, Gio, Gtk, Gdk, GdkPixbuf  # noqa: E402
import cairo  # noqa: E402

REPO = r"C:\Users\Marek\Claude\Gimp"
PLUGIN_DIR = os.path.join(os.environ["APPDATA"], "GIMP", "3.2", "plug-ins",
                          "lut-cube")       # ma startup-dir.txt z install.ps1
IMG_DIR = os.path.join(tempfile.gettempdir(), "claude",
                       "C--Users-Marek-Claude-Gimp",
                       "d8876034-fa45-4d83-bfcb-61353e088c30", "scratchpad", "img")
DOCS = os.path.join(REPO, "docs", "lut-cube")
sys.path.insert(0, os.path.join(REPO, "lut-cube"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import gimp_ops  # noqa: E402
import library  # noqa: E402
import lut_apply  # noqa: E402
import sample_image  # noqa: E402
import ui  # noqa: E402

results = []


def check(name, cond, info=""):
    results.append(bool(cond))
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, info), flush=True)


def pump(seconds=0.0):
    end = time.perf_counter() + seconds
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration()
        if time.perf_counter() >= end:
            break
        time.sleep(0.01)


def settle(win, timeout=30):
    """Czeka na podgląd i miniatury (debounce + zadania w tle)."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        pump(0.05)
        if win._update_job is None and win._thumb_job is None:
            return True
    return False


def snapshot(win, name):
    pump(0.2)
    pb = win.offscreen_window.get_pixbuf()
    os.makedirs(DOCS, exist_ok=True)
    path = os.path.join(DOCS, name)
    pb.savev(path, "png", [], [])
    return path


def pb_bytes(pb):
    return pb.get_pixels() if pb is not None else b""


def key_event(keyval, kind=Gdk.EventType.KEY_PRESS):
    ev = Gdk.Event.new(kind)
    ev.keyval = keyval
    return ev


# --- atrapy okien dialogowych (zaplanowane odpowiedzi) -------------------------
answers = []
messages = []
ui._ask = lambda parent, text, secondary, buttons, kind=None: answers.pop(0)
ui._message = lambda parent, text, secondary="", kind=None: messages.append(
    (text, secondary))
ui._ask_text = lambda parent, title, label, value="": answers.pop(0)
chosen = []
ui._choose = lambda *a, **k: chosen.pop(0)

lib_root = os.path.join(tempfile.gettempdir(), "lut-cube test biblioteka ąę")
shutil.rmtree(lib_root, ignore_errors=True)
gimp_ops.library_root = lambda: lib_root

GimpUi.init("lut-cube")

# 1. Pierwsze uruchomienie: pusta biblioteka → import startowy z folderu Luts
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                     Gio.File.new_for_path(os.path.join(IMG_DIR, "poziome.png")))
layer = img.get_layers()[0]
answers.append(1)                       # „Importuj”
t = time.perf_counter()
win = ui.LutWindow(PLUGIN_DIR, img, [layer], None, offscreen=True)
pump(0.3)
check("import startowy: 49 LUT-ów", len(win.lib.entries) == 49,
      str(len(win.lib.entries)))
check("import startowy: komunikat podsumowania",
      messages and "Dodano: 49" in messages[-1][1],
      messages[-1][1].replace("\n", " | ") if messages else "")
names = [win.lib.display_name(k) for k in win.lib.query()]
check("prefiks skrócony w nazwach", names[0] == "001 Faded Matinee", names[0])
check("pliki .cube skopiowane do biblioteki",
      len(os.listdir(os.path.join(lib_root, "luts"))) == 49)
ok = settle(win, 60)
dt = time.perf_counter() - t
thumbs = sum(1 for row in win.store if row[1] is not None)
check("miniatury dla wszystkich 49 LUT-ów", ok and thumbs == 49,
      "%d miniatur, %.1f s od otwarcia okna" % (thumbs, dt))

# 2. Wybór LUT-a → podgląd się zmienia
settle(win)                       # po imporcie okno samo wybiera 1. LUT
first = win.current_key
check("po imporcie wybrany pierwszy LUT", first == win.lib.query()[0], str(first))
check("podgląd po ≠ przed", pb_bytes(win.after_pb) != pb_bytes(win.before_pb))
snapshot(win, "okno-suwak.png")

# 3. Nawigacja klawiaturą: strzałka w dół z pola wyszukiwania
win.search.grab_focus()
handled = win._on_key_press(win.dialog, key_event(Gdk.KEY_Down))
settle(win)
check("strzałka ↓ zmienia LUT", handled and win.current_key != first)
t = time.perf_counter()
for _ in range(5):
    win._move_selection(1)
    win._update_preview()
per = (time.perf_counter() - t) / 5 * 1000
check("przeliczenie podglądu", per < 1000, "%.0f ms na LUT (%d×%d)"
      % (per, win.fit_w, win.fit_h))

# 4. Trzymanie spacji = oryginał
win.tree.grab_focus()
win._on_key_press(win.dialog, key_event(Gdk.KEY_space))
check("spacja wciśnięta: pokazuje oryginał", win.hold_before)
win._on_key_release(win.dialog, key_event(Gdk.KEY_space, Gdk.EventType.KEY_RELEASE))
check("spacja puszczona: pokazuje efekt", not win.hold_before)

# 5. Siła i mieszanie
after_full = pb_bytes(win.after_pb)
win.strength.set_value(0)
win._update_preview()
check("siła 0 %: podgląd = oryginał",
      pb_bytes(win.after_pb) == pb_bytes(win.before_pb))
win.strength.set_value(60)
win.luminance.set_value(0)
win._update_preview()
mid = pb_bytes(win.after_pb)
check("siła 60 % + tylko kolor: podgląd inny niż 0 i 100 %",
      mid != after_full and mid != pb_bytes(win.before_pb))
win.luminance.set_value(100)
win.strength.set_value(80)

# 6. Tryby podglądu i zoom
win.mode_combo.set_active_id("side")
settle(win)
snapshot(win, "okno-obok-siebie.png")
win.mode_combo.set_active_id("split")
win.vertical_check.set_active(True)
win.split = 0.4
settle(win)
snapshot(win, "okno-suwak-pionowy.png")
win.vertical_check.set_active(False)
win.zoom_combo.set_active_id("100")
settle(win)
check("zoom 100 %: wycinek w pełnej rozdzielczości",
      win.crop is not None and win.crop[2] <= layer.get_width(),
      str(win.crop[:4]) if win.crop else "")
win.zoom_combo.set_active_id("fit")
settle(win)
win.mode_combo.set_active_id("toggle")
settle(win)
win.before_toggle.set_active(True)
check("przełącznik: pokazuje oryginał", win.before_toggle.get_active())
win.before_toggle.set_active(False)
win.mode_combo.set_active_id("split")

# 7. Edycja biblioteki: nazwa, kategoria, ulubione, wyszukiwarka
key = win.current_key
answers.append("Mój ulubiony ąę")
win._on_rename()
check("zmiana nazwy", win.lib.display_name(key) == "Mój ulubiony ąę")
win.lib.add_category("Ciepłe")
win.lib.set_category(key, "Ciepłe")
win._fill_categories()
win._on_toggle_favorite()
check("ulubiony na górze listy", win.store[0][0] == key and win.store[0][2] == "★")
win.category_combo.set_active_id("cat:Ciepłe")
pump(0.1)
check("filtr kategorii", len(win.store) == 1)
win.category_combo.set_active_id("")
win.search.set_text("matinee")
pump(0.5)
check("wyszukiwarka", len(win.store) == 1 and "Matinee" in win.store[0][3],
      win.store[0][3] if len(win.store) else "")
win.search.set_text("")
pump(0.5)

# 8. Duplikaty przy ponownym imporcie
chosen.append([os.path.join(REPO, "Luts")])
win._on_add_folder()
check("deduplikacja po hashu",
      "Pominięto duplikaty (ta sama treść pliku): 49" in messages[-1][1])

# 9. Plik spoza biblioteki → nałożenie bez dodawania, potem dodanie
tmp = os.path.join(tempfile.gettempdir(), "Własny LUT ż.cube")
with open(tmp, "w", encoding="utf-8") as f:
    f.write("TITLE \"odwrócony\"\nLUT_3D_SIZE 2\n")
    for b in (0, 1):
        for g in (0, 1):
            for r in (0, 1):
                f.write("%d %d %d\n" % (1 - r, 1 - g, 1 - b))
chosen.append([tmp])
win._on_load_file()
settle(win)
check("plik spoza biblioteki wybrany",
      win.current_key == ui.FILE_PREFIX + tmp and len(win.lib.entries) == 49)
win._on_add_temp()
check("„Dodaj do biblioteki”", len(win.lib.entries) == 50 and win.temp_path is None)

# 10. Usuwanie (tylko z listy)
win._on_remove = win._on_remove   # okno potwierdzenia – wywołujemy bibliotekę
removed = win.current_key
path = win.lib.path(removed)
win.lib.remove(removed, delete_file=True)
win.current_key = None
win._refill()
check("usunięcie z pliku", not os.path.exists(path) and len(win.lib.entries) == 49)

# 11. Nałożenie z okna (nowa warstwa, postęp) – LUT o zmienionej nazwie
first = key
win._select_key(first)
settle(win)
progress_calls = []
orig_progress = win._progress
win._progress = lambda f: progress_calls.append(f) or orig_progress(f)
t = time.perf_counter()
ok = win.apply()
check("Zastosuj: nowa warstwa", ok and len(img.get_layers()) == 2,
      "%.2f s" % (time.perf_counter() - t))
check("Zastosuj: nazwa warstwy",
      img.get_layers()[0].get_name() == "LUT: Mój ulubiony ąę 80%",
      img.get_layers()[0].get_name())
check("Zastosuj: pasek postępu", progress_calls and progress_calls[-1] == 1.0)
check("ostatnio użyty zapamiętany", win.lib.recent[0] == first)
win._store_settings()
win.close()
again = library.Library(lib_root)
check("ustawienia zapamiętane (siła, ostatni LUT)",
      again.settings["strength"] == 80.0 and again.settings["last_lut"] == first)

# 12. Przerwanie: obraz bez zmian
win = ui.LutWindow(PLUGIN_DIR, img, [img.get_layers()[1]], None, offscreen=True)
settle(win)
win._progress = lambda f: False
layers_before = len(img.get_layers())
ok = win.apply()
check("Przerwij: brak zmian", not ok and len(img.get_layers()) == layers_before)
win.close()
img.delete()

# 13. Zaznaczenie w podglądzie (miękka maska)
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                     Gio.File.new_for_path(os.path.join(IMG_DIR, "pionowe.png")))
img.select_ellipse(Gimp.ChannelOps.REPLACE, 200, 300, 800, 1000)
Gimp.Selection.feather(img, 80)
win = ui.LutWindow(PLUGIN_DIR, img, img.get_layers(), None, offscreen=True)
settle(win)
check("podgląd z maską zaznaczenia", win.fit_mask is not None)
win.mode_combo.set_active_id("side")
settle(win)
snapshot(win, "okno-zaznaczenie.png")
win.close()
img.delete()

# 14. Obraz z alfą, bardzo mały i 16-bitowy – okno się otwiera, podgląd działa
for name in ("alfa.png", "male.png", "16bit.png"):
    img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                         Gio.File.new_for_path(os.path.join(IMG_DIR, name)))
    win = ui.LutWindow(PLUGIN_DIR, img, img.get_layers(), None, offscreen=True)
    ok = settle(win)
    check("okno: %s" % name, ok and win.current_lut is not None
          and pb_bytes(win.after_pb) != pb_bytes(win.before_pb),
          "%d×%d" % (win.fit_w, win.fit_h))
    if name == "alfa.png":
        snapshot(win, "okno-alfa.png")
    win.close()
    img.delete()

# 15. Profil inny niż sRGB → ostrzeżenie i konwersja
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                     Gio.File.new_for_path(os.path.join(IMG_DIR, "male.png")))
img.set_color_profile(Gimp.ColorProfile.new_from_file(Gio.File.new_for_path(
    r"C:\Program Files\Common Files\Adobe\Color\Profiles\Recommended\AdobeRGB1998.icc")))
win = ui.LutWindow(PLUGIN_DIR, img, img.get_layers(), None, offscreen=True)
settle(win)
check("Adobe RGB: ostrzeżenie widoczne", win.srgb_bar.get_visible())
win._on_convert_srgb(win.srgb_bar, 1)
check("Adobe RGB: po konwersji sRGB", gimp_ops.profile_info(img)[0]
      and not win.srgb_bar.get_visible())
win.close()
img.delete()

# 16. Skala szarości → propozycja konwersji do RGB
img = Gimp.Image.new(50, 40, Gimp.ImageBaseType.GRAY)
img.insert_layer(Gimp.Layer.new(img, "s", 50, 40, Gimp.ImageType.GRAY_IMAGE, 100,
                                Gimp.LayerMode.NORMAL), None, 0)
answers.append(1)
check("skala szarości → RGB po zgodzie", ui._prepare_image(img)
      and img.get_base_type() == Gimp.ImageBaseType.RGB)
img.delete()

# 17. Okno biblioteki (bez obrazu, obrazek testowy)
win = ui.LutWindow(PLUGIN_DIR, offscreen=True)
settle(win, 60)
check("okno biblioteki: obrazek testowy i miniatury",
      isinstance(win.source, ui.SampleSource)
      and all(row[1] is not None for row in win.store))
win._select_key(win.lib.query()[3])
settle(win)
snapshot(win, "okno-biblioteka.png")
win.close()

# 18. Zestawienie kontrolne: obraz testowy przez wszystkie LUT-y
lib = library.Library(lib_root)
src_w, src_h = 240, 160
src = sample_image.make(src_w, src_h)
keys = sorted(lib.entries, key=lambda k: lib.entries[k]["file"])
cols, pad, label_h = 7, 8, 18


def sheet(keys, path, cols):
    rows = (len(keys) + 1 + cols - 1) // cols
    W = cols * (src_w + pad) + pad
    H = rows * (src_h + label_h + pad) + pad
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.15, 0.15, 0.15)
    cr.paint()
    items = [("Oryginał", src)]
    bad = []
    for k in keys:
        try:
            out, _ = lut_apply.apply_rgba(lib.load_lut(k), src, lut_apply.Params())
        except Exception as e:  # noqa
            bad.append((k, e))
            continue
        stem = os.path.splitext(lib.entries[k]["file"])[0]
        items.append((library.short_name(stem, library.DEFAULT_PREFIX, True), out))
    for i, (name, data) in enumerate(items):
        x = pad + (i % cols) * (src_w + pad)
        y = pad + (i // cols) * (src_h + label_h + pad)
        pb = GdkPixbuf.Pixbuf.new_from_bytes(
            __import__("gi.repository.GLib", fromlist=["GLib"]).Bytes.new(
                lut_apply.to_rgba8(data)),
            GdkPixbuf.Colorspace.RGB, True, 8, src_w, src_h, src_w * 4)
        Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
        cr.paint()
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.set_font_size(12)
        cr.move_to(x + 2, y + src_h + 13)
        cr.show_text(name[:34])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    surf.write_to_png(path)
    return items, bad


items, bad = sheet(keys, os.path.join(DOCS, "local", "kontrola-49-lutow.png"), cols)
check("zestawienie: wszystkie 49 LUT-ów wczytane", len(items) == 50 and not bad,
      str(bad))
flat = [lut_apply.to_rgba8(d) for _n, d in items]
check("zestawienie: każdy LUT zmienia obraz",
      all(f != flat[0] for f in flat[1:]))
sheet(keys[:7], os.path.join(DOCS, "przyklady-lutow.png"), 4)

shutil.rmtree(lib_root, ignore_errors=True)
print("WYNIK: %d/%d PASS" % (sum(results), len(results)), flush=True)
