# Instaluje wtyczki z tego folderu do GIMP 3.x (Windows):
#   podpis-zdjecia – „Podpis na zdjęciu”
#   meme-maker     – „Kreator memów” (+ czcionka Anton do folderu czcionek GIMP-a)
#   lut-cube       – „LUT-y (.cube)” (bez plików .cube – te importujesz we wtyczce)
# Użycie:  powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Version 3.2] [-Plugin meme-maker]
param(
    [string]$Version,
    [string[]]$Plugin
)

$ErrorActionPreference = "Stop"
$known = @("podpis-zdjecia", "meme-maker", "lut-cube")
$gimpRoot = Join-Path $env:APPDATA "GIMP"

if (-not $Plugin) {
    # Domyślnie: wszystkie wtyczki, których foldery leżą obok skryptu.
    $Plugin = $known | Where-Object { Test-Path (Join-Path $PSScriptRoot $_) }
}
if (-not $Plugin) {
    throw "Nie znaleziono folderu wtyczki obok install.ps1."
}

if (-not $Version) {
    # Najnowszy katalog konfiguracji 3.x (np. 3.0, 3.2), bez kopii zapasowych.
    $dir = Get-ChildItem $gimpRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^3\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending |
        Select-Object -First 1
    if (-not $dir) {
        throw "Nie znaleziono katalogu $gimpRoot\3.x - uruchom GIMP 3 raz albo podaj -Version."
    }
    $Version = $dir.Name
}

$dest = Join-Path $gimpRoot "$Version\plug-ins"
New-Item -ItemType Directory -Force $dest | Out-Null

foreach ($name in $Plugin) {
    $src = Join-Path $PSScriptRoot $name
    if (-not (Test-Path $src)) { throw "Brak folderu wtyczki: $src" }
    Copy-Item $src $dest -Recurse -Force
    Write-Host "Zainstalowano do: $(Join-Path $dest $name)"

    if ($name -eq "meme-maker") {
        # Czcionka Anton (SIL OFL) do folderu czcionek GIMP-a.
        $fonts = Join-Path $gimpRoot "$Version\fonts"
        New-Item -ItemType Directory -Force $fonts | Out-Null
        Copy-Item (Join-Path $src "fonts\Anton-Regular.ttf") $fonts -Force
        Copy-Item (Join-Path $src "fonts\OFL.txt") (Join-Path $fonts "Anton-OFL.txt") -Force
        Write-Host "Czcionka Anton skopiowana do: $fonts"
    }

    if ($name -eq "lut-cube") {
        # Pliki .cube NIE są kopiowane. Jeśli obok skryptu jest folder Luts,
        # zapisujemy tylko jego ścieżkę – wtyczka zaproponuje import startowy.
        $luts = Get-ChildItem $PSScriptRoot -Directory |
            Where-Object { $_.Name -ieq "Luts" } | Select-Object -First 1
        if ($luts) {
            $hint = Join-Path $dest "lut-cube\startup-dir.txt"
            Set-Content $hint $luts.FullName -Encoding utf8
            Write-Host "Folder importu startowego LUT-ow: $($luts.FullName)"
        }
        if (-not (Test-Path (Join-Path $src "vendor\VENDOR.json"))) {
            Write-Host "Uwaga: brak numpy w lut-cube\vendor - wtyczka zadziala wolniej."
            Write-Host "Szybki wariant: powershell -ExecutionPolicy Bypass -File .\tools\fetch-numpy.ps1"
        }
    }
}

Write-Host "Uruchom ponownie GIMP. Menu: Filtry -> Podpis / Filtry -> Memy / Filtry -> LUT."
