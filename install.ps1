# Instaluje wtyczkę „Podpis na zdjęciu” do GIMP 3.x (Windows).
# Użycie:  powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Version 3.2]
param(
    [string]$Version
)

$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "podpis-zdjecia"
$gimpRoot = Join-Path $env:APPDATA "GIMP"

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
Copy-Item $src $dest -Recurse -Force
Write-Host "Zainstalowano do: $(Join-Path $dest 'podpis-zdjecia')"
Write-Host "Uruchom ponownie GIMP. Menu: Filtry -> Podpis."
