# -*- coding: utf-8 -*-
"""
Parser plików LUT w formacie .cube (specyfikacja Adobe Cube LUT 1.0,
z rozszerzeniami Resolve/IRIDAS: LUT_1D_INPUT_RANGE, LUT_3D_INPUT_RANGE).

Moduł nie zależy od GIMP-a ani od numpy – działa w zwykłym Pythonie 3.

Tabele trzymamy jako płaskie array('f') (float32):
  * 3D: N*N*N*3 wartości w kolejności z pliku – R zmienia się najszybciej,
    potem G, potem B. Wiersz dla węzła (r, g, b) ma indeks r + N*g + N*N*b.
  * 1D: N*3 wartości (R, G, B dla każdego węzła).
"""

import os
import struct
from array import array


class CubeError(Exception):
    """Błąd pliku .cube z komunikatem dla użytkownika (po polsku)."""


class CubeLUT:
    """Sparsowany LUT. Może mieć część 1D, część 3D albo obie."""

    def __init__(self, title="", size_1d=0, table_1d=None, size_3d=0,
                 table_3d=None, domain_min_1d=(0.0, 0.0, 0.0),
                 domain_max_1d=(1.0, 1.0, 1.0), domain_min_3d=(0.0, 0.0, 0.0),
                 domain_max_3d=(1.0, 1.0, 1.0)):
        self.title = title
        self.size_1d = size_1d
        self.table_1d = table_1d
        self.size_3d = size_3d
        self.table_3d = table_3d
        self.domain_min_1d = tuple(domain_min_1d)
        self.domain_max_1d = tuple(domain_max_1d)
        self.domain_min_3d = tuple(domain_min_3d)
        self.domain_max_3d = tuple(domain_max_3d)

    @property
    def kind(self):
        if self.size_1d and self.size_3d:
            return "1D+3D"
        return "3D" if self.size_3d else "1D"

    @property
    def grid_size(self):
        """Rozmiar siatki do wyświetlenia (3D ma pierwszeństwo)."""
        return self.size_3d or self.size_1d

    def __repr__(self):
        return "<CubeLUT %s %s %r>" % (self.kind, self.grid_size, self.title)


# Typowe rozmiary to 17, 25, 33, 64, 65; dopuszczamy rozsądny zakres.
MAX_3D_SIZE = 256
MAX_1D_SIZE = 65536


def _floats(parts, lineno, what):
    try:
        return [float(p) for p in parts]
    except ValueError:
        joined = " ".join(parts)
        if "," in joined:
            raise CubeError(
                "Wiersz %d: liczby zapisane z przecinkiem dziesiętnym (%s). "
                "Format .cube wymaga kropki, np. 0.5 zamiast 0,5."
                % (lineno, joined))
        raise CubeError("Wiersz %d: %s – to nie są poprawne liczby: %s"
                        % (lineno, what, joined))


def _int(parts, lineno, keyword):
    if len(parts) != 1:
        raise CubeError("Wiersz %d: %s wymaga jednej liczby całkowitej."
                        % (lineno, keyword))
    try:
        return int(parts[0])
    except ValueError:
        raise CubeError("Wiersz %d: %s – niepoprawna liczba: %s"
                        % (lineno, keyword, parts[0]))


def _triple(parts, lineno, keyword):
    if len(parts) != 3:
        raise CubeError("Wiersz %d: %s wymaga trzech liczb." % (lineno, keyword))
    return tuple(_floats(parts, lineno, keyword))


def _range_pair(parts, lineno, keyword):
    if len(parts) != 2:
        raise CubeError("Wiersz %d: %s wymaga dwóch liczb." % (lineno, keyword))
    lo, hi = _floats(parts, lineno, keyword)
    return (lo, lo, lo), (hi, hi, hi)


def parse_cube_text(text, source=""):
    """Parsuje treść pliku .cube (str). Zwraca CubeLUT albo rzuca CubeError."""
    title = ""
    size_1d = size_3d = 0
    dmin = dmax = None            # DOMAIN_MIN/MAX – dotyczą obu części
    dmin_1d = dmax_1d = None      # LUT_1D_INPUT_RANGE
    dmin_3d = dmax_3d = None      # LUT_3D_INPUT_RANGE
    data = array("f")
    rows = 0

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Komentarz na końcu wiersza (spotykany w praktyce).
        if "#" in line and not line.startswith("TITLE"):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        first = line.split(None, 1)[0]
        if first[0].isalpha():
            keyword = first.upper()
            parts = line.split()[1:]
            if rows:
                raise CubeError("Wiersz %d: słowo kluczowe %s po danych "
                                "tabeli – plik jest uszkodzony."
                                % (lineno, keyword))
            if keyword == "TITLE":
                rest = line[len(first):].strip()
                title = rest[1:-1] if len(rest) >= 2 and rest[0] == rest[-1] == '"' \
                    else rest.strip('"')
            elif keyword == "LUT_3D_SIZE":
                size_3d = _int(parts, lineno, keyword)
                if not 2 <= size_3d <= MAX_3D_SIZE:
                    raise CubeError("Wiersz %d: LUT_3D_SIZE = %d poza zakresem "
                                    "2–%d." % (lineno, size_3d, MAX_3D_SIZE))
            elif keyword == "LUT_1D_SIZE":
                size_1d = _int(parts, lineno, keyword)
                if not 2 <= size_1d <= MAX_1D_SIZE:
                    raise CubeError("Wiersz %d: LUT_1D_SIZE = %d poza zakresem "
                                    "2–%d." % (lineno, size_1d, MAX_1D_SIZE))
            elif keyword == "DOMAIN_MIN":
                dmin = _triple(parts, lineno, keyword)
            elif keyword == "DOMAIN_MAX":
                dmax = _triple(parts, lineno, keyword)
            elif keyword == "LUT_1D_INPUT_RANGE":
                dmin_1d, dmax_1d = _range_pair(parts, lineno, keyword)
            elif keyword == "LUT_3D_INPUT_RANGE":
                dmin_3d, dmax_3d = _range_pair(parts, lineno, keyword)
            # Inne słowa kluczowe (np. LUT_IN_VIDEO_RANGE) ignorujemy.
            continue

        parts = line.split()
        if len(parts) != 3:
            if len(parts) > 3 and "," in line:
                _floats(parts, lineno, "dane")   # rzuci komunikat o przecinku
            raise CubeError("Wiersz %d: wiersz danych musi mieć 3 liczby "
                            "(R G B), a ma %d." % (lineno, len(parts)))
        data.extend(_floats(parts, lineno, "dane"))
        rows += 1

    name = os.path.basename(source) if source else "plik"
    if not size_1d and not size_3d:
        raise CubeError("%s: brak LUT_3D_SIZE ani LUT_1D_SIZE – to nie wygląda "
                        "na plik .cube." % name)
    expected = size_1d + size_3d ** 3
    if rows != expected:
        parts = []
        if size_1d:
            parts.append("%d (1D)" % size_1d)
        if size_3d:
            parts.append("%d³ = %d (3D)" % (size_3d, size_3d ** 3))
        raise CubeError("%s: zła liczba wierszy danych – jest %d, a powinno "
                        "być %s." % (name, rows, " + ".join(parts)))

    dmin = dmin or (0.0, 0.0, 0.0)
    dmax = dmax or (1.0, 1.0, 1.0)
    lo1, hi1 = dmin_1d or dmin, dmax_1d or dmax
    lo3, hi3 = dmin_3d or dmin, dmax_3d or dmax
    for lo, hi in ((lo1, hi1), (lo3, hi3)):
        if any(h <= l for l, h in zip(lo, hi)):
            raise CubeError("%s: DOMAIN_MAX musi być większe od DOMAIN_MIN."
                            % name)

    # Wg specyfikacji (Resolve) część 1D jest w pliku przed częścią 3D.
    n1 = size_1d * 3
    table_1d = data[:n1] if size_1d else None
    table_3d = data[n1:] if size_3d else None
    return CubeLUT(title, size_1d, table_1d, size_3d, table_3d,
                   lo1, hi1, lo3, hi3)


def read_text(path):
    """Czyta plik tekstowy: UTF-8 (z BOM lub bez), awaryjnie cp1250/latin-1."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_cube_file(path):
    """Wczytuje i parsuje plik .cube. Rzuca CubeError (także przy błędach I/O)."""
    try:
        text = read_text(path)
    except OSError as e:
        raise CubeError("Nie można odczytać pliku %s: %s"
                        % (os.path.basename(path), e.strerror or e))
    return parse_cube_text(text, path)


# ---------------------------------------------------------------------------
# Szybki cache binarny (żeby nie parsować ~400 KB tekstu za każdym razem)
# ---------------------------------------------------------------------------

_MAGIC = b"LUTC1\0"
_HEADER = struct.Struct("<6sII12f")   # magic, size_1d, size_3d, 4 × (r,g,b)


def save_binary(lut, path):
    """Zapisuje LUT do pliku binarnego (float32, little-endian)."""
    title = lut.title.encode("utf-8")
    header = _HEADER.pack(_MAGIC, lut.size_1d, lut.size_3d,
                          *(lut.domain_min_1d + lut.domain_max_1d
                            + lut.domain_min_3d + lut.domain_max_3d))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(title)))
        f.write(title)
        for table in (lut.table_1d, lut.table_3d):
            if table is not None:
                arr = array("f", table)
                if arr.itemsize != 4:
                    raise CubeError("Nieobsługiwana platforma (float != 4 B).")
                if struct.pack("=I", 1) != struct.pack("<I", 1):
                    arr.byteswap()
                arr.tofile(f)
    os.replace(tmp, path)


def load_binary(path):
    """Odczytuje LUT zapisany przez save_binary. Rzuca CubeError, gdy uszkodzony."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        magic, size_1d, size_3d, *dom = _HEADER.unpack_from(raw, 0)
        if magic != _MAGIC:
            raise ValueError("zły nagłówek")
        pos = _HEADER.size
        (tlen,) = struct.unpack_from("<I", raw, pos)
        pos += 4
        title = raw[pos:pos + tlen].decode("utf-8")
        pos += tlen
        n1, n3 = size_1d * 3, size_3d ** 3 * 3
        if len(raw) != pos + 4 * (n1 + n3):
            raise ValueError("zła długość")
        tables = []
        for n in (n1, n3):
            arr = array("f")
            arr.frombytes(raw[pos:pos + 4 * n])
            if struct.pack("=I", 1) != struct.pack("<I", 1):
                arr.byteswap()
            tables.append(arr if n else None)
            pos += 4 * n
    except (OSError, ValueError, struct.error, UnicodeDecodeError) as e:
        raise CubeError("Uszkodzony cache LUT-a (%s): %s"
                        % (os.path.basename(path), e))
    return CubeLUT(title, size_1d, tables[0], size_3d, tables[1],
                   dom[0:3], dom[3:6], dom[6:9], dom[9:12])
