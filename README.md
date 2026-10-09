# Podpis na zdjęciu – wtyczka GIMP 3

> W tym repozytorium jest też druga wtyczka: **Kreator memów** (`meme-maker/`, GPL-3.0, Filtry → Memy). Opis: [meme-maker/README.md](meme-maker/README.md).
> `install.ps1` bez parametrów instaluje wszystkie wtyczki leżące obok skryptu; jedną wybierzesz przez `-Plugin podpis-zdjecia` albo `-Plugin meme-maker`.

Dodaje podpis na zdjęciu jako **nową warstwę na samej górze stosu**. Wtyczka ma dwa tryby:

- **Filtry → Podpis → Podpis tekstowy…** (`python-fu-podpis-tekst`) tworzy edytowalną warstwę tekstową.
- **Filtry → Podpis → Podpis z obrazu…** (`python-fu-podpis-obraz`) wstawia logo lub sygnaturę z pliku PNG, JPG, WebP, SVG albo innego formatu, który GIMP potrafi wczytać.

Całość jest jedną operacją cofania, więc jedno Ctrl+Z usuwa podpis. Istniejące warstwy zostają nietknięte.

Przetestowano na GIMP 3.2.6 (Windows 11). Kod działa na każdym systemie i nie potrzebuje żadnych zewnętrznych bibliotek.

## Instalacja (Windows)

Najprościej: pobierz `podpis-zdjecia-vX.Y.Z.zip` z zakładki
[Releases](https://github.com/zetmar-collab/gimp-podpis-zdjecia/releases/latest),
rozpakuj i uruchom `install.ps1` (prawy przycisk → „Uruchom za pomocą programu PowerShell”)
albo skopiuj folder `podpis-zdjecia` ręcznie (patrz niżej).

Automatycznie, skryptem, który sam wykrywa najnowszy katalog `3.x`:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
# albo wybrana wersja:
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Version 3.0
```

Ręcznie: skopiuj cały folder `podpis-zdjecia` do

```
%APPDATA%\GIMP\3.2\plug-ins\podpis-zdjecia\podpis-zdjecia.py
```

(w GIMP 3.0 zamiast `3.2` jest `3.0`). Folder musi mieć tę samą nazwę co plik `.py`. Na koniec uruchom GIMP ponownie.

Linux/macOS: ścieżka `~/.config/GIMP/3.x/plug-ins/podpis-zdjecia/`, a plik musi mieć prawo wykonania (`chmod +x`).

## Parametry

### Wspólne

| id | Opis | Domyślnie |
|---|---|---|
| `opacity` | Krycie 0–100 % | 70 |
| `position` | 9 pozycji: `top-left` … `bottom-right` | `bottom-right` |
| `margin-percent` | Margines jako % krótszego boku obrazu | 3.0 |
| `margin` | Margines w px. **0 oznacza, że liczony jest procent** | 0 |
| `layer-name` | Nazwa nowej warstwy | Podpis |

Margines nie działa w osi, w której pozycja to „środek”.

### Podpis tekstowy

| id | Opis | Domyślnie |
|---|---|---|
| `text` | Treść podpisu (w dialogu pole wielolinijkowe) | © Marek Zettel |
| `font` | Font | bieżący font z kontekstu GIMP-a (zwykle Sans-serif) |
| `size-mode` | `percent` (% szerokości obrazu) albo `pixels` | `percent` |
| `font-size-percent` | 0.5–50 % szerokości obrazu | 4.0 |
| `font-size-px` | 4–2000 px | 48 |
| `color` | Kolor tekstu | biały |
| `justify` | `auto` / `left` / `center` / `right` | `auto` |

Tryb procentowy sprawia, że podpis wygląda tak samo względem kadru na zdjęciu 1200 px i na 6000 px. `justify = auto` bierze wyrównanie z kolumny pozycji: lewa kolumna daje wyrównanie do lewej, środkowa do środka, prawa do prawej.

### Podpis z obrazu

| id | Opis | Domyślnie |
|---|---|---|
| `file` | Plik podpisu | (wymagany) |
| `scale-mode` | `percent-width` albo `original` | `percent-width` |
| `scale-percent` | Szerokość podpisu jako % szerokości zdjęcia | 15.0 |

Proporcje przy skalowaniu są zachowane. Przy zmniejszaniu wtyczka używa interpolacji LoHalo, przy powiększaniu NoHalo. Jeśli podpis nie mieści się w zdjęciu razem z marginesem, zostaje proporcjonalnie zmniejszony, a GIMP pokazuje ostrzeżenie.

## Wywołanie z PDB / skryptu (Python)

```python
proc = Gimp.get_pdb().lookup_procedure("python-fu-podpis-tekst")
cfg = proc.create_config()
cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
cfg.set_property("image", image)
cfg.set_core_object_array("drawables", image.get_selected_drawables())
cfg.set_property("text", "© Marek Zettel")
cfg.set_property("position", "bottom-right")
proc.run(cfg)
```

## Testy

- Czyste funkcje (pozycja, marginesy, rozmiary), bez GIMP-a:
  ```
  python -m pytest tests
  ```
- Test integracyjny w prawdziwym GIMP-ie (headless, wtyczka musi być zainstalowana):
  ```
  "%LOCALAPPDATA%\Programs\GIMP 3\bin\gimp-console-3.2.exe" -i --batch-interpreter python-fu-eval -b "exec(open(r'C:\ścieżka\do\repo\tests\gimp_integration.py', encoding='utf-8').read())" --quit
  ```
  Test sprawdza kryteria 2–6, 9 i 10 ze specyfikacji. Ostatni wynik: 37/37 PASS.

## Znane ograniczenia i odstępstwa od specyfikacji

- **Odstęp wokół tekstu.** Warstwa tekstowa ma wewnętrzny odstęp nad i pod literami (metryki fontu). Wizualny margines od dołu może więc wyjść nieco większy niż ustawiony.
- **`ProcedureSensitivityMask.ALWAYS` nie istnieje** w API 3.x. Zamiast niego wtyczka używa `DRAWABLE | DRAWABLES | NO_DRAWABLES`, dzięki czemu działa przy jednej warstwie, kilku warstwach i bez zaznaczenia.
- **Pola rozmiaru (px/%) i skali.** Nieaktywne pole jest wyszarzone przez `set_sensitive_if_in`, ale nie ukryte.
- **Margines px/%.** Oba pola są zawsze widoczne. Etykieta „Margines (px, 0 = użyj %)” mówi, które z nich obowiązuje.
- **Komunikaty błędów.** Wtyczka zwraca `EXECUTION_ERROR` z `GLib.Error` i GIMP sam wyświetla ten tekst. Dodatkowe `Gimp.message()` dałoby dwa identyczne okienka, więc `Gimp.message()` służy tylko do ostrzeżeń.
- **Nieistniejący plik** w wywołaniu PDB odrzuca już sam GIMP, na etapie walidacji argumentu (`CALLING_ERROR`). Wtyczka ma własną kontrolę na wypadek „Powtórz” z plikiem usuniętym w międzyczasie.
- **Font domyślny** pochodzi z kontekstu GIMP-a (`default_to_context`), bo w chwili rejestracji procedury nie można pobrać fontu po nazwie. Gdyby fontu nie było, wtyczka w czasie działania użyje „Sans-serif”.
- **Po nieudanej operacji nic nie zostaje.** Gdy warstwa zdążyła powstać, a potem wystąpił błąd, wtyczka ją usuwa, a grupa cofania i kontekst zawsze się zamykają (`try/finally`).
- **Obraz indeksowany (tryb B).** Jeśli wczytanie się nie powiedzie, komunikat podpowiada konwersję do RGB. Obraz w skali szarości działa bez konwersji (przetestowane).
