# Pobiera numpy dla Pythona wbudowanego w GIMP 3.2 (Windows) i rozpakowuje
# go do lut-cube\vendor\. Bez tego wtyczka LUT działa w wolniejszym wariancie
# „czysty Python”.
#
# Dlaczego nie „pip install numpy”? GIMP na Windows ma Pythona zbudowanego
# w środowisku MSYS2 CLANG64 (moduły *.cp314-mingw_x86_64_ucrt_llvm.pyd,
# libpython3.14.dll). Koła numpy z PyPI są dla Pythona z python.org (MSVC)
# i się w nim nie załadują. Dlatego bierzemy gotowe paczki MSYS2 – zbudowane
# dokładnie pod ten ABI – i sprawdzamy ich sumy SHA-256.
#
# Licencje: numpy – BSD-3-Clause, OpenBLAS – BSD-3-Clause (kopiowane do vendor\).
#
# Użycie:  powershell -ExecutionPolicy Bypass -File .\tools\fetch-numpy.ps1

$ErrorActionPreference = "Stop"

$mirror = "https://mirror.msys2.org/mingw/clang64"
$packages = @(
    @{ File = "mingw-w64-clang-x86_64-python-numpy-2.5.3-1-any.pkg.tar.zst"
       Sha256 = "b6c20f497c5f22fd907fe26c0f1a792be8ff17c663d27327a210bd08d96be0f1" },
    @{ File = "mingw-w64-clang-x86_64-openblas-0.3.34-1-any.pkg.tar.zst"
       Sha256 = "3536654fd43a6aba07c4019ce27a623f10528913efc5bd559b95e282cfdb9f7f" }
)
# Pakiety MSYS2 są zbudowane dla tego Pythona – wtyczka sprawdza to przy imporcie.
$pythonVersion = "3.14"
$extSuffix = ".cp314-mingw_x86_64_ucrt_llvm.pyd"

$repo = Split-Path $PSScriptRoot -Parent
$vendor = Join-Path $repo "lut-cube\vendor"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("lut-cube-numpy-" + [guid]::NewGuid())
$tar = Join-Path $env:SystemRoot "System32\tar.exe"   # bsdtar z obsługą zstd

New-Item -ItemType Directory -Force $work | Out-Null
try {
    foreach ($p in $packages) {
        $dest = Join-Path $work $p.File
        Write-Host "Pobieram $($p.File)…"
        Invoke-WebRequest -Uri "$mirror/$($p.File)" -OutFile $dest -UseBasicParsing
        $hash = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $p.Sha256) {
            throw "Zła suma SHA-256 pliku $($p.File): $hash"
        }
        & $tar -xf $dest -C $work
        if ($LASTEXITCODE -ne 0) { throw "Nie udało się rozpakować $($p.File)" }
    }

    if (Test-Path $vendor) { Remove-Item $vendor -Recurse -Force }
    $site = Join-Path $vendor "site-packages"
    $bin = Join-Path $vendor "bin"
    $lic = Join-Path $vendor "licenses"
    New-Item -ItemType Directory -Force $site, $bin, $lic | Out-Null

    $src = Join-Path $work "clang64\lib\python3.14\site-packages"
    Copy-Item (Join-Path $src "numpy") $site -Recurse
    Copy-Item (Join-Path $work "clang64\bin\libopenblas.dll") $bin

    # Odchudzenie: testy, f2py, podpowiedzi typów – niepotrzebne w działaniu.
    Get-ChildItem (Join-Path $site "numpy") -Recurse -Directory -Filter tests |
        Remove-Item -Recurse -Force
    foreach ($d in @("f2py", "_pyinstaller")) {
        $path = Join-Path $site "numpy\$d"
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
    Get-ChildItem (Join-Path $site "numpy") -Recurse -Include *.pyi, *.pxd |
        Remove-Item -Force

    $numpyLic = Get-ChildItem $src -Directory -Filter "numpy-*.dist-info" | Select-Object -First 1
    Copy-Item (Join-Path $numpyLic.FullName "licenses") (Join-Path $lic "numpy") -Recurse
    Copy-Item (Join-Path $work "clang64\share\licenses\OpenBLAS") (Join-Path $lic "OpenBLAS") -Recurse

    $info = [ordered]@{
        python     = $pythonVersion
        ext_suffix = $extSuffix
        numpy      = "2.5.3"
        openblas   = "0.3.34"
        source     = $mirror
        packages   = @($packages | ForEach-Object { $_.File })
    }
    $info | ConvertTo-Json | Set-Content (Join-Path $vendor "VENDOR.json") -Encoding utf8

    $size = (Get-ChildItem $vendor -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("Gotowe: {0} ({1:N0} MB)" -f $vendor, $size)
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
