# -*- coding: utf-8 -*-
"""
Nakładanie LUT-ów 1D/3D na piksele RGBA float32.

Moduł nie zależy od GIMP-a. Ma dwa warianty obliczeń o tym samym wyniku:
  * numpy – szybki (zdjęcie 24 Mpx w kilka sekund),
  * czysty Python – wolniejszy, używany, gdy numpy nie ma.

Wartości wejściowe to kodowanie perceptualne (gamma sRGB, format babl
„R'G'B'A float”). Wynik dla siły s, maski m i wag kolor/jasność c/l:

    D  = LUT(O) − O                  (zmiana, jaką daje LUT)
    dY = 0.2126·Dr + 0.7152·Dg + 0.0722·Db   (część „jasności” zmiany)
    E  = c·(D − dY) + l·dY           (przy c = l = 1: E = D)
    V  = O + s·(clip(O + E, 0, 1) − O)

Dla s = 0 wynik to dokładnie oryginał, dla s = 1, c = l = 1 – czysty LUT.
Maska zaznaczenia m (miękkie krawędzie) miesza O i V w świetle liniowym,
tak samo jak robi to GIMP przy scalaniu wyniku z zaznaczeniem – dzięki temu
podgląd zgadza się z tym, co trafi do obrazu. Kanał alfa nie jest zmieniany.
"""

import os
import sys
from array import array

TETRAHEDRAL = "tetrahedral"
TRILINEAR = "trilinear"

# Współczynniki luminancji Rec.709 / sRGB.
LUMA = (0.2126, 0.7152, 0.0722)


class Params:
    """Ustawienia nakładania LUT-a. Wartości 0–1 (siła, kolor, jasność)."""

    def __init__(self, strength=1.0, color=1.0, luminance=1.0,
                 interpolation=TETRAHEDRAL):
        self.strength = float(strength)
        self.color = float(color)
        self.luminance = float(luminance)
        if interpolation not in (TETRAHEDRAL, TRILINEAR):
            raise ValueError("Nieznana interpolacja: %s" % interpolation)
        self.interpolation = interpolation

    @property
    def full_effect(self):
        return self.color == 1.0 and self.luminance == 1.0

    def key(self):
        return (self.strength, self.color, self.luminance, self.interpolation)


# ---------------------------------------------------------------------------
# numpy (opcjonalnie, także z dołączonego folderu vendor/)
# ---------------------------------------------------------------------------

_np = None
_np_checked = False
NUMPY_SOURCE = ""      # "system", "vendor" albo "" (brak)
NUMPY_ERROR = ""


def _vendor_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _vendor_matches(vendor):
    """Czy dołączony numpy pasuje do bieżącego Pythona (wersja + ABI)."""
    import json
    import sysconfig
    try:
        with open(os.path.join(vendor, "VENDOR.json"), encoding="utf-8-sig") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return False
    return (info.get("python") == "%d.%d" % sys.version_info[:2]
            and info.get("ext_suffix") == sysconfig.get_config_var("EXT_SUFFIX"))


def get_numpy(allow=True):
    """
    Zwraca moduł numpy albo None. Najpierw próbuje zwykłego importu, potem
    kopii z folderu vendor/ obok wtyczki (tylko gdy pasuje do Pythona).
    """
    global _np, _np_checked, NUMPY_SOURCE, NUMPY_ERROR
    # LUT_CUBE_NO_NUMPY=1 wymusza wariant „czysty Python” (testy, awarie).
    if not allow or os.environ.get("LUT_CUBE_NO_NUMPY"):
        return None
    if _np_checked:
        return _np
    _np_checked = True
    # Obliczenia nie korzystają z BLAS-a – jeden wątek wystarczy.
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    try:
        import numpy
        _np, NUMPY_SOURCE = numpy, "system"
        return _np
    except ImportError as e:
        NUMPY_ERROR = str(e)
    vendor = _vendor_dir()
    if os.path.isdir(vendor) and _vendor_matches(vendor):
        added = None
        try:
            if hasattr(os, "add_dll_directory"):
                added = os.add_dll_directory(os.path.join(vendor, "bin"))
            sys.path.insert(0, os.path.join(vendor, "site-packages"))
            import numpy
            _np, NUMPY_SOURCE = numpy, "vendor"
            return _np
        except Exception as e:   # noqa – każdy błąd = wariant bez numpy
            NUMPY_ERROR = "vendor: %s" % e
            for name in [m for m in sys.modules if m == "numpy"
                         or m.startswith("numpy.")]:
                del sys.modules[name]
            if added is not None:
                added.close()
    return None


# ---------------------------------------------------------------------------
# Czysty Python – pojedynczy piksel
# ---------------------------------------------------------------------------

def _norm(v, lo, hi):
    x = (v - lo) / (hi - lo)
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _lerp1d(t, n, x, c):
    """Interpolacja liniowa w tabeli 1D (kanał c) dla x w 0–1."""
    p = x * (n - 1)
    i = int(p)
    if i >= n - 1:
        i = n - 2
    f = p - i
    a = t[3 * i + c]
    return a + (t[3 * i + 3 + c] - a) * f


def _tetra(t, n, r, g, b):
    """Interpolacja tetraedralna w siatce 3D; r, g, b w zakresie 0–1."""
    m = n - 1
    pr, pg, pb = r * m, g * m, b * m
    ir, ig, ib = int(pr), int(pg), int(pb)
    if ir >= m:
        ir = m - 1
    if ig >= m:
        ig = m - 1
    if ib >= m:
        ib = m - 1
    fr, fg, fb = pr - ir, pg - ig, pb - ib
    sr, sg, sb = 3, 3 * n, 3 * n * n           # kroki w płaskiej tabeli
    o = 3 * (ir + n * ig + n * n * ib)
    # Wybór czworościanu wg kolejności ułamków.
    if fr >= fg:
        if fg >= fb:        # r ≥ g ≥ b
            a, f1, b2, f2, f3 = sr, fr, sr + sg, fg, fb
        elif fr >= fb:      # r ≥ b > g
            a, f1, b2, f2, f3 = sr, fr, sr + sb, fb, fg
        else:               # b > r ≥ g
            a, f1, b2, f2, f3 = sb, fb, sb + sr, fr, fg
    else:
        if fb > fg:         # b > g > r
            a, f1, b2, f2, f3 = sb, fb, sb + sg, fg, fr
        elif fb > fr:       # g ≥ b > r
            a, f1, b2, f2, f3 = sg, fg, sg + sb, fb, fr
        else:               # g > r ≥ b
            a, f1, b2, f2, f3 = sg, fg, sg + sr, fr, fb
    w0, w1, w2 = 1.0 - f1, f1 - f2, f2 - f3
    c0, c1, c2, c3 = o, o + a, o + b2, o + sr + sg + sb
    return (w0 * t[c0] + w1 * t[c1] + w2 * t[c2] + f3 * t[c3],
            w0 * t[c0 + 1] + w1 * t[c1 + 1] + w2 * t[c2 + 1] + f3 * t[c3 + 1],
            w0 * t[c0 + 2] + w1 * t[c1 + 2] + w2 * t[c2 + 2] + f3 * t[c3 + 2])


def _trilinear(t, n, r, g, b):
    """Interpolacja trójliniowa w siatce 3D; r, g, b w zakresie 0–1."""
    m = n - 1
    pr, pg, pb = r * m, g * m, b * m
    ir, ig, ib = min(int(pr), m - 1), min(int(pg), m - 1), min(int(pb), m - 1)
    fr, fg, fb = pr - ir, pg - ig, pb - ib
    out = [0.0, 0.0, 0.0]
    for db, wb in ((0, 1.0 - fb), (1, fb)):
        for dg, wg in ((0, 1.0 - fg), (1, fg)):
            for dr, wr in ((0, 1.0 - fr), (1, fr)):
                w = wr * wg * wb
                if w:
                    o = 3 * ((ir + dr) + n * (ig + dg) + n * n * (ib + db))
                    out[0] += w * t[o]
                    out[1] += w * t[o + 1]
                    out[2] += w * t[o + 2]
    return tuple(out)


def lookup(lut, r, g, b, interpolation=TETRAHEDRAL):
    """Czysty LUT dla jednego piksela (1D, potem 3D); wynik przycięty do 0–1."""
    if lut.size_1d:
        lo, hi = lut.domain_min_1d, lut.domain_max_1d
        t, n = lut.table_1d, lut.size_1d
        r = _lerp1d(t, n, _norm(r, lo[0], hi[0]), 0)
        g = _lerp1d(t, n, _norm(g, lo[1], hi[1]), 1)
        b = _lerp1d(t, n, _norm(b, lo[2], hi[2]), 2)
    if lut.size_3d:
        lo, hi = lut.domain_min_3d, lut.domain_max_3d
        fn = _tetra if interpolation == TETRAHEDRAL else _trilinear
        r, g, b = fn(lut.table_3d, lut.size_3d, _norm(r, lo[0], hi[0]),
                     _norm(g, lo[1], hi[1]), _norm(b, lo[2], hi[2]))
    clamp = lambda v: 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)  # noqa: E731
    return clamp(r), clamp(g), clamp(b)


def srgb_decode(v):
    """Wartość perceptualna sRGB → liniowa (także poza 0–1, symetrycznie)."""
    a = -v if v < 0.0 else v
    a = a / 12.92 if a <= 0.04045 else ((a + 0.055) / 1.055) ** 2.4
    return -a if v < 0.0 else a


def srgb_encode(v):
    """Wartość liniowa → perceptualna sRGB."""
    a = -v if v < 0.0 else v
    a = a * 12.92 if a <= 0.0031308 else 1.055 * a ** (1 / 2.4) - 0.055
    return -a if v < 0.0 else a


def _effect_fn(lut, params):
    """
    Zwraca f(r, g, b) -> wynik dla siły 100 % (z mieszaniem kolor/jasność).
    """
    interp = params.interpolation
    c, l = params.color, params.luminance
    kr, kg, kb = LUMA

    def f(r, g, b):
        cr = 0.0 if r < 0.0 else (1.0 if r > 1.0 else r)
        cg = 0.0 if g < 0.0 else (1.0 if g > 1.0 else g)
        cb = 0.0 if b < 0.0 else (1.0 if b > 1.0 else b)
        lr, lg, lb = lookup(lut, cr, cg, cb, interp)
        if c == 1.0 and l == 1.0:
            return r + lr - cr, g + lg - cg, b + lb - cb
        dr, dg, db = lr - cr, lg - cg, lb - cb
        dy = kr * dr + kg * dg + kb * db
        er, eg, eb = (c * (dr - dy) + l * dy, c * (dg - dy) + l * dy,
                      c * (db - dy) + l * dy)
        clamp = lambda v: 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)  # noqa
        return (r + clamp(cr + er) - cr, g + clamp(cg + eg) - cg,
                b + clamp(cb + eb) - cb)
    return f


class _Cache(dict):
    """Słownik kolor → wynik; brakujące wartości liczy przy pierwszym użyciu."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def __missing__(self, key):
        v = self.fn(*key)
        self[key] = v
        return v


_CACHE_LIMIT = 3000000


def _apply_pure(lut, buf, params, mask, cache=None):
    """Nakłada LUT na array('f') RGBA w miejscu. Zwraca użyty cache."""
    if cache is None or len(cache) > _CACHE_LIMIT:
        cache = _Cache(_effect_fn(lut, params))
    rs, gs, bs = buf[0::4], buf[1::4], buf[2::4]
    res = list(map(cache.__getitem__, zip(rs, gs, bs)))
    s = params.strength
    for ch, orig in enumerate((rs, gs, bs)):
        new = [v[ch] for v in res]
        if s != 1.0:
            new = [o + s * (v - o) for o, v in zip(orig, new)]
        if mask is not None:
            dec, enc = srgb_decode, srgb_encode
            new = [o if m <= 0.0 else (v if m >= 1.0 else
                   enc(dec(o) + m * (dec(v) - dec(o))))
                   for o, v, m in zip(orig, new, mask)]
        buf[ch::4] = array("f", new)
    return cache


# ---------------------------------------------------------------------------
# numpy – całe tablice
# ---------------------------------------------------------------------------

def _np_decode(np, v):
    a = np.abs(v)
    out = np.where(a <= 0.04045, a / np.float32(12.92),
                   ((a + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4))
    return np.copysign(out, v).astype(np.float32)


def _np_encode(np, v):
    a = np.abs(v)
    out = np.where(a <= 0.0031308, a * np.float32(12.92),
                   np.float32(1.055) * a ** np.float32(1 / 2.4) - np.float32(0.055))
    return np.copysign(out, v).astype(np.float32)


def _np_tables(np, lut):
    """Tabele LUT-a jako tablice numpy (cache na obiekcie LUT-a)."""
    cached = getattr(lut, "_np_cache", None)
    if cached is None:
        t1 = (np.frombuffer(lut.table_1d, dtype=np.float32).reshape(-1, 3)
              if lut.size_1d else None)
        t3 = (np.frombuffer(lut.table_3d, dtype=np.float32).reshape(-1, 3)
              if lut.size_3d else None)
        cached = (t1, t3)
        lut._np_cache = cached
    return cached


def _np_norm(np, x, lo, hi):
    """Przeskalowanie do zakresu DOMAIN_MIN–MAX (x jest już w 0–1)."""
    if lo == (0.0, 0.0, 0.0) and hi == (1.0, 1.0, 1.0):
        return x
    lo = np.asarray(lo, dtype=np.float32)
    hi = np.asarray(hi, dtype=np.float32)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def _np_1d(np, t, n, x):
    p = x * np.float32(n - 1)
    i = np.minimum(p.astype(np.int32), n - 2)
    f = p - i
    out = np.empty_like(x)
    for c in range(3):
        col = t[:, c]
        a = np.take(col, i[:, c])
        out[:, c] = a + (np.take(col, i[:, c] + 1) - a) * f[:, c]
    return out


def _np_cell(np, n, x):
    """Indeks węzła (r0, g0, b0) w płaskiej tabeli i ułamki położenia."""
    p = x * np.float32(n - 1)
    i = np.minimum(p.astype(np.int32), n - 2)
    f = p - i
    return i[:, 0] + n * i[:, 1] + (n * n) * i[:, 2], f


def _np_tetra(np, t, n, x):
    base, f = _np_cell(np, n, x)
    fr, fg, fb = f[:, 0], f[:, 1], f[:, 2]
    nn = n * n
    far = 1 + n + nn                      # przesunięcie do węzła (1, 1, 1)
    r_ge_g, g_ge_b, r_ge_b = fr >= fg, fg >= fb, fr >= fb
    # Oś o największym ułamku (pierwszy krok) i o najmniejszym (bez niej
    # dochodzimy do drugiego wierzchołka). Remisy rozstrzygane spójnie.
    off_max = np.where(r_ge_g & r_ge_b, 1, np.where(g_ge_b, n, nn))
    off_min = np.where(g_ge_b & r_ge_b, nn, np.where(r_ge_g, n, 1))
    f1 = np.maximum(np.maximum(fr, fg), fb)
    f3 = np.minimum(np.minimum(fr, fg), fb)
    f2 = fr + fg + fb - f1 - f3
    c0 = np.take(t, base, axis=0)
    c1 = np.take(t, base + off_max, axis=0)
    c2 = np.take(t, base + far - off_min, axis=0)
    c3 = np.take(t, base + far, axis=0)
    # (1−f1)·c0 + (f1−f2)·c1 + (f2−f3)·c2 + f3·c3, w postaci różnicowej:
    c3 -= c2
    c3 *= f3[:, None]
    c2 -= c1
    c2 *= f2[:, None]
    c1 -= c0
    c1 *= f1[:, None]
    c0 += c1
    c0 += c2
    c0 += c3
    return c0


def _np_trilinear(np, t, n, x):
    base, f = _np_cell(np, n, x)
    nn = n * n

    def lerp(a, b, w):
        b -= a
        b *= w[:, None]
        a += b
        return a

    fr, fg, fb = f[:, 0], f[:, 1], f[:, 2]
    planes = []
    for db in (0, nn):
        rows = []
        for dg in (0, n):
            o = base + (db + dg)
            rows.append(lerp(np.take(t, o, axis=0), np.take(t, o + 1, axis=0), fr))
        planes.append(lerp(rows[0], rows[1], fg))
    return lerp(planes[0], planes[1], fb)


def _np_effect(np, lut, rgb, params):
    """rgb (M,3) float32 → zmiana d dla siły 100 % (wynik = rgb + d)."""
    t1, t3 = _np_tables(np, lut)
    c = np.clip(rgb, 0.0, 1.0)
    x = c
    if lut.size_1d:
        x = _np_1d(np, t1, lut.size_1d,
                   _np_norm(np, x, lut.domain_min_1d, lut.domain_max_1d))
    if lut.size_3d:
        fn = _np_tetra if params.interpolation == TETRAHEDRAL else _np_trilinear
        x = fn(np, t3, lut.size_3d,
               _np_norm(np, x, lut.domain_min_3d, lut.domain_max_3d))
    np.clip(x, 0.0, 1.0, out=x)
    x -= c
    if not params.full_effect:
        dy = (x @ np.asarray(LUMA, dtype=np.float32))[:, None]
        x = np.clip(c + np.float32(params.color) * (x - dy)
                    + np.float32(params.luminance) * dy, 0.0, 1.0) - c
    return x


CHUNK = 1 << 17       # pikseli na kawałek (wątek) – mało pamięci, dobra pamięć podręczna
_pool = None


def _thread_pool():
    """Pula wątków: numpy zwalnia GIL, więc kawałki liczą się równolegle."""
    global _pool
    if _pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _pool = ThreadPoolExecutor(max(1, min(8, (os.cpu_count() or 2))))
    return _pool


def _apply_numpy(np, lut, buf, params, mask):
    px = np.frombuffer(buf, dtype=np.float32).reshape(-1, 4).copy()
    m = None if mask is None else np.frombuffer(mask, dtype=np.float32)
    s = np.float32(params.strength)

    def work(start):
        rgb = px[start:start + CHUNK, :3]
        d = _np_effect(np, lut, rgb, params)
        if params.strength != 1.0:
            d *= s
        if m is None:
            rgb += d
            return
        w = m[start:start + CHUNK, None]
        lin = _np_decode(np, rgb)
        mixed = lin + w * (_np_decode(np, rgb + d) - lin)
        rgb[...] = np.where(w >= 1.0, rgb + d,
                            np.where(w <= 0.0, rgb, _np_encode(np, mixed)))

    starts = range(0, len(px), CHUNK)
    if len(starts) > 1:
        list(_thread_pool().map(work, starts))
    elif starts:
        work(0)
    return px.tobytes()


# ---------------------------------------------------------------------------
# Interfejs publiczny
# ---------------------------------------------------------------------------

def apply_rgba(lut, data, params, mask=None, use_numpy=True, cache=None):
    """
    Nakłada LUT na piksele RGBA float32 (bytes / bytearray / memoryview).
    mask – opcjonalne wagi 0–1 (float32, po jednej na piksel).
    Zwraca (bytes_wynikowe, cache) – cache przyspiesza wariant bez numpy
    przy kolejnych kawałkach tego samego obrazu (podaj go z powrotem).
    """
    if len(data) % 16:
        raise ValueError("Dane muszą być RGBA float32 (wielokrotność 16 B).")
    if mask is not None and len(mask) * 4 != len(data):
        raise ValueError("Maska ma inną liczbę pikseli niż obraz.")
    if params.strength == 0.0:
        return bytes(data), cache
    np = get_numpy(use_numpy)
    if np is not None:
        return _apply_numpy(np, lut, data, params, mask), cache
    buf = array("f")
    buf.frombytes(bytes(data))
    m = None
    if mask is not None:
        m = array("f")
        m.frombytes(bytes(mask))
    cache = _apply_pure(lut, buf, params, m, cache)
    return buf.tobytes(), cache


def to_rgba8(data, use_numpy=True):
    """RGBA float32 → RGBA 8 bit (do wyświetlenia podglądu)."""
    np = get_numpy(use_numpy)
    if np is not None:
        px = np.frombuffer(data, dtype=np.float32)
        return (np.clip(px, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes()
    buf = array("f")
    buf.frombytes(bytes(data))
    return bytes([0 if v <= 0.0 else (255 if v >= 1.0 else int(v * 255.0 + 0.5))
                  for v in buf])


def backend_name(use_numpy=True):
    """Opis wariantu obliczeń do wyświetlenia w oknie."""
    if get_numpy(use_numpy) is not None:
        return "numpy %s (%s)" % (_np.__version__, NUMPY_SOURCE)
    return "czysty Python (wolniejszy)"
