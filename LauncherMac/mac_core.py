"""Logica pura del LauncherMac (progetto laterale di WotscClient/Launcher).

Questo modulo NON dipende da GUI (niente customtkinter/tkinter): puo' essere
importato e testato ovunque, anche senza Mac e senza display.

Il modello di installazione Mac e' a due stadi (stesso tag release):
  1. base   = asset .7z condiviso (es. WOTSC.client.7z, gli asset UO sono
     identici su ogni OS per ClassicUO) -> merge con exclude.ini +
     mac-ignore.ini;
  2. overlay = asset .zip dedicato Mac (es. WOTSC.client-mac.zip: binario
     `wotsc` + lib + default) -> copiato SOPRA la base, con solo
     exclude.ini (l'overlay deve poter vincere).

Regole merge (identiche al launcher Windows):
  - mac-ignore.ini -> file della base da NON copiare mai su Mac
    (es. wotsc.exe). Skip sempre.
  - exclude.ini     -> file utente da non sovrascrivere: skip solo se la
    destinazione esiste gia', altrimenti si installa il default.
"""

import configparser
import fnmatch
import os
import platform
import shutil
import stat
import zipfile

GAME_EXE_MAC = "wotsc-mac"
GAME_EXE_WIN = "wotsc.exe"
# Su Mac il processo in esecuzione e' il runtime (lo script `wotsc-mac` fa
# exec di runtime/osx-*/cuo): pgrep deve cercare `cuo`, non lo script.
# NOTA: lo script NON puo' chiamarsi `wotsc` perche' collide con la cartella
# dati `wotsc/` della base (UGUALI anche su filesystem case-insensitive).
GAME_PROCESS_MAC = "cuo"

# Directory generate dall'utente da non distribuire mai e da non toccare in
# merge (nomi in minuscolo, confronto sul basename dei livelli visitati).
MERGE_SKIP_DIRS = frozenset({"journallogs", "logs"})

BASE_ASSET_EXT = ".7z"
OVERLAY_ASSET_EXT = ".zip"
MAC_ASSET_HINT = "mac"

SKIP_DIRS_BASE = frozenset({
    "$RECYCLE.BIN", "System Volume Information", ".git",
    "__pycache__", "$Recycle.Bin",
})
SKIP_DIRS_MAC_EXTRA = frozenset({
    ".Trashes", ".Spotlight-V100", ".DS_Store", "__MACOSX",
})

DOTNET_WIN_DIRS = ("ProgramFiles", "ProgramFiles(x86)")
DOTNET_MAC_DIRS = (
    "/usr/local/share/dotnet",
    "/opt/homebrew/share/dotnet",
    os.path.expanduser("~/.dotnet"),
)


# --- Piattaforma -----------------------------------------------------------

def effective_platform():
    """Ritorna 'windows'/'darwin'/...; FAKE_PLATFORM=darwin permette di
    eseguire il ramo Mac su Windows (test e CI senza Mac)."""
    fake = os.environ.get("FAKE_PLATFORM", "").strip().lower()
    if fake:
        return fake
    return (platform.system() or "").lower()


def is_mac(plat=None):
    p = (plat or effective_platform()).lower()
    return p == "darwin" or "mac" in p


def is_windows(plat=None):
    p = (plat or effective_platform()).lower()
    return p.startswith("win")


def game_binary(plat=None):
    return GAME_EXE_MAC if is_mac(plat) else GAME_EXE_WIN


def process_name(plat=None):
    """Nome processo da cercare: su Mac `cuo` (il runtime), altrove il binario."""
    return GAME_PROCESS_MAC if is_mac(plat) else game_binary(plat)


def skip_dirs(plat=None):
    base = set(SKIP_DIRS_BASE)
    if is_mac(plat):
        base |= set(SKIP_DIRS_MAC_EXTRA)
    return frozenset(base)


# --- File ini --------------------------------------------------------------

def load_list_from_ini(path, section, option):
    cfg = configparser.ConfigParser()
    if not os.path.exists(path):
        return []
    try:
        cfg.read(path)
    except Exception:
        return []
    if cfg.has_option(section, option):
        raw = cfg.get(section, option)
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return []


def load_exclude_list(exclude_ini):
    return load_list_from_ini(exclude_ini, "Exclude", "files")


def load_mac_ignore_list(mac_ignore_ini):
    """File della base da saltare su Mac (es. wotsc.exe)."""
    items = load_list_from_ini(mac_ignore_ini, "MacIgnore", "files")
    if not items:
        # fallback: vecchia sezione [Exclude] se il file riusa quel formato
        items = load_list_from_ini(mac_ignore_ini, "Exclude", "files")
    return items


def find_local_test_overlay(config_path, base_dir, auto_name="overlay-test.zip"):
    """Zip di prova locale (collaudo senza release): priorita' a
    config [Test] local_overlay, poi <base_dir>/overlay-test.zip.
    Ritorna il path o None."""
    cfg = configparser.ConfigParser()
    try:
        if config_path and os.path.exists(config_path):
            cfg.read(config_path)
            cand = (cfg.get("Test", "local_overlay", fallback="") or "").strip()
            cand = cand.strip('"').strip("'")
            if cand:
                if not os.path.isabs(cand) and base_dir:
                    cand = os.path.join(base_dir, cand)
                if os.path.isfile(cand):
                    return cand
    except Exception:
        pass
    auto = os.path.join(base_dir or "", auto_name) if base_dir else auto_name
    return auto if auto and os.path.isfile(auto) else None


# --- Selezione asset release -----------------------------------------------

def _asset_name(a):
    if isinstance(a, dict):
        return str(a.get("name", ""))
    return str(a)


def select_base_asset(assets):
    """Primo asset .7z (stessa regola del launcher Windows)."""
    for a in assets or []:
        if _asset_name(a).lower().endswith(BASE_ASSET_EXT):
            return a
    return None


def select_overlay_asset(assets):
    """Asset overlay Mac: nome che contiene 'mac' e finisce .zip.
    Ignora base .7z, minipatch.zip e zip non-Mac."""
    for a in assets or []:
        name = _asset_name(a).lower()
        if MAC_ASSET_HINT in name and name.endswith(OVERLAY_ASSET_EXT):
            return a
    return None


# --- Merge -----------------------------------------------------------------

def _ignored(name_low, base_low, ignore):
    for pat in ignore:
        if not pat:
            continue
        if "*" in pat or "?" in pat or "[" in pat:
            if fnmatch.fnmatchcase(name_low, pat) or fnmatch.fnmatchcase(base_low, pat):
                return True
        elif name_low == pat or base_low == pat:
            return True
    return False


def should_copy(relative_name, dest_exists, exclude=(), ignore=()):
    """Decide se copiare un file estratto sopra la destinazione.

    ignore  (mac-ignore.ini): skip sempre; supporta wildcard (*.pdb, *.dll).
    exclude (exclude.ini):    skip solo se dest esiste (file utente),
                              altrimenti si installa il default.
    """
    low = (relative_name or "").lower()
    base = os.path.basename(low)
    if _ignored(low, base, ignore):
        return False
    if (low in exclude or base in exclude) and dest_exists:
        return False
    return True


def merge_tree(src_dir, dest_dir, exclude=(), ignore=(),
               skip_dirs=MERGE_SKIP_DIRS):
    """Copia ricorsiva src->dest con regole should_copy. L'overlay vince
    (sovrascrive) salvo exclude su file esistenti. Le directory in skip_dirs
    (es. log utente) non vengono mai visitate. Ritorna (copiati, saltati)."""
    excl = {e.lower() for e in (exclude or [])}
    ign = {e.lower() for e in (ignore or [])}
    skip = {d.lower() for d in (skip_dirs if skip_dirs is not None else ())}
    copied, skipped = 0, 0
    for root_dir, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d.lower() not in skip]
        for f in files:
            full = os.path.join(root_dir, f)
            rel = os.path.relpath(full, src_dir)
            dest_full = os.path.join(dest_dir, rel)
            if not should_copy(rel, os.path.exists(dest_full), excl, ign):
                skipped += 1
                continue
            if os.path.lexists(dest_full):
                # File contro directory (o viceversa): mai fondere, si tiene
                # la destinazione esistente (difesa in profondita'; con i nomi
                # attuali non deve capitare: vedi nota su `wotsc-mac` sopra).
                if os.path.isdir(dest_full) and not os.path.isdir(full):
                    skipped += 1
                    continue
                try:
                    if os.path.isdir(dest_full) and not os.path.islink(dest_full):
                        shutil.rmtree(dest_full)
                    else:
                        os.remove(dest_full)
                except Exception:
                    skipped += 1
                    continue
            parent = os.path.dirname(dest_full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.move(full, dest_full)
            copied += 1
    return copied, skipped


def strip_single_top_dir(extracted_dir):
    """Se l'archivio contiene un'unica cartella wrapper (es. la base .7z con
    `WOTSC client/`), ritorna il suo percorso cosi' il merge appiattisce alla
    root di gioco; altrimenti ritorna extracted_dir invariata. Ignora dotfile."""
    try:
        entries = [e for e in os.listdir(extracted_dir) if not e.startswith(".")]
    except Exception:
        return extracted_dir
    if len(entries) == 1:
        cand = os.path.join(extracted_dir, entries[0])
        try:
            if os.path.isdir(cand) and not os.path.islink(cand):
                return cand
        except Exception:
            pass
    return extracted_dir


def extract_overlay_zip(zip_path, dest_dir, exclude=()):
    """Estrae lo zip Mac in una cartella temporanea e fa merge sopra dest.
    Ritorna (copiati, saltati)."""
    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="wotscmac_")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)
        return merge_tree(tmp_dir, dest_dir, exclude=exclude)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def ensure_executable(path):
    """chmod +x (best-effort). Su Windows no-op -> True."""
    if os.name == "nt":
        return True
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return True
    except Exception:
        return False


# --- Comandi OS-specifici (costruiti, non eseguiti: testabili) --------------

def process_check_command(binary=None, plat=None):
    """Comando per rilevare il client in esecuzione. Su Mac pgrep sul
    processo `cuo` (il runtime lanciato dallo script), su Windows tasklist
    sul binario (stesso comportamento del launcher originale)."""
    plat = plat or effective_platform()
    binary = binary or process_name(plat)
    if is_mac(plat):
        return ["pgrep", "-x", binary]
    return ["tasklist", "/FI", f"IMAGENAME eq {binary}", "/FO", "CSV", "/NH"]


def dotnet_search_dirs(plat=None):
    """Directory .NET da ispezionare oltre a `dotnet --list-runtimes`
    (che funziona su ogni OS e resta il check principale)."""
    plat = plat or effective_platform()
    if is_mac(plat):
        return list(DOTNET_MAC_DIRS)
    if is_windows(plat):
        roots = []
        for env in DOTNET_WIN_DIRS:
            root = os.environ.get(env)
            if root:
                roots.append(os.path.join(root, "dotnet", "shared"))
        return roots
    return ["/usr/share/dotnet", "/usr/lib/dotnet"]


def parse_system_profiler(output):
    """Estrae i nomi GPU dall'output di `system_profiler SPDisplaysDataType`
    (righe 'Chipset Model: ...')."""
    names = []
    for line in (output or "").splitlines():
        line = line.strip()
        if line.lower().startswith("chipset model:"):
            name = line.split(":", 1)[1].strip()
            if name:
                names.append(name)
    return names


# --- Diagnostica (per il tester: incolla-e-invia) ---------------------------

def build_diagnostics(info):
    """Compone il report testuale da un dict {chiave: valore}."""
    lines = ["WOTSC LauncherMac - diagnostica"]
    for key in sorted(info or {}):
        lines.append(f"{key}: {info[key]}")
    return "\n".join(lines) + "\n"
