# WOTSC LauncherMac (progetto laterale macOS)

Clone del launcher Windows (`../Launcher/InstallerWotsc.py`) con flusso identico,
ma installazione a **due stadi** e script di lancio **`wotsc-mac`**:

1. **base** `WOTSC.client.7z` (asset UO condivisi: verificato che `wotsc/` della
   base contiene gli stessi dati di `UOData/` del bundle Mac)
   → merge con `exclude.ini` + `mac-ignore.ini` (via i binari Windows);
2. **overlay** `WOTSC.client-mac.zip` (stesso tag release, nome con `mac` + `.zip`)
   → copiato **sopra** la base (solo `exclude.ini`, l'overlay vince) + `chmod +x`.

Contenuto overlay (percorsi relativi alla root di gioco): `wotsc-mac` (script di
lancio; NON `wotsc`: collide con la cartella dati `wotsc/` anche su FS
case-insensitive), `runtime/osx-arm64/`, `runtime/osx-x64/`,
`tile-shader-effects.json` (versione Mac). Niente `UOData/` (2,7 GB già nella
base), niente `settings.json` (protetto da `exclude.ini`; quello della base punta
già a `ultimaonlinedirectory: wotsc`), niente `Data/` (sottoinsieme identico),
niente file bundle (`Info.plist`, `Resources/`, `LEGGIMI`).

Il processo in esecuzione su Mac è `cuo` (lo script fa exec del runtime):
`is_game_running` usa `pgrep -x cuo`.

## File

- `InstallerWotscMac.py` — GUI (port di quella Windows: titolo, `GAME_EXE="wotsc-mac"`,
  `pgrep -x cuo` invece di `tasklist`, check `.NET` senza `ProgramFiles`, icona `.icns`,
  `SKIP_DIRS` + voci macOS, niente prompt GPU/OpenGL automatico).
- `mac_core.py` — logica pura senza GUI (asset, merge, comandi OS, diagnostica).
- `exclude.ini` — file utente mai sovrascritti (entrambi gli stadi).
- `mac-ignore.ini` — file della base da saltare sempre su Mac.
- `tools/build-mac.sh` — build PyInstaller **solo su Mac** (no cross-compile).
- `tests/` — pytest, gira anche su Windows (`FAKE_PLATFORM=darwin` per il ramo Mac).

## Overlay di prova (collaudo senza release)

Metti uno zip di prova accanto all'app come `overlay-test.zip`, oppure punta
un altro file in `config.ini`:

```ini
[Test]
local_overlay = mia-prova.zip
```

Al giro dopo la logica resta quella di rete (base `.7z` da GitHub), ma l'overlay
viene copiato dal file locale e lo status lo segnala ("Overlay di prova: ...").

## Sviluppo senza Mac

```powershell
python -m pytest LauncherMac/tests/ -v   # da WotscClient/
```

`icona.icns` mancante: convertire da `../Launcher/icona.ico` (es. su Mac con
`sips` o iconutil) e metterla qui. Senza, la build usa `icona.ico` come segnaposto.

## Release

Stesso tag della base: allegare `WOTSC.client-mac.zip` (layout relativo identico
alla base; dentro solo `wotsc`, dylib e default che devono prevalere) e
`dist/WOTSCLauncherMac.zip` dalla CI con tag `-mac-expN` finché è sperimentale.
