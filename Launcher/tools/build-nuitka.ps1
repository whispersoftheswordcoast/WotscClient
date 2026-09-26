# Build di distribuzione Nuitka (onedir + zip).
# Uso:  .\tools\build-nuitka.ps1 [-Version 1.0.0]
# Layout distribuzione (tutto ignorato da git, vedi .gitignore -> Provanuitka/):
#   Provanuitka\WOTSCLauncher\WOTSCLauncher.exe       stub vetrina (solo exe in root)
#   Provanuitka\WOTSCLauncher\Leggimi.txt
#   Provanuitka\WOTSCLauncher\app\...                runtime Nuitka intatto
#   Provanuitka\WOTSCLauncher-nuitka-v<Version>.zip   zip da allegare alla release
param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # ...\Launcher
$Tools = $PSScriptRoot
Set-Location -LiteralPath $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python del .venv non trovato: $VenvPython. Crea/attiva il .venv prima."
}
& $VenvPython -m nuitka --version 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka non installato nel .venv. Esegui: .\.venv\Scripts\python.exe -m pip install nuitka ordered-set zstandard"
}

# Versione in formato quad numerico per le risorse Windows ("1.0.0" -> "1.0.0.0").
$vParts = @($Version.Split(".") + @("0", "0", "0", "0"))[0..3]
$vQuad = ($vParts -join ".")
$vComma = ($vParts -join ",")

$OutDir = Join-Path $Root "Provanuitka"
$RawDist = Join-Path $OutDir "InstallerWotsc.dist"   # nome tecnico dato da Nuitka (dal .py)
$Stage = Join-Path $OutDir "WOTSCLauncher"           # nome pulito per la distribuzione
$AppDir = Join-Path $Stage "app"                     # runtime Nuitka intatto
$Zip = Join-Path $OutDir "WOTSCLauncher-nuitka-v$Version.zip"
$StubBuild = Join-Path $OutDir "stub-build"          # intermedi stub (ignorati da git)

if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
$StaleApp = Join-Path $OutDir "app"   # residuo di run precedenti: app/ vive dentro Stage
if (Test-Path -LiteralPath $StaleApp) { Remove-Item -LiteralPath $StaleApp -Recurse -Force }

& $VenvPython -m nuitka `
    --standalone `
    --output-dir=Provanuitka `
    --output-filename=WOTSCLauncher-nuitka.exe `
    --enable-plugin=tk-inter `
    --include-data-files=background.jpg=assets/background.jpg `
    --include-data-files=icona.ico=assets/icona.ico `
    --include-data-files=exclude.ini=exclude.ini `
    --windows-icon-from-ico=icona.ico `
    --windows-console-mode=disable `
    --company-name="Whispers of the Sword Coast" `
    --product-name="WOTSC Launcher" `
    --file-description="WOTSC Launcher - Whispers of the Sword Coast" `
    --file-version=$Version `
    --product-version=$Version `
    --copyright="Whispers of the Sword Coast" `
    --assume-yes-for-downloads `
    InstallerWotsc.py
if ($LASTEXITCODE -ne 0) { throw "Build Nuitka fallita (exit $LASTEXITCODE)." }

# Il runtime Nuitka va intatto nella sottocartella app/.
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
Move-Item -LiteralPath $RawDist -Destination $AppDir

# --- Stub vetrina: tools/stub.c compilato con MSVC + icona/versione da stub.rc ---
if (Test-Path -LiteralPath $StubBuild) { Remove-Item -LiteralPath $StubBuild -Recurse -Force }
New-Item -ItemType Directory -Path $StubBuild -Force | Out-Null

$msvcBase = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC"
$msvcVer = (Get-ChildItem -LiteralPath $msvcBase -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
if (-not $msvcVer) { throw "MSVC non trovato in $msvcBase." }
$sdkBase = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = (Get-ChildItem -LiteralPath (Join-Path $sdkBase "Include") -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
if (-not $sdkVer) { throw "Windows SDK non trovato in $sdkBase\Include." }
Write-Host "Toolchain stub: MSVC $msvcVer + SDK $sdkVer"

$msvcBin = "$msvcBase\$msvcVer\bin\Hostx64\x64"
$env:INCLUDE = "$msvcBase\$msvcVer\include;$sdkBase\Include\$sdkVer\ucrt;$sdkBase\Include\$sdkVer\um;$sdkBase\Include\$sdkVer\shared"
$env:LIB = "$msvcBase\$msvcVer\lib\x64;$sdkBase\Lib\$sdkVer\ucrt\x64;$sdkBase\Lib\$sdkVer\um\x64"
$env:PATH = "$msvcBin;$sdkBase\bin\$sdkVer\x64;$env:PATH"

$rcTemplate = Get-Content -LiteralPath (Join-Path $Tools "stub.rc.template") -Raw
$iconPath = ((Join-Path $Root "icona.ico") -replace "\\", "/")  # rc.exe legge \a come escape: slash
$rcTemplate.Replace("@VERSION_COMMA@", $vComma).Replace("@VERSION_STR@", $vQuad).Replace("@ICON_PATH@", $iconPath) |
    Out-File -FilePath (Join-Path $StubBuild "stub.rc") -Encoding ascii
& "$sdkBase\bin\$sdkVer\x64\rc.exe" /nologo /fo (Join-Path $StubBuild "stub.res") (Join-Path $StubBuild "stub.rc")
if ($LASTEXITCODE -ne 0) { throw "rc.exe fallito (exit $LASTEXITCODE)." }
& "$msvcBin\cl.exe" /nologo /O1 /MT /W3 /c /Fo"$StubBuild\\" (Join-Path $Tools "stub.c")
if ($LASTEXITCODE -ne 0) { throw "Compilazione stub fallita (exit $LASTEXITCODE)." }
& "$msvcBin\link.exe" /nologo /OUT:"$Stage\WOTSCLauncher.exe" /SUBSYSTEM:WINDOWS `
    (Join-Path $StubBuild "stub.obj") (Join-Path $StubBuild "stub.res") `
    user32.lib shell32.lib
if ($LASTEXITCODE -ne 0) { throw "Link stub fallito (exit $LASTEXITCODE)." }

$Leggimi = @"
WOTSC Launcher v$Version - Whispers of the Sword Coast
======================================================

INSTALLAZIONE
1. Estrai lo zip dove vuoi (es. C:\Giochi\WOTSCLauncher).
2. Avvia WOTSCLauncher.exe (lascia la cartella app\ dov'e').
3. Premi Sfoglia e scegli la cartella del client: vale quella allineata
   (un livello sopra la cartella di wotsc.exe, dove atterrano gli update);
   se serve, il launcher propone lui il riallineamento.
4. Premi GIOCA!

NOTE
- Nella root c'e' solo l'eseguibile: tutto il resto sta in app\.
- app\assets\ contiene i media statici (background, icona): non toccare.
- app\exclude.ini elenca i file del client da NON sovrascrivere in
  aggiornamento (es. impostazioni personali): puoi modificarlo, viene
  distribuito con un default e non serve ricompilare per cambiarlo.
- config.ini, last_release.txt e cache vengono creati dentro app\ al
  primo uso: cancellali per resettare il launcher.
- Serve .NET 10 per avviare wotsc.exe: se manca, il launcher te lo segnala.
- Richiede connessione per controllare gli aggiornamenti su GitHub.
- Flag OpenGL in basso a sinistra: forza il renderer OpenGL (force_driver),
  consigliato sulle GPU datate; il launcher lo propone da solo al primo
  avvio se rileva una scheda debole (scelta ricordata in config.ini).
"@
$Leggimi | Out-File -FilePath (Join-Path $Stage "Leggimi.txt") -Encoding utf8

Compress-Archive -Path $Stage -DestinationPath $Zip

Write-Host ""
Write-Host "OK: $Stage"
Write-Host "OK: $Zip"
