# Build distribuzione macOS (PyInstaller onedir + zip).
# Uso (su Mac):  ./tools/build-mac.sh [1.0.0] [universal2|arm64|x86_64]
# Output: dist/WOTSCLauncherMac.zip (pronto da allegare alla release).
# NOTA: PyInstaller non cross-compila: eseguire SOLO su Mac.
set -euo pipefail
VERSION="${1:-1.0.0}"
ARCH="${2:-universal2}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null; then
  echo "Serve python3 (python.org, 3.10+ universal2)." >&2
  exit 1
fi
python3 -m pip install -q pyinstaller requests pillow customtkinter

ICON_ARG=()
if [ -f "icona.icns" ]; then
  ICON_ARG=(--icon icona.icns)
elif [ -f "icona.ico" ]; then
  echo "ATTENZIONE: icona.icns mancante, uso icona.ico (solo segnaposto)."
  ICON_ARG=(--icon icona.ico)
fi

rm -rf build dist
python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --name WOTSCLauncherMac \
  --target-arch "$ARCH" \
  --osx-bundle-identifier coast.sword.wotsc-launcher-mac \
  --add-data "background.jpg:assets" \
  --add-data "exclude.ini:." \
  --add-data "mac-ignore.ini:." \
  "${ICON_ARG[@]}" \
  InstallerWotscMac.py

# Firma ad-hoc (niente Developer ID: l'utente apre con right-click -> Apri).
codesign --deep --force -s - "dist/WOTSCLauncherMac.app" || true

cat > "dist/Leggimi-mac.txt" <<EOF
WOTSC Launcher Mac v$VERSION - Whispers of the Sword Coast
==========================================================

1. Estrai lo zip dove vuoi.
2. Primo avvio: right-click su WOTSCLauncherMac -> Apri (Gatekeeper, app non firmata).
3. Premi Sfoglia e scegli la cartella del client.
4. Premi GIOCA!: scarica prima la base .7z condivisa, poi l'overlay Mac
   (WOTSC.client-mac.zip) copiato sopra; settings/macro restano intatti.
5. Serve .NET 10 per macOS: se manca, il launcher apre la pagina Microsoft.

NOTE
- config.ini, last_release.txt e cache nascono accanto all'app al primo uso.
- Problemi? Usa "Copia diagnostica" (log) e invialo a chi sviluppa.
EOF

cd dist
rm -f "WOTSCLauncherMac.zip"
ditto -c -k --sequesterRsrc --keepParent WOTSCLauncherMac.app Leggimi-mac.txt "WOTSCLauncherMac.zip" 2>/dev/null \
  || zip -qr "WOTSCLauncherMac.zip" WOTSCLauncherMac.app Leggimi-mac.txt
echo "OK: $ROOT/dist/WOTSCLauncherMac.zip"
