# -*- coding: utf-8 -*-
"""
Okno wtyczki LUT: biblioteka LUT-ów, podgląd przed/po, siła efektu.

Standardowy GimpUi.ProcedureDialog nie ma podglądu dla własnego
przetwarzania pikseli, dlatego okno jest zbudowane w GTK3. Podgląd liczymy
na zmniejszonej kopii warstwy (GEGL skaluje przy odczycie), a w trybie
100 % – na wycinku w pełnej rozdzielczości.
"""

import os
import time
import urllib.parse

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gimp, GimpUi, Gtk, Gdk, GdkPixbuf, GLib, Gio, Pango  # noqa: E402
import cairo  # noqa: E402

import cube_parser  # noqa: E402
import gimp_ops  # noqa: E402
import library  # noqa: E402
import lut_apply  # noqa: E402
import sample_image  # noqa: E402
from cube_parser import CubeError  # noqa: E402

THUMB_SIDE = 64
DEBOUNCE_MS = 120
RESP_APPLY = 1
FILE_PREFIX = "file:"
SPLIT_GRAB = 10          # px od linii podziału, w których „łapiemy” suwak

PREVIEW_MODES = [("split", "Suwak przed/po"), ("side", "Obok siebie"),
                 ("toggle", "Przełącznik przed/po")]
INTERPOLATIONS = [("tetrahedral", "Tetraedralna (dokładniejsza)"),
                  ("trilinear", "Trójliniowa")]


# --- pomocnicze ----------------------------------------------------------------

def _pixbuf(rgba8, w, h):
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgba8),
                                           GdkPixbuf.Colorspace.RGB, True, 8,
                                           w, h, w * 4)


def _message(parent, text, secondary="", kind=Gtk.MessageType.INFO):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                            message_type=kind, buttons=Gtk.ButtonsType.OK,
                            text=text)
    if secondary:
        dlg.format_secondary_text(secondary)
    dlg.run()
    dlg.destroy()


def _ask(parent, text, secondary, buttons, kind=Gtk.MessageType.QUESTION):
    """buttons: [(etykieta, odpowiedź), …]. Zwraca odpowiedź."""
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                            message_type=kind, buttons=Gtk.ButtonsType.NONE,
                            text=text)
    if secondary:
        dlg.format_secondary_text(secondary)
    for label, resp in buttons:
        dlg.add_button(label, resp)
    resp = dlg.run()
    dlg.destroy()
    return resp


def _ask_text(parent, title, label, value=""):
    dlg = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dlg.add_buttons("_Anuluj", Gtk.ResponseType.CANCEL, "_OK", Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)
    box = dlg.get_content_area()
    box.set_spacing(6)
    box.set_border_width(12)
    box.add(Gtk.Label(label=label, xalign=0))
    entry = Gtk.Entry(text=value, activates_default=True, width_chars=40)
    box.add(entry)
    dlg.show_all()
    resp = dlg.run()
    text = entry.get_text().strip()
    dlg.destroy()
    return text if resp == Gtk.ResponseType.OK else None


def _choose(parent, title, action, settings, multiple=False, cube_filter=True):
    """Natywne okno wyboru plików/folderu. Zwraca listę ścieżek."""
    accept = "_Wybierz" if action == Gtk.FileChooserAction.SELECT_FOLDER else "_Otwórz"
    chooser = Gtk.FileChooserNative.new(title, parent, action, accept, "_Anuluj")
    chooser.set_select_multiple(multiple)
    if cube_filter and action == Gtk.FileChooserAction.OPEN:
        flt = Gtk.FileFilter()
        flt.set_name("LUT .cube")
        flt.add_pattern("*.cube")
        flt.add_pattern("*.CUBE")
        chooser.add_filter(flt)
    last = settings.get("last_dir")
    if last and os.path.isdir(last):
        chooser.set_current_folder(last)
    paths = []
    if chooser.run() == Gtk.ResponseType.ACCEPT:
        paths = [p for p in chooser.get_filenames() if p]
    chooser.destroy()
    if paths:
        first = paths[0]
        settings["last_dir"] = (first if action == Gtk.FileChooserAction.SELECT_FOLDER
                                else os.path.dirname(first))
    return paths


def _combo(items, active):
    combo = Gtk.ComboBoxText()
    for nick, label in items:
        combo.append(nick, label)
    if not combo.set_active_id(active):
        combo.set_active(0)
    return combo


# --- źródła obrazu do podglądu ----------------------------------------------------

class ImageSource:
    """Podgląd z warstwy obrazu GIMP-a."""

    def __init__(self, image, layer):
        self.image, self.layer = image, layer
        self.width, self.height = layer.get_width(), layer.get_height()

    def fit(self, max_side):
        data, mask, w, h, _scale = gimp_ops.read_scaled(self.image, self.layer,
                                                        max_side)
        return data, mask, w, h

    def crop(self, cx, cy, w, h):
        data, mask, x, y, w, h = gimp_ops.read_crop(
            self.image, self.layer, int(cx - w / 2), int(cy - h / 2), w, h)
        return data, mask, x, y, w, h


class SampleSource:
    """Wbudowany obrazek testowy (gdy nie ma otwartego obrazu)."""

    W, H = 720, 480

    def __init__(self):
        self.width, self.height = self.W, self.H
        self._data = sample_image.make(self.W, self.H)

    def fit(self, max_side):
        if max_side >= max(self.W, self.H):
            return self._data, None, self.W, self.H
        w = max(1, self.W * max_side // max(self.W, self.H))
        h = max(1, self.H * max_side // max(self.W, self.H))
        return sample_image.make(w, h), None, w, h

    def crop(self, cx, cy, w, h):
        return self._data, None, 0, 0, self.W, self.H


# --- okno --------------------------------------------------------------------------

class LutWindow:

    def __init__(self, plugin_dir, image=None, layers=None, config=None,
                 offscreen=False):
        self.plugin_dir = plugin_dir
        self.offscreen = offscreen     # tylko testy: okno poza ekranem
        self.image = image
        self.layers = layers or []
        self.config = config
        self.lib = library.Library(gimp_ops.library_root())
        self.settings = self.lib.settings
        self.use_numpy = True
        self.numpy_ok = lut_apply.get_numpy() is not None
        self.preview_max = 1100 if self.numpy_ok else 640

        self.current_key = None        # id z biblioteki albo "file:<ścieżka>"
        self.current_lut = None
        self.temp_path = None          # plik wczytany spoza biblioteki
        self.thumbs = {}               # klucz -> Pixbuf
        self._thumb_job = None
        self._update_job = None
        self._cancel = False
        self._busy = False
        self.hold_before = False
        self.applied = False

        self.source = (ImageSource(image, self.layers[0]) if image is not None
                       else SampleSource())
        self.zoom_full = False
        self.split = 0.5
        self.view_center = (self.source.width / 2.0, self.source.height / 2.0)
        self._drag = None
        self._img_rects = []
        self.crop = None               # (x, y, w, h, before_pb, after_pb)
        self._crop_data = None

        self._build()
        self._load_source()

    # --- budowa okna -------------------------------------------------------------

    def _build(self):
        title = ("Zastosuj LUT (.cube)" if self.image is not None
                 else "Biblioteka LUT-ów")
        self.dialog = dlg = Gtk.Dialog(title=title)
        GimpUi.window_set_transient(dlg)
        dlg.set_default_size(1240, 800)
        if self.image is not None:
            dlg.add_button("_Anuluj", Gtk.ResponseType.CANCEL)
            self.apply_button = dlg.add_button("_Zastosuj", RESP_APPLY)
            dlg.set_default_response(RESP_APPLY)
        else:
            dlg.add_button("_Zamknij", Gtk.ResponseType.CLOSE)
            self.apply_button = None
        dlg.connect("key-press-event", self._on_key_press)
        dlg.connect("key-release-event", self._on_key_release)
        dlg.connect("delete-event", lambda *_: self._busy)   # nie zamykaj w trakcie

        self.paned = paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(360)
        area = dlg.get_content_area()
        area.set_border_width(6)
        area.set_spacing(6)
        area.pack_start(paned, True, True, 0)
        paned.pack1(self._build_library_panel(), False, False)
        paned.pack2(self._build_preview_panel(), True, False)

        # Postęp nakładania – poza panelem, który na ten czas jest wyszarzony.
        self.progress_box = Gtk.Box(spacing=6)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress_box.pack_start(self.progress, True, True, 0)
        stop = Gtk.Button(label="Przerwij")
        stop.connect("clicked", self._on_cancel_apply)
        self.progress_box.pack_start(stop, False, False, 0)
        area.pack_start(self.progress_box, False, False, 0)
        if self.offscreen:
            # Testy automatyczne: zawartość okna w oknie poza ekranem.
            area.remove(paned)
            area.remove(self.progress_box)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.pack_start(paned, True, True, 0)
            box.pack_start(self.progress_box, False, False, 0)
            self.offscreen_window = Gtk.OffscreenWindow()
            self.offscreen_window.set_default_size(1240, 760)
            self.offscreen_window.add(box)
            self.offscreen_window.show_all()
        else:
            dlg.show_all()
        self.progress_box.hide()
        self._update_mode_widgets()
        self._update_srgb_bar()

    def _build_library_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.search = Gtk.SearchEntry(placeholder_text="Szukaj LUT-a…")
        self.search.connect("search-changed", lambda *_: self._refill())
        box.pack_start(self.search, False, False, 0)

        row = Gtk.Box(spacing=4)
        self.category_combo = Gtk.ComboBoxText()
        self.category_combo.connect("changed", self._on_filter_changed)
        row.pack_start(self.category_combo, True, True, 0)
        self.fav_toggle = Gtk.ToggleButton(label="★ Ulubione")
        self.fav_toggle.set_tooltip_text("Pokaż tylko ulubione")
        self.fav_toggle.set_active(self.settings["favorites_only"])
        self.fav_toggle.connect("toggled", self._on_filter_changed)
        row.pack_start(self.fav_toggle, False, False, 0)
        self.sort_combo = _combo([("name", "Nazwa"), ("date", "Data dodania")],
                                 self.settings["sort"])
        self.sort_combo.set_tooltip_text("Sortowanie (ulubione i ostatnio "
                                         "użyte zawsze na górze)")
        self.sort_combo.connect("changed", self._on_filter_changed)
        row.pack_start(self.sort_combo, False, False, 0)
        box.pack_start(row, False, False, 0)

        # klucz, miniatura, gwiazdka, nazwa, podpowiedź
        self.store = Gtk.ListStore(str, GdkPixbuf.Pixbuf, str, str, str)
        self.tree = tree = Gtk.TreeView(model=self.store, headers_visible=False)
        tree.set_enable_search(False)
        tree.set_tooltip_column(4)
        col = Gtk.TreeViewColumn("")
        cell = Gtk.CellRendererPixbuf()
        cell.set_fixed_size(THUMB_SIDE + 6, -1)
        col.pack_start(cell, False)
        col.add_attribute(cell, "pixbuf", 1)
        tree.append_column(col)
        self.star_column = Gtk.TreeViewColumn("")
        star = Gtk.CellRendererText()
        star.set_property("foreground", "#e0a000")
        self.star_column.pack_start(star, False)
        self.star_column.add_attribute(star, "text", 2)
        tree.append_column(self.star_column)
        name_col = Gtk.TreeViewColumn("")
        name = Gtk.CellRendererText()
        name.set_property("ellipsize", Pango.EllipsizeMode.END)
        name_col.pack_start(name, True)
        name_col.add_attribute(name, "markup", 3)
        tree.append_column(name_col)
        tree.get_selection().connect("changed", self._on_selection_changed)
        tree.connect("button-press-event", self._on_tree_button)
        tree.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        tree.drag_dest_add_uri_targets()
        tree.connect("drag-data-received", self._on_drop)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        box.pack_start(scroll, True, True, 0)

        grid = Gtk.Grid(column_spacing=4, row_spacing=4, column_homogeneous=True)
        b = Gtk.Button(label="Dodaj pliki…")
        b.set_tooltip_text("Kopiuje wybrane pliki .cube do biblioteki "
                           "(możesz też przeciągnąć pliki na listę)")
        b.connect("clicked", self._on_add_files)
        grid.attach(b, 0, 0, 1, 1)
        b = Gtk.Button(label="Dodaj folder…")
        b.set_tooltip_text("Dodaje wszystkie pliki .cube z folderu i podfolderów")
        b.connect("clicked", self._on_add_folder)
        grid.attach(b, 1, 0, 1, 1)
        b = Gtk.Button(label="Wczytaj plik .cube…")
        b.set_tooltip_text("Nakłada dowolny plik .cube bez dodawania do biblioteki")
        b.connect("clicked", self._on_load_file)
        grid.attach(b, 0, 1, 1, 1)
        self.add_temp_button = Gtk.Button(label="Dodaj do biblioteki")
        self.add_temp_button.set_tooltip_text("Dodaje wczytany plik do biblioteki")
        self.add_temp_button.connect("clicked", self._on_add_temp)
        grid.attach(self.add_temp_button, 1, 1, 1, 1)
        box.pack_start(grid, False, False, 0)

        more = Gtk.MenuButton(label="Więcej ▾")
        more.set_popup(self._build_menu(for_item=False))
        box.pack_start(more, False, False, 0)

        self.lib_status = Gtk.Label(xalign=0)
        self.lib_status.set_line_wrap(True)
        box.pack_start(self.lib_status, False, False, 0)

        self._fill_categories()
        return box

    def _build_menu(self, for_item):
        menu = Gtk.Menu()

        def item(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: cb())
            menu.append(mi)
            return mi

        lib_selected = (self.current_key is not None
                        and not self.current_key.startswith(FILE_PREFIX))
        for mi in (item("Zmień nazwę…", self._on_rename),
                   item("Kategoria…", self._on_set_category),
                   item("Ulubiony (włącz/wyłącz)", self._on_toggle_favorite),
                   item("Usuń z biblioteki…", self._on_remove)):
            mi.set_sensitive(lib_selected or not for_item)
        if not for_item:
            menu.append(Gtk.SeparatorMenuItem())
            item("Kategorie…", self._on_manage_categories)
            item("Ustawienia…", self._on_settings)
            item("Import startowy…", lambda: self._startup_import(force=True))
        menu.show_all()
        return menu

    def _build_preview_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.srgb_bar = Gtk.InfoBar(message_type=Gtk.MessageType.WARNING)
        self.srgb_label = Gtk.Label(xalign=0)
        self.srgb_label.set_line_wrap(True)
        self.srgb_bar.get_content_area().add(self.srgb_label)
        self.srgb_bar.add_button("Konwertuj do sRGB", 1)
        self.srgb_bar.connect("response", self._on_convert_srgb)
        box.pack_start(self.srgb_bar, False, False, 0)

        bar = Gtk.Box(spacing=6)
        self.mode_combo = _combo(PREVIEW_MODES, self.settings["preview_mode"])
        self.mode_combo.connect("changed", self._on_view_changed)
        bar.pack_start(self.mode_combo, False, False, 0)
        self.vertical_check = Gtk.CheckButton(label="Pionowo")
        self.vertical_check.set_tooltip_text("Suwak poziomy (góra: przed, dół: po)")
        self.vertical_check.set_active(self.settings["split_vertical"])
        self.vertical_check.connect("toggled", self._on_view_changed)
        bar.pack_start(self.vertical_check, False, False, 0)
        self.before_toggle = Gtk.ToggleButton(label="Pokaż oryginał")
        self.before_toggle.set_tooltip_text("Kliknij albo przytrzymaj spację / \\")
        self.before_toggle.connect("toggled", lambda *_: self.area.queue_draw())
        bar.pack_start(self.before_toggle, False, False, 0)
        self.zoom_combo = _combo([("fit", "Dopasuj do okna"), ("100", "100 %")],
                                 "fit")
        self.zoom_combo.set_tooltip_text("W trybie 100 % przeciągnij podgląd, "
                                         "żeby go przesunąć")
        self.zoom_combo.connect("changed", self._on_zoom_changed)
        bar.pack_end(self.zoom_combo, False, False, 0)
        box.pack_start(bar, False, False, 0)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(400, 300)
        self.area.set_can_focus(True)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                             | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_area_press)
        self.area.connect("button-release-event", self._on_area_release)
        self.area.connect("motion-notify-event", self._on_area_motion)
        self.area.connect("size-allocate", self._on_area_resized)
        box.pack_start(self.area, True, True, 0)

        box.pack_start(self._build_controls(), False, False, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(self.status, False, False, 0)
        return box

    def _slider(self, grid, row, label, value, tooltip):
        adj = Gtk.Adjustment(value=value, lower=0, upper=100, step_increment=1,
                             page_increment=10)
        lab = Gtk.Label(label=label, xalign=0)
        lab.set_tooltip_text(tooltip)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj,
                          draw_value=False, hexpand=True)
        scale.set_tooltip_text(tooltip)
        spin = Gtk.SpinButton(adjustment=adj, digits=0, width_chars=4)
        grid.attach(lab, 0, row, 1, 1)
        grid.attach(scale, 1, row, 1, 1)
        grid.attach(spin, 2, row, 1, 1)
        grid.attach(Gtk.Label(label="%"), 3, row, 1, 1)
        adj.connect("value-changed", lambda *_: self._schedule_update())
        return adj

    def _build_controls(self):
        grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        s = self.settings
        self.strength = self._slider(grid, 0, "Siła", s["strength"],
                                     "0 % = oryginał, 100 % = pełny LUT")
        expander = Gtk.Expander(label="Mieszanie: kolor / jasność")
        sub = Gtk.Grid(column_spacing=8, row_spacing=4)
        self.color = self._slider(sub, 0, "Kolor", s["color"],
                                  "Ile zmiany barwy przenieść z LUT-a")
        self.luminance = self._slider(sub, 1, "Jasność", s["luminance"],
                                      "Ile zmiany jasności/kontrastu przenieść "
                                      "(0 % = look bez zmiany kontrastu)")
        expander.add(sub)
        expander.set_expanded(s["color"] != 100.0 or s["luminance"] != 100.0)
        grid.attach(expander, 0, 1, 4, 1)

        grid.attach(Gtk.Label(label="Interpolacja", xalign=0), 0, 2, 1, 1)
        self.interp_combo = _combo(INTERPOLATIONS, s["interpolation"])
        self.interp_combo.connect("changed", lambda *_: self._schedule_update())
        grid.attach(self.interp_combo, 1, 2, 3, 1)

        if self.image is not None:
            grid.attach(Gtk.Label(label="Wynik", xalign=0), 0, 3, 1, 1)
            out = Gtk.Box(spacing=8)
            self.out_new = Gtk.RadioButton(label="Nowa warstwa z efektem")
            self.out_inplace = Gtk.RadioButton(label="Na aktywnej warstwie",
                                               group=self.out_new)
            (self.out_inplace if s["output"] == "in-place"
             else self.out_new).set_active(True)
            self.opacity_check = Gtk.CheckButton(label="Siła jako krycie warstwy")
            self.opacity_check.set_tooltip_text(
                "Warstwa dostaje pełny efekt, a siła trafia do krycia – "
                "można ją potem zmieniać w oknie Warstwy")
            self.opacity_check.set_active(s["strength_as_opacity"])
            self.out_new.connect("toggled", lambda *_: self._update_mode_widgets())
            for w in (self.out_new, self.out_inplace, self.opacity_check):
                out.pack_start(w, False, False, 0)
            grid.attach(out, 1, 3, 3, 1)
        return grid

    # --- dane podglądu -------------------------------------------------------------

    def _load_source(self):
        t = time.perf_counter()
        data, mask, w, h = self.source.fit(self.preview_max)
        self.fit_data, self.fit_mask, self.fit_w, self.fit_h = data, mask, w, h
        self.before_pb = _pixbuf(lut_apply.to_rgba8(data), w, h)
        self.after_pb = self.before_pb
        tdata, _tmask, tw, th = self.source.fit(THUMB_SIDE)
        self.thumb_src = (tdata, tw, th)
        self.thumbs.clear()
        self.crop = None
        self._read_ms = (time.perf_counter() - t) * 1000
        self._refill(select=self.settings["last_lut"])
        GLib.idle_add(self._startup_import)

    def params(self):
        return lut_apply.Params(self.strength.get_value() / 100.0,
                                self.color.get_value() / 100.0,
                                self.luminance.get_value() / 100.0,
                                self.interp_combo.get_active_id())

    def _schedule_update(self, delay=DEBOUNCE_MS):
        if self._update_job is not None:
            GLib.source_remove(self._update_job)
        self._update_job = GLib.timeout_add(delay, self._update_preview)

    def _update_preview(self):
        self._update_job = None
        if self._busy:
            return False
        t = time.perf_counter()
        params = self.params()
        if self.current_lut is None:
            self.after_pb = self.before_pb
        else:
            out, _ = lut_apply.apply_rgba(self.current_lut, self.fit_data, params,
                                          self.fit_mask, self.use_numpy)
            self.after_pb = _pixbuf(lut_apply.to_rgba8(out), self.fit_w, self.fit_h)
        if self.zoom_full:
            self._update_crop(params)
        ms = (time.perf_counter() - t) * 1000
        self._set_status(ms)
        self.area.queue_draw()
        return False

    def _update_crop(self, params):
        aw = max(1, self.area.get_allocated_width())
        ah = max(1, self.area.get_allocated_height())
        if self.mode_combo.get_active_id() == "side":
            if self.vertical_check.get_active():
                ah //= 2
            else:
                aw //= 2
        cx, cy = self.view_center
        key = (int(cx), int(cy), aw, ah)
        if self._crop_data is None or self._crop_data[0] != key:
            data, mask, x, y, w, h = self.source.crop(cx, cy, aw, ah)
            before = _pixbuf(lut_apply.to_rgba8(data), w, h)
            self._crop_data = (key, data, mask, x, y, w, h, before)
        _key, data, mask, x, y, w, h, before = self._crop_data
        after = before
        if self.current_lut is not None:
            out, _ = lut_apply.apply_rgba(self.current_lut, data, params, mask,
                                          self.use_numpy)
            after = _pixbuf(lut_apply.to_rgba8(out), w, h)
        self.crop = (x, y, w, h, before, after)

    def _set_status(self, ms=None):
        parts = []
        if self.current_lut is not None:
            parts.append("LUT %s, siatka %d" % (self.current_lut.kind,
                                                self.current_lut.grid_size))
        parts.append("podgląd %d×%d" % (self.fit_w, self.fit_h))
        if ms is not None:
            parts.append("%.0f ms" % ms)
        parts.append(lut_apply.backend_name(self.use_numpy))
        if self.image is not None and self.layers:
            parts.append("warstwa: %s" % self.layers[0].get_name())
            if len(self.layers) > 1:
                parts.append("(+%d warstw)" % (len(self.layers) - 1))
        self.status.set_text("  •  ".join(parts))

    # --- lista LUT-ów ----------------------------------------------------------------

    def _fill_categories(self):
        combo = self.category_combo
        combo.handler_block_by_func(self._on_filter_changed)
        combo.remove_all()
        combo.append("", "Wszystkie kategorie")
        for c in self.lib.categories:
            combo.append("cat:" + c, c)
        want = self.settings["category_filter"]
        if not combo.set_active_id("cat:" + want if want else ""):
            combo.set_active(0)
        combo.handler_unblock_by_func(self._on_filter_changed)

    def _on_filter_changed(self, *_):
        cid = self.category_combo.get_active_id() or ""
        self.settings["category_filter"] = cid[4:] if cid.startswith("cat:") else ""
        self.settings["favorites_only"] = self.fav_toggle.get_active()
        self.settings["sort"] = self.sort_combo.get_active_id() or "name"
        self._refill()

    def _row_markup(self, key):
        if key.startswith(FILE_PREFIX):
            name = os.path.splitext(os.path.basename(key[len(FILE_PREFIX):]))[0]
            return ("%s\n<small><i>spoza biblioteki</i></small>"
                    % GLib.markup_escape_text(name))
        e = self.lib.entries[key]
        extra = [e.get("category") or "", "%s %d" % (e.get("kind", "3D"),
                                                    e.get("size", 0))]
        return ("%s\n<small>%s</small>"
                % (GLib.markup_escape_text(self.lib.display_name(key)),
                   GLib.markup_escape_text("  ·  ".join(x for x in extra if x))))

    def _row_tooltip(self, key):
        if key.startswith(FILE_PREFIX):
            return key[len(FILE_PREFIX):]
        e = self.lib.entries[key]
        return "%s\nDodano: %s" % (e["file"], e.get("added", "").replace("T", " "))

    def _keys(self):
        keys = self.lib.query(self.search.get_text(),
                              self.settings["category_filter"],
                              self.settings["favorites_only"],
                              self.settings["sort"])
        if self.temp_path:
            keys.insert(0, FILE_PREFIX + self.temp_path)
        return keys

    def _refill(self, select=None):
        select = select or self.current_key
        sel = self.tree.get_selection()
        sel.handler_block_by_func(self._on_selection_changed)
        self.store.clear()
        for key in self._keys():
            fav = (not key.startswith(FILE_PREFIX)
                   and self.lib.entries[key].get("favorite"))
            self.store.append([key, self.thumbs.get(key), "★" if fav else "☆",
                               self._row_markup(key), self._row_tooltip(key)])
        sel.handler_unblock_by_func(self._on_selection_changed)
        n = len(self.lib.entries)
        self.lib_status.set_text("W bibliotece: %d LUT-ów. Strzałki ↑↓ – "
                                 "następny/poprzedni LUT." % n if n else
                                 "Biblioteka jest pusta – dodaj pliki .cube.")
        self.add_temp_button.set_sensitive(bool(self.temp_path))
        if select:
            self._select_key(select)
        self._start_thumbs()

    def _select_key(self, key):
        for row in self.store:
            if row[0] == key:
                self.tree.get_selection().select_iter(row.iter)
                self.tree.scroll_to_cell(row.path, None, False, 0, 0)
                return True
        return False

    def _move_selection(self, step):
        model, it = self.tree.get_selection().get_selected()
        n = len(self.store)
        if not n:
            return
        idx = model.get_path(it).get_indices()[0] + step if it else 0
        idx = max(0, min(n - 1, idx))
        path = Gtk.TreePath.new_from_indices([idx])
        self.tree.set_cursor(path, None, False)
        self.tree.scroll_to_cell(path, None, False, 0, 0)

    def _on_selection_changed(self, selection):
        model, it = selection.get_selected()
        if it is None:
            return
        key = model[it][0]
        if key == self.current_key and self.current_lut is not None:
            return
        self.current_key = key
        try:
            self.current_lut = self._load_lut(key)
        except CubeError as e:
            self.current_lut = None
            self.status.set_text("Błąd LUT-a: %s" % e)
            self.after_pb = self.before_pb
            self.area.queue_draw()
            return
        self._schedule_update(10)

    def _load_lut(self, key):
        if key.startswith(FILE_PREFIX):
            return cube_parser.parse_cube_file(key[len(FILE_PREFIX):])
        return self.lib.load_lut(key)

    def _label(self, key):
        if key.startswith(FILE_PREFIX):
            return os.path.splitext(os.path.basename(key[len(FILE_PREFIX):]))[0]
        return self.lib.display_name(key)

    # --- miniatury (leniwie, po jednej w czasie bezczynności) ---------------------

    def _start_thumbs(self):
        if self._thumb_job is None:
            self._thumb_job = GLib.idle_add(self._thumb_step,
                                            priority=GLib.PRIORITY_LOW)

    def _thumb_step(self):
        if self._busy:
            self._thumb_job = None
            return False
        data, w, h = self.thumb_src
        for row in self.store:
            key = row[0]
            if row[1] is not None:
                continue
            pb = self.thumbs.get(key)
            if pb is None:
                try:
                    lut = self._load_lut(key)
                    out, _ = lut_apply.apply_rgba(lut, data, lut_apply.Params(),
                                                  None, self.use_numpy)
                    pb = _pixbuf(lut_apply.to_rgba8(out), w, h)
                except CubeError:
                    pb = self._broken_thumb(w, h)
                self.thumbs[key] = pb
            row[1] = pb
            return True             # następna miniatura w kolejnym kroku
        self._thumb_job = None
        return False

    def _broken_thumb(self, w, h):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
        pb.fill(0xb04040ff)
        return pb

    # --- akcje biblioteki --------------------------------------------------------------

    def _after_import(self, result, title):
        self._fill_categories()
        select = result.added[0] if result.added else None
        self._refill(select=select)
        kind = Gtk.MessageType.WARNING if result.errors else Gtk.MessageType.INFO
        _message(self.dialog, title, result.summary(), kind)

    def _guard(self, fn, *args):
        """Wywołuje akcję biblioteki i pokazuje błąd po polsku."""
        try:
            return fn(*args)
        except (library.LibraryError, CubeError, OSError) as e:
            _message(self.dialog, "Błąd biblioteki", str(e), Gtk.MessageType.ERROR)
            return None

    def _on_add_files(self, *_):
        paths = _choose(self.dialog, "Dodaj pliki .cube do biblioteki",
                        Gtk.FileChooserAction.OPEN, self.settings, multiple=True)
        if paths:
            res = self._guard(self.lib.add_files, paths)
            if res:
                self._after_import(res, "Import plików .cube")

    def _on_add_folder(self, *_):
        paths = _choose(self.dialog, "Dodaj folder z plikami .cube",
                        Gtk.FileChooserAction.SELECT_FOLDER, self.settings)
        if paths:
            res = self._guard(self.lib.add_folder, paths[0], True)
            if res:
                self._after_import(res, "Import folderu")

    def _on_load_file(self, *_):
        paths = _choose(self.dialog, "Wczytaj plik .cube (bez dodawania do "
                        "biblioteki)", Gtk.FileChooserAction.OPEN, self.settings)
        if not paths:
            return
        try:
            cube_parser.parse_cube_file(paths[0])
        except CubeError as e:
            _message(self.dialog, "Nie można wczytać pliku .cube", str(e),
                     Gtk.MessageType.ERROR)
            return
        self.temp_path = paths[0]
        self.thumbs.pop(FILE_PREFIX + paths[0], None)
        self.current_key = None
        self._refill(select=FILE_PREFIX + paths[0])

    def _on_add_temp(self, *_):
        if not self.temp_path:
            return
        res = self._guard(self.lib.add_files, [self.temp_path])
        if res is None:
            return
        self.temp_path = None
        if res.added:
            self.current_key = None
            self._refill(select=res.added[0])
        else:
            self._refill()
            _message(self.dialog, "Dodawanie do biblioteki", res.summary())

    def _on_drop(self, widget, context, x, y, data, info, time_):
        paths = []
        for uri in data.get_uris() or []:
            try:
                path = GLib.filename_from_uri(uri)[0]
            except GLib.Error:
                path = urllib.parse.unquote(uri.replace("file:///", ""))
            if path:
                paths.append(path)
        result = library.ImportResult()
        for p in paths:
            if os.path.isdir(p):
                r = self._guard(self.lib.add_folder, p, True)
            elif p.lower().endswith(".cube"):
                r = self._guard(self.lib.add_files, [p])
            else:
                r = library.ImportResult()
                r.errors.append((p, "to nie jest plik .cube"))
            if r:
                result.merge(r)
        if paths:
            self._after_import(result, "Import przeciągniętych plików")

    def _selected_lib_id(self):
        key = self.current_key
        if key is None or key.startswith(FILE_PREFIX) or key not in self.lib.entries:
            return None
        return key

    def _on_rename(self):
        lut_id = self._selected_lib_id()
        if lut_id is None:
            return
        name = _ask_text(self.dialog, "Zmień nazwę",
                         "Nazwa wyświetlana (plik na dysku się nie zmienia).\n"
                         "Pusta nazwa = nazwa z pliku.",
                         self.lib.display_name(lut_id))
        if name is not None:
            self._guard(self.lib.rename, lut_id, name)
            self._refill()

    def _on_set_category(self):
        lut_id = self._selected_lib_id()
        if lut_id is None:
            return
        dlg = Gtk.Dialog(title="Kategoria", transient_for=self.dialog, modal=True)
        dlg.add_buttons("_Anuluj", Gtk.ResponseType.CANCEL, "_OK", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(6)
        box.add(Gtk.Label(label="Wybierz kategorię albo wpisz nową:", xalign=0))
        combo = Gtk.ComboBoxText.new_with_entry()
        combo.append_text("")
        for c in self.lib.categories:
            combo.append_text(c)
        combo.get_child().set_text(self.lib.entries[lut_id].get("category", ""))
        combo.get_child().set_activates_default(True)
        dlg.set_default_response(Gtk.ResponseType.OK)
        box.add(combo)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            self._guard(self.lib.set_category, lut_id,
                        combo.get_child().get_text().strip())
            self._fill_categories()
            self._refill()
        dlg.destroy()

    def _on_toggle_favorite(self):
        lut_id = self._selected_lib_id()
        if lut_id is not None:
            self._guard(self.lib.set_favorite, lut_id,
                        not self.lib.entries[lut_id].get("favorite"))
            self._refill()

    def _on_remove(self):
        lut_id = self._selected_lib_id()
        if lut_id is None:
            return
        dlg = Gtk.MessageDialog(transient_for=self.dialog, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text="Usunąć „%s” z biblioteki?"
                                % self.lib.display_name(lut_id))
        check = Gtk.CheckButton(label="Usuń też plik z folderu biblioteki "
                                "(oryginał, z którego importowano, zostaje)")
        dlg.get_message_area().add(check)
        dlg.add_button("_Anuluj", Gtk.ResponseType.CANCEL)
        dlg.add_button("_Usuń", Gtk.ResponseType.OK)
        dlg.show_all()
        resp, delete_file = dlg.run(), check.get_active()
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            self._guard(self.lib.remove, lut_id, delete_file)
            self.thumbs.pop(lut_id, None)
            self.current_key = None
            self.current_lut = None
            self._refill()

    def _on_manage_categories(self):
        dlg = Gtk.Dialog(title="Kategorie", transient_for=self.dialog, modal=True)
        dlg.add_button("_Zamknij", Gtk.ResponseType.CLOSE)
        dlg.set_default_size(320, 320)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(6)
        store = Gtk.ListStore(str)
        view = Gtk.TreeView(model=store, headers_visible=False)
        view.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0))
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)

        def reload():
            store.clear()
            for c in self.lib.categories:
                store.append([c])

        def selected():
            model, it = view.get_selection().get_selected()
            return model[it][0] if it else None

        def add(*_):
            name = _ask_text(dlg, "Nowa kategoria", "Nazwa kategorii:")
            if name:
                self._guard(self.lib.add_category, name)
                reload()

        def rename(*_):
            old = selected()
            if old:
                new = _ask_text(dlg, "Zmień nazwę kategorii", "Nowa nazwa:", old)
                if new:
                    self._guard(self.lib.rename_category, old, new)
                    reload()

        def remove(*_):
            name = selected()
            if name and _ask(dlg, "Usunąć kategorię „%s”?" % name,
                             "LUT-y z tej kategorii zostaną w bibliotece "
                             "(bez kategorii).",
                             [("_Anuluj", 0), ("_Usuń", 1)]) == 1:
                self._guard(self.lib.remove_category, name)
                reload()

        row = Gtk.Box(spacing=4)
        for label, cb in (("Dodaj…", add), ("Zmień nazwę…", rename),
                          ("Usuń", remove)):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            row.pack_start(b, True, True, 0)
        box.pack_start(row, False, False, 0)
        reload()
        dlg.show_all()
        dlg.run()
        dlg.destroy()
        self._fill_categories()
        self._refill()

    def _on_settings(self):
        s = self.settings
        dlg = Gtk.Dialog(title="Ustawienia biblioteki LUT", transient_for=self.dialog,
                         modal=True)
        dlg.add_buttons("_Anuluj", Gtk.ResponseType.CANCEL, "_OK", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        strip = Gtk.CheckButton(label="Skracaj wspólny prefiks w nazwach LUT-ów")
        strip.set_active(s["strip_prefix"])
        box.add(strip)
        box.add(Gtk.Label(label="Prefiks do pominięcia (pliki na dysku się nie "
                          "zmieniają):", xalign=0))
        prefix = Gtk.Entry(text=s["prefix"], width_chars=40)
        box.add(prefix)
        box.add(Gtk.Label(label="Folder importu startowego:", xalign=0))
        folder = Gtk.FileChooserButton(title="Folder z plikami .cube",
                                       action=Gtk.FileChooserAction.SELECT_FOLDER)
        start = library.suggested_startup_dir(s, self.plugin_dir)
        if start:
            folder.set_filename(start)
        box.add(folder)
        info = Gtk.Label(xalign=0, selectable=True)
        info.set_line_wrap(True)
        info.set_markup("<small>Folder biblioteki: %s\nObliczenia: %s</small>"
                        % (GLib.markup_escape_text(self.lib.root),
                           GLib.markup_escape_text(lut_apply.backend_name())))
        box.add(info)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            s["strip_prefix"] = strip.get_active()
            s["prefix"] = prefix.get_text()
            if folder.get_filename():
                s["startup_dir"] = folder.get_filename()
            self._guard(self.lib.save)
            self._refill()
        dlg.destroy()

    def _startup_import(self, force=False):
        """Przy pierwszym uruchomieniu proponuje import z folderu LUTS."""
        if not force and (not self.lib.is_empty() or self.settings["startup_offered"]):
            return False
        folder = library.suggested_startup_dir(self.settings, self.plugin_dir)
        if folder:
            count = len(library.find_cube_files(folder))
            text = ("Zaimportować %d plików .cube z folderu:\n%s" % (count, folder))
        else:
            text = "Nie znaleziono domyślnego folderu z LUT-ami."
        resp = _ask(self.dialog,
                    "Biblioteka LUT-ów jest pusta" if not force else "Import startowy",
                    text + "\n\nPliki zostaną skopiowane do biblioteki "
                    "(oryginały zostają bez zmian).",
                    ([("_Importuj", 1)] if folder else [])
                    + [("_Wybierz folder…", 2), ("_Nie teraz", 0)])
        self.settings["startup_offered"] = True
        if resp == 2:
            picked = _choose(self.dialog, "Folder z plikami .cube",
                             Gtk.FileChooserAction.SELECT_FOLDER, self.settings)
            folder = picked[0] if picked else None
        if resp in (1, 2) and folder:
            self.settings["startup_dir"] = folder
            res = self._guard(self.lib.add_folder, folder, True)
            if res:
                self._after_import(res, "Import startowy")
        else:
            self._guard(self.lib.save)
        return False

    # --- widok podglądu ----------------------------------------------------------------

    def _update_mode_widgets(self):
        mode = self.mode_combo.get_active_id()
        self.vertical_check.set_sensitive(mode in ("split", "side"))
        self.before_toggle.set_sensitive(mode == "toggle")
        if self.image is not None:
            self.opacity_check.set_sensitive(self.out_new.get_active())

    def _on_view_changed(self, *_):
        self.settings["preview_mode"] = self.mode_combo.get_active_id()
        self.settings["split_vertical"] = self.vertical_check.get_active()
        self._update_mode_widgets()
        self._crop_data = None
        if self.zoom_full:
            self._schedule_update(0)
        self.area.queue_draw()

    def _on_zoom_changed(self, *_):
        self.zoom_full = self.zoom_combo.get_active_id() == "100"
        self._crop_data = None
        self.crop = None
        self._schedule_update(0)

    def _on_area_resized(self, *_):
        if self.zoom_full:
            self._crop_data = None
            self._schedule_update()

    def _update_srgb_bar(self):
        if self.image is None:
            self.srgb_bar.hide()
            return
        ok, label = gimp_ops.profile_info(self.image)
        if ok:
            self.srgb_bar.hide()
        else:
            self.srgb_label.set_text(
                "Obraz ma profil kolorów „%s”. LUT-y .cube zakładają sRGB – "
                "bez konwersji kolory wyjdą inaczej niż w Lightroomie czy "
                "DaVinci." % label)
            self.srgb_bar.show_all()

    def _on_convert_srgb(self, bar, resp):
        try:
            gimp_ops.convert_to_srgb(self.image)
            Gimp.displays_flush()
        except gimp_ops.PluginError as e:
            _message(self.dialog, "Konwersja do sRGB", str(e), Gtk.MessageType.ERROR)
            return
        self._load_source()
        self._update_srgb_bar()
        self._schedule_update(0)

    def _current_images(self):
        """(przed, po, szer., wys.) do narysowania."""
        if self.zoom_full and self.crop is not None:
            _x, _y, w, h, before, after = self.crop
            return before, after, w, h, 1.0
        return self.before_pb, self.after_pb, self.fit_w, self.fit_h, None

    def _fit_rect(self, ax, ay, aw, ah, w, h, scale):
        if scale is None:
            scale = min(aw / float(w), ah / float(h))
        dw, dh = w * scale, h * scale
        x0 = ax + (aw - dw) / 2.0
        y0 = ay + (ah - dh) / 2.0
        if self._drag and self._drag.get("pan"):
            x0 += self._drag["dx"]
            y0 += self._drag["dy"]
        return x0, y0, dw, dh, scale

    def _paint(self, cr, pb, rect, scale, checker):
        x0, y0, dw, dh = rect
        cr.save()
        cr.rectangle(x0, y0, dw, dh)
        cr.clip()
        cr.set_source(checker)
        cr.paint()
        cr.translate(x0, y0)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD if scale < 1.0
                                   else cairo.FILTER_FAST)
        cr.paint()
        cr.restore()

    def _label_text(self, cr, x, y, text):
        cr.save()
        cr.set_font_size(13)
        ext = cr.text_extents(text)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(x, y, ext.width + 12, 20)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + 6, y + 15)
        cr.show_text(text)
        cr.restore()

    def _checker(self):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 16, 16)
        c = cairo.Context(surf)
        c.set_source_rgb(0.6, 0.6, 0.6)
        c.paint()
        c.set_source_rgb(0.4, 0.4, 0.4)
        c.rectangle(0, 0, 8, 8)
        c.rectangle(8, 8, 8, 8)
        c.fill()
        pat = cairo.SurfacePattern(surf)
        pat.set_extend(cairo.EXTEND_REPEAT)
        return pat

    def _on_draw(self, area, cr):
        aw, ah = area.get_allocated_width(), area.get_allocated_height()
        cr.set_source_rgb(0.18, 0.18, 0.18)
        cr.paint()
        checker = self._checker()
        before, after, w, h, scale = self._current_images()
        mode = self.mode_combo.get_active_id()
        vertical = self.vertical_check.get_active()
        show_before = self.hold_before or (mode == "toggle"
                                           and self.before_toggle.get_active())
        self._img_rects = []

        if mode == "side" and not show_before:
            gap = 6
            if vertical:
                boxes = [(0, 0, aw, (ah - gap) / 2.0),
                         (0, (ah + gap) / 2.0, aw, (ah - gap) / 2.0)]
            else:
                boxes = [(0, 0, (aw - gap) / 2.0, ah),
                         ((aw + gap) / 2.0, 0, (aw - gap) / 2.0, ah)]
            for (bx, by, bw, bh), pb, text in zip(boxes, (before, after),
                                                  ("Przed", "Po")):
                x0, y0, dw, dh, s = self._fit_rect(bx, by, bw, bh, w, h, scale)
                self._paint(cr, pb, (x0, y0, dw, dh), s, checker)
                self._label_text(cr, bx + 8, by + 8, text)
                self._img_rects.append((x0, y0, dw, dh))
            return False

        x0, y0, dw, dh, s = self._fit_rect(0, 0, aw, ah, w, h, scale)
        self._img_rects.append((x0, y0, dw, dh))
        if show_before or mode == "toggle":
            self._paint(cr, before if show_before else after, (x0, y0, dw, dh),
                        s, checker)
            self._label_text(cr, 8, 8, "Przed (oryginał)" if show_before else "Po")
            return False

        # Suwak: lewa/górna część – oryginał, prawa/dolna – efekt.
        self._paint(cr, before, (x0, y0, dw, dh), s, checker)
        cr.save()
        if vertical:
            cut = y0 + dh * self.split
            cr.rectangle(x0, cut, dw, y0 + dh - cut)
        else:
            cut = x0 + dw * self.split
            cr.rectangle(cut, y0, x0 + dw - cut, dh)
        cr.clip()
        self._paint(cr, after, (x0, y0, dw, dh), s, checker)
        cr.restore()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2)
        if vertical:
            cr.move_to(x0, cut)
            cr.line_to(x0 + dw, cut)
        else:
            cr.move_to(cut, y0)
            cr.line_to(cut, y0 + dh)
        cr.stroke()
        if vertical:
            cr.arc(x0 + dw / 2.0, cut, 7, 0, 6.2832)
        else:
            cr.arc(cut, y0 + dh / 2.0, 7, 0, 6.2832)
        cr.fill()
        self._label_text(cr, 8, 8, "Przed")
        self._label_text(cr, aw - 46 if not vertical else 8,
                         8 if not vertical else ah - 28, "Po")
        return False

    def _near_split(self, x, y):
        if self.mode_combo.get_active_id() != "split" or not self._img_rects:
            return False
        x0, y0, dw, dh = self._img_rects[0]
        if self.vertical_check.get_active():
            return abs(y - (y0 + dh * self.split)) <= SPLIT_GRAB
        return abs(x - (x0 + dw * self.split)) <= SPLIT_GRAB

    def _on_area_press(self, area, ev):
        area.grab_focus()
        if ev.button not in (1, 2):
            return False
        mode = self.mode_combo.get_active_id()
        if mode == "toggle" and ev.button == 1 and not self.zoom_full:
            self.before_toggle.set_active(not self.before_toggle.get_active())
            return True
        pan = self.zoom_full and (ev.button == 2 or not self._near_split(ev.x, ev.y))
        self._drag = {"pan": pan, "x": ev.x, "y": ev.y, "dx": 0.0, "dy": 0.0}
        if not pan and mode == "split":
            self._move_split(ev.x, ev.y)
        return True

    def _on_area_motion(self, area, ev):
        if self._drag is None:
            cursor = None
            if self._near_split(ev.x, ev.y) or (
                    self.mode_combo.get_active_id() == "split" and not self.zoom_full):
                cursor = Gdk.Cursor.new_from_name(
                    area.get_display(),
                    "row-resize" if self.vertical_check.get_active() else "col-resize")
            elif self.zoom_full:
                cursor = Gdk.Cursor.new_from_name(area.get_display(), "grab")
            area.get_window().set_cursor(cursor)
            return False
        if self._drag["pan"]:
            self._drag["dx"] = ev.x - self._drag["x"]
            self._drag["dy"] = ev.y - self._drag["y"]
            area.queue_draw()
        elif self.mode_combo.get_active_id() == "split":
            self._move_split(ev.x, ev.y)
        return True

    def _on_area_release(self, area, ev):
        drag, self._drag = self._drag, None
        if drag and drag["pan"] and (drag["dx"] or drag["dy"]):
            cx, cy = self.view_center
            cx = min(max(cx - drag["dx"], 0), self.source.width)
            cy = min(max(cy - drag["dy"], 0), self.source.height)
            self.view_center = (cx, cy)
            self._crop_data = None
            self._schedule_update(0)
        area.queue_draw()
        return True

    def _move_split(self, x, y):
        if not self._img_rects:
            return
        x0, y0, dw, dh = self._img_rects[0]
        if self.vertical_check.get_active():
            self.split = min(1.0, max(0.0, (y - y0) / max(1.0, dh)))
        else:
            self.split = min(1.0, max(0.0, (x - x0) / max(1.0, dw)))
        self.area.queue_draw()

    # --- klawiatura ----------------------------------------------------------------------

    def _on_key_press(self, widget, ev):
        focus = self.dialog.get_focus()
        in_text = isinstance(focus, Gtk.Entry)
        if ev.keyval in (Gdk.KEY_space, Gdk.KEY_backslash) and not in_text:
            if not self.hold_before:
                self.hold_before = True
                self.area.queue_draw()
            return True
        if ev.keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and not isinstance(
                focus, (Gtk.TreeView, Gtk.SpinButton, Gtk.Scale, Gtk.ComboBox)):
            self._move_selection(-1 if ev.keyval == Gdk.KEY_Up else 1)
            return True
        return False

    def _on_key_release(self, widget, ev):
        if ev.keyval in (Gdk.KEY_space, Gdk.KEY_backslash) and self.hold_before:
            self.hold_before = False
            self.area.queue_draw()
            return True
        return False

    def _on_tree_button(self, tree, ev):
        hit = tree.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return False
        path, column = hit[0], hit[1]
        if ev.button == 3:
            tree.set_cursor(path, None, False)
            self._build_menu(for_item=True).popup_at_pointer(ev)
            return True
        if ev.button == 1 and column is self.star_column:
            tree.set_cursor(path, None, False)
            self._on_toggle_favorite()
            return True
        return False

    # --- nakładanie ---------------------------------------------------------------------

    def _store_settings(self):
        s = self.settings
        s["strength"] = self.strength.get_value()
        s["color"] = self.color.get_value()
        s["luminance"] = self.luminance.get_value()
        s["interpolation"] = self.interp_combo.get_active_id()
        if self.image is not None:
            s["output"] = "in-place" if self.out_inplace.get_active() else "new-layer"
            s["strength_as_opacity"] = self.opacity_check.get_active()
        if self.current_key:
            s["last_lut"] = self.current_key
        try:
            self.lib.save()
        except library.LibraryError:
            pass

    def _progress(self, fraction):
        self.progress.set_fraction(fraction)
        self.progress.set_text("%d %%" % int(fraction * 100))
        while Gtk.events_pending():
            Gtk.main_iteration()
        return not self._cancel

    def _on_cancel_apply(self, *_):
        self._cancel = True

    def _set_busy(self, busy):
        self._busy = busy
        self._cancel = False
        self.paned.set_sensitive(not busy)
        if self.apply_button is not None:
            self.dialog.get_widget_for_response(RESP_APPLY).set_sensitive(not busy)
            self.dialog.get_widget_for_response(
                Gtk.ResponseType.CANCEL).set_sensitive(not busy)
        if busy:
            self.progress.set_fraction(0.0)
            self.progress_box.show_all()
        else:
            self.progress_box.hide()

    def apply(self):
        """Nakłada wybrany LUT na warstwy. True = gotowe (okno do zamknięcia)."""
        if self.current_lut is None:
            _message(self.dialog, "Nie wybrano LUT-a",
                     "Wybierz LUT z listy albo wczytaj plik .cube.")
            return False
        self._store_settings()
        params = self.params()
        output = self.settings["output"]
        as_opacity = self.settings["strength_as_opacity"]
        label = self._label(self.current_key)
        t = time.perf_counter()
        self._set_busy(True)
        self.image.undo_group_start()
        try:
            for k, layer in enumerate(self.layers):
                def progress(f, k=k):
                    return self._progress((k + f) / len(self.layers))
                gimp_ops.apply_to_layer(self.image, layer, self.current_lut,
                                        params, output, as_opacity, label, progress)
        except gimp_ops.Cancelled:
            self.status.set_text("Przerwano – obraz bez zmian.")
            return False
        except (gimp_ops.PluginError, CubeError) as e:
            _message(self.dialog, "Nie udało się nałożyć LUT-a", str(e),
                     Gtk.MessageType.ERROR)
            return False
        finally:
            self.image.undo_group_end()
            self._set_busy(False)
            Gimp.displays_flush()
        self.applied_seconds = time.perf_counter() - t
        if not self.current_key.startswith(FILE_PREFIX):
            self._guard(self.lib.mark_used, self.current_key)
        self._remember_in_config(params, output, as_opacity)
        return True

    def _remember_in_config(self, params, output, as_opacity):
        """Wartości dla „Filtry → Powtórz” (wywołanie bez okna)."""
        if self.config is None:
            return
        key = self.current_key
        path = (key[len(FILE_PREFIX):] if key.startswith(FILE_PREFIX)
                else self.lib.path(key))
        c = self.config
        c.set_property("lut-file", Gio.File.new_for_path(path))
        c.set_property("strength", params.strength * 100)
        c.set_property("color", params.color * 100)
        c.set_property("luminance", params.luminance * 100)
        c.set_property("interpolation", params.interpolation)
        c.set_property("output", output)
        c.set_property("strength-as-opacity", as_opacity)

    def run(self):
        result = None
        while True:
            resp = self.dialog.run()
            if resp == RESP_APPLY:
                if self.apply():
                    result = True
                    break
                continue
            break
        self._store_settings()
        self.close()
        return result

    def close(self):
        """Zamyka okno i anuluje zaplanowane przeliczenia podglądu/miniatur."""
        for job in (self._update_job, self._thumb_job):
            if job is not None:
                GLib.source_remove(job)
        self._update_job = self._thumb_job = None
        self.dialog.destroy()
        if self.offscreen:
            self.offscreen_window.destroy()


# --- wejścia z pliku głównego ------------------------------------------------------

def _prepare_image(image):
    """Obraz w skali szarości / indeksowany: propozycja konwersji do RGB."""
    if not gimp_ops.needs_rgb_conversion(image):
        return True
    resp = _ask(None, "Obraz nie jest w trybie RGB",
                "Obraz jest %s. LUT-y kolorystyczne działają na obrazach RGB.\n"
                "Skonwertować obraz do RGB? (można cofnąć: Ctrl+Z)"
                % gimp_ops.base_type_name(image),
                [("_Anuluj", 0), ("_Konwertuj do RGB", 1)])
    if resp != 1:
        return False
    image.convert_rgb()
    Gimp.displays_flush()
    return True


def run_apply_dialog(binary, plugin_dir, image, drawables, config):
    GimpUi.init(binary)
    layers = gimp_ops.target_layers(image, drawables)
    if not _prepare_image(image):
        return None
    win = LutWindow(plugin_dir, image, layers, config)
    return win.run()


def run_library_dialog(binary, plugin_dir):
    GimpUi.init(binary)
    LutWindow(plugin_dir).run()
