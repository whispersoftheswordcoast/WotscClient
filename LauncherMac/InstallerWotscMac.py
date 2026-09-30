import customtkinter as ctk
from tkinter import filedialog
import threading
import requests
import os
import platform
import tempfile
import configparser
import json
import py7zr
import shutil
import stat
import subprocess
import sys
import time
import webbrowser
import zipfile

try:
    from mac_core import (
        effective_platform as _mac_effective_platform,
        is_mac as _mac_is_mac,
        load_exclude_list as _core_load_exclude,
        load_mac_ignore_list as _core_load_mac_ignore,
        select_base_asset as _core_select_base,
        select_overlay_asset as _core_select_overlay,
        merge_tree as _core_merge_tree,
        ensure_executable as _core_ensure_executable,
        strip_single_top_dir as _core_strip_wrapper,
        build_diagnostics as _core_build_diagnostics,
    )
    _HAS_MAC_CORE = True
except Exception:
    _HAS_MAC_CORE = False

# --- Config paths robusti per PyInstaller ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    MEI_DIR = getattr(sys, '_MEIPASS', BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    MEI_DIR = BASE_DIR

REPO_OWNER = "whispersoftheswordcoast"
REPO_NAME = "WotscClient"
DOTNET_DOWNLOAD_URL = "https://dotnet.microsoft.com/download/dotnet/10.0"
GAME_EXE = "wotsc-mac"  # Mac: script di lancio alla root (NON `wotsc`: collide
# con la cartella dati `wotsc/` della base, anche su FS case-insensitive).
# Il processo vivo resta `cuo` (vedi is_game_running).
STATE_FILE = os.path.join(BASE_DIR, "last_release.txt")
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")
EXCLUDE_INI = os.path.join(BASE_DIR, "exclude.ini")
MAC_IGNORE_INI = os.path.join(BASE_DIR, "mac-ignore.ini")
MAC_OVERLAY_HINT = "mac"  # l'asset overlay Mac contiene 'mac' e finisce .zip

def _is_mac_runtime():
    # Ramo Mac anche su Windows con FAKE_PLATFORM=darwin (test/CI senza Mac).
    if _HAS_MAC_CORE:
        try:
            return bool(_mac_is_mac())
        except Exception:
            pass
    fake = os.environ.get("FAKE_PLATFORM", "").strip().lower()
    if fake:
        return fake == "darwin" or "mac" in fake
    return (platform.system() or "").lower() == "darwin"

def _asset_path(filename):
    # Layout distribuzione: media statici in assets/, con fallback alla root
    # per dev (sorgenti) e vecchie build. Ritorna il primo esistente.
    for cand in (os.path.join(MEI_DIR, "assets", filename),
                 os.path.join(MEI_DIR, filename),
                 os.path.join(BASE_DIR, "assets", filename),
                 os.path.join(BASE_DIR, filename)):
        if os.path.isfile(cand):
            return cand
    return os.path.join(MEI_DIR, "assets", filename)

BACKGROUND_IMAGE = _asset_path("background.jpg")  # 600x420 consigliati
def _icon_file():
    # Su Mac si preferisce icona.icns, con fallback a icona.ico (dev/Windows).
    for name in ("icona.icns", "icona.ico"):
        p = _asset_path(name)
        if os.path.isfile(p):
            return p
    return _asset_path("icona.icns")
ICON_FILE = _icon_file()
RELEASE_CACHE_TTL = 900  # riusa releases/latest per 15 minuti (Gioca + Aggiorna)
RELEASE_CACHE_FILE = os.path.join(BASE_DIR, "last_release_cache.json")
RELEASE_RETRY_BACKOFF = 300  # dopo un errore, max 1 retry API ogni 5 minuti

def load_exclude_list():
    if _HAS_MAC_CORE:
        try:
            return _core_load_exclude(EXCLUDE_INI)
        except Exception:
            pass
    cfg = configparser.ConfigParser()
    if not os.path.exists(EXCLUDE_INI):
        return []
    cfg.read(EXCLUDE_INI)
    if cfg.has_option("Exclude", "files"):
        raw = cfg.get("Exclude", "files")
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return []

def load_mac_ignore_list():
    # File della base 7z da NON copiare su Mac (es. wotsc.exe). Skip sempre.
    if _HAS_MAC_CORE:
        try:
            return _core_load_mac_ignore(MAC_IGNORE_INI)
        except Exception:
            pass
    cfg = configparser.ConfigParser()
    if not os.path.exists(MAC_IGNORE_INI):
        return []
    try:
        cfg.read(MAC_IGNORE_INI)
    except Exception:
        return []
    for section in ("MacIgnore", "Exclude"):
        if cfg.has_option(section, "files"):
            raw = cfg.get(section, "files")
            return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return []

def get_diagnostics(folder="", extra=None):
    # Report testuale per il tester (Copia diagnostica). Niente GUI qui.
    info = {
        "binary": GAME_EXE,
        "folder": folder or "",
        "has_mac_core": _HAS_MAC_CORE,
        "is_mac_runtime": _is_mac_runtime(),
        "platform": platform.system(),
        "python": platform.python_version(),
    }
    try:
        r = subprocess.run(["dotnet", "--list-runtimes"],
                           capture_output=True, text=True, timeout=15)
        info["dotnet_runtimes"] = (r.stdout.strip().splitlines()[:5]
                                   if r.returncode == 0 else f"exit {r.returncode}")
    except Exception as e:
        info["dotnet_runtimes"] = f"non rilevabile: {e}"
    if folder and os.path.isdir(folder):
        try:
            entries = sorted(os.listdir(folder))[:20]
            info["folder_entries"] = ", ".join(entries) or "(vuota)"
        except Exception as e:
            info["folder_entries"] = f"non leggibile: {e}"
    if extra:
        info.update(extra)
    if _HAS_MAC_CORE:
        try:
            return _core_build_diagnostics(info)
        except Exception:
            pass
    return "\n".join(f"{k}: {info[k]}" for k in sorted(info)) + "\n"

PIXEL_FONT = ("Fixedsys", 12)
BTN_COLOR = "#8B4513"
BTN_HOVER = "#A0522D"

def _styled_dialog_base(parent, title, message):
    dlg = ctk.CTkToplevel(parent)
    dlg.title(title)
    try:
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - 440) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - 230) // 2)
        dlg.geometry(f"440x230+{x}+{y}")
    except Exception:
        dlg.geometry("440x230")
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()
    label = ctk.CTkLabel(dlg, text=message, font=PIXEL_FONT,
                         wraplength=390, justify="center")
    label.pack(padx=25, pady=(28, 12))
    frame = ctk.CTkFrame(dlg, fg_color="transparent")
    frame.pack(pady=10)
    return dlg, frame

def ask_styled(parent, title, message, yes_text="Sì", no_text="No"):
    """Popup Sì/No in stile client. Ritorna True (Sì) o False (No/X)."""
    result = {"value": False}
    dlg, frame = _styled_dialog_base(parent, title, message)

    def choose(v):
        result["value"] = v
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()

    btn_yes = ctk.CTkButton(frame, text=yes_text, command=lambda: choose(True),
                            width=150, height=30, font=PIXEL_FONT,
                            fg_color=BTN_COLOR, hover_color=BTN_HOVER, corner_radius=3)
    btn_yes.pack(side="left", padx=10)
    btn_no = ctk.CTkButton(frame, text=no_text, command=lambda: choose(False),
                           width=150, height=30, font=PIXEL_FONT,
                           fg_color=BTN_COLOR, hover_color=BTN_HOVER, corner_radius=3)
    btn_no.pack(side="left", padx=10)
    dlg.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    dlg.wait_window()
    return result["value"]

def info_styled(parent, title, message, button="OK"):
    """Popup informativo a un bottone, in stile client."""
    dlg, frame = _styled_dialog_base(parent, title, message)

    def close():
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()

    btn = ctk.CTkButton(frame, text=button, command=close,
                        width=150, height=30, font=PIXEL_FONT,
                        fg_color=BTN_COLOR, hover_color=BTN_HOVER, corner_radius=3)
    btn.pack(padx=10)
    dlg.protocol("WM_DELETE_WINDOW", close)
    dlg.wait_window()

class WOTSCDownloader:
    def __init__(self, root):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = root
        self.root.geometry("600x420")
        self.root.title("WOTSC Downloader (Mac)")
        self.root.resizable(False, False)
        try:
            if os.path.exists(ICON_FILE):
                self.root.iconbitmap(ICON_FILE)
        except Exception:
            pass

        self.exclude_list = load_exclude_list()
        self.mac_ignore_list = load_mac_ignore_list()
        self.dots_running = False
        self.current_status_base = ""
        self.wotsc_path_cached = None
        self._syncing_opengl = False
        self._last_release_error = None
        self._last_release_stale = False
        self._release_cache_data = None
        self._release_cache_ts = 0.0
        self._release_cache_lock = threading.Lock()
        self._release_next_retry = 0.0
        self._release_backoff_error = None
        self._load_release_cache()

        # --- Background ---
        self.bg_image = None
        if os.path.exists(BACKGROUND_IMAGE):
            try:
                from PIL import Image
                img = Image.open(BACKGROUND_IMAGE).resize((600,420))
                self.bg_image = ctk.CTkImage(light_image=img, dark_image=img, size=(600,420))
                self.bg_label = ctk.CTkLabel(root, image=self.bg_image, text="")
                self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            except Exception as e:
                print("Errore caricamento background:", e)

        # --- Font pixel art ---
        self.pixel_font = PIXEL_FONT

        # --- Cartella ---
        self.folder_var = ctk.StringVar()
        self.entry_folder = ctk.CTkEntry(root, textvariable=self.folder_var, width=350, height=25, font=self.pixel_font)
        self.entry_folder.place(x=120, y=40)

        self.select_btn = ctk.CTkButton(root, text="Sfoglia", command=self.select_folder,
                                        width=80, height=25, font=self.pixel_font,
                                        fg_color="#8B4513", hover_color="#A0522D", corner_radius=3)
        self.select_btn.place(x=480, y=36)

        # --- Status ---
        self.status_label = ctk.CTkLabel(root, text="Pronto", font=self.pixel_font)
        self.status_label.place(x=120, y=80)

        # --- Progress bar (colore uguale ai pulsanti) ---
        self.progress_bar = ctk.CTkProgressBar(root, width=420, height=20, fg_color="#5C4033", progress_color="#8B4513", corner_radius=0 )
        self.progress_bar.place(x=90, y=110)
        self.progress_bar.set(0)

        # --- Bottoni ---
        self.force_btn = ctk.CTkButton(root, text="Forza aggiornamento",
                                       command=lambda: self.start_download(force=True),
                                       width=200, height=30, font=self.pixel_font,
                                       fg_color="#8B4513", hover_color="#A0522D", corner_radius=3)
        self.force_btn.place(x=280, y=380)

        self.launch_btn = ctk.CTkButton(root, text="GIOCA!", command=self.launch_wotsc,
                                         width=100, height=30, font=self.pixel_font,
                                         fg_color="#8B4513", hover_color="#A0522D", corner_radius=3)
        self.launch_btn.place(x=490, y=380)

        # --- Flag OpenGL (force_driver in settings.json della cartella scelta) ---
        self.opengl_var = ctk.BooleanVar(value=False)
        self.opengl_check = ctk.CTkCheckBox(root, text="OpenGL", variable=self.opengl_var,
                                            command=self.toggle_opengl, font=("Fixedsys", 9),
                                            fg_color="#8B4513", hover_color="#A0522D", corner_radius=3,
                                            checkbox_width=16, checkbox_height=16)
        self.opengl_check.place(x=8, y=394)

        self.thread = None
        self.load_folder()

        # --- Aggiungi ombre ai pulsanti ---
        self.root.after(100, lambda: self.add_shadow(self.select_btn))
        self.root.after(100, lambda: self.add_shadow(self.force_btn))
        self.root.after(100, lambda: self.add_shadow(self.launch_btn))

    def add_shadow(self, widget):
        # Funzione per creare una "finta ombra" usando un CTkFrame dietro il pulsante
        x, y = widget.winfo_x(), widget.winfo_y()
        w, h = widget.winfo_width(), widget.winfo_height()
        shadow = ctk.CTkFrame(self.root, width=w, height=h, fg_color="black", corner_radius=widget.cget("corner_radius"))
        shadow.place(x=x+2, y=y+2)
        widget.lift()

    # --- Funzioni ---
    def load_folder(self):
        cfg = configparser.ConfigParser()
        if os.path.exists(CONFIG_FILE):
            cfg.read(CONFIG_FILE)
            path = cfg.get("Settings","extract_folder",fallback="")
            if os.path.exists(path): self.folder_var.set(path)
            self.wotsc_path_cached = cfg.get("Settings","wotsc_path",fallback=None)
        self.refresh_opengl_flag()

    def _find_nested_wotsc_folder(self, base, max_depth=5):
        # Cerca la cartella contenente GAME_EXE dentro 'base', fino a max_depth
        # livelli (1 = figli immediati). Ritorna il match più shallow, a parità
        # quello alfabetico, oppure None. Solo directory, mai file.
        # Guard anti-freeze: salta link/dir di sistema, nomi papabili prima,
        # poi cap conteggio + budget tempo (mai bloccare la GUI).
        SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".git",
                     "__pycache__", "$Recycle.Bin",
                     ".Trashes", ".Spotlight-V100", ".DS_Store", "__MACOSX"}
        PRIORITY_KEYS = ("wotsc", "client", "ultima", "game", "shard", "classicuo", "cuo")
        MAX_DIRS = 20000
        TIME_BUDGET = 5.0
        base = os.path.normpath(os.path.abspath(base))
        current = [base]
        scanned = 0
        deadline = time.monotonic() + TIME_BUDGET
        out_of_budget = False
        for _ in range(1, max_depth + 1):
            hits = []
            nxt = []
            for d in current:
                try:
                    entries = sorted(os.listdir(d),
                                     key=lambda e: (0 if any(k in e.lower() for k in PRIORITY_KEYS) else 1,
                                                    e.lower()))
                except Exception:
                    continue
                for e in entries:
                    if out_of_budget:
                        break
                    if e in SKIP_DIRS or e.startswith("."):
                        continue
                    full = os.path.join(d, e)
                    try:
                        if os.path.islink(full):
                            continue
                        if not os.path.isdir(full):
                            continue
                    except Exception:
                        continue
                    scanned += 1
                    if scanned > MAX_DIRS or time.monotonic() > deadline:
                        out_of_budget = True
                        break
                    if os.path.isfile(os.path.join(full, GAME_EXE)):
                        hits.append(full)
                    nxt.append(full)
                if out_of_budget:
                    break
            if hits:
                return sorted(hits)[0]
            if not nxt or out_of_budget:
                break
            current = nxt
        return None

    def select_folder(self):
        path = filedialog.askdirectory()
        if not path:
            return
        picked = os.path.normpath(os.path.abspath(path))
        wotsc_here = os.path.join(picked, GAME_EXE)
        wotsc_parent = os.path.join(os.path.dirname(picked), GAME_EXE)
        found_folder = None
        align_target = None
        if os.path.isfile(wotsc_here):
            found_folder = picked
            align_target = os.path.dirname(picked)
        elif os.path.isfile(wotsc_parent):
            found_folder = os.path.dirname(picked)
            align_target = os.path.dirname(picked)
        else:
            found_folder = self._find_nested_wotsc_folder(picked, max_depth=5)
            if found_folder is not None:
                align_target = os.path.dirname(found_folder)
        if found_folder is None:
            # Nessun client entro 5 livelli: fresh-install, nessun giudizio.
            path = picked
            self.folder_var.set(path)
            self.wotsc_path_cached = None
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
            if "Settings" not in cfg:
                cfg["Settings"] = {}
            cfg["Settings"]["extract_folder"] = path
            try:
                cfg.remove_option("Settings", "wotsc_path")
            except Exception:
                pass
            with open(CONFIG_FILE,"w") as f: cfg.write(f)
            self.refresh_opengl_flag()
            self.safe_status("wotsc.exe non trovato qui (ricerca a 5 livelli)")
            return
        # Regola unica: allineata solo se scelta == target (un livello sopra
        # la cartella dell'exe, dove il merge estrattivo atterra). Dalla root
        # di un disco non si sale: la si considera allineata.
        if (os.path.normcase(align_target) == os.path.normcase(picked)
                or os.path.dirname(align_target) == align_target):
            info_styled(self.root, "Installazione preesistente trovata",
                        f"{GAME_EXE} rilevato in:\n{found_folder}\n\n"
                        "Nessun riallineamento necessario.")
        else:
            allinea = ask_styled(
                self.root, "Eseguibile trovato",
                f"Gli aggiornamenti si installano un livello sopra.\n\n"
                f"Usare come cartella:\n{align_target}\n\n"
                "(Sì = allinea, No = mantieni scelta)")
            if allinea:
                picked = align_target
        path = picked
        self.folder_var.set(path)
        self.wotsc_path_cached = None
        cfg = configparser.ConfigParser()
        if os.path.exists(CONFIG_FILE):
            cfg.read(CONFIG_FILE)
        if "Settings" not in cfg:
            cfg["Settings"] = {}
        cfg["Settings"]["extract_folder"] = path
        try:
            cfg.remove_option("Settings", "wotsc_path")
        except Exception:
            pass
        with open(CONFIG_FILE,"w") as f: cfg.write(f)
        self.refresh_opengl_flag()
        self.safe_status(f"Client rilevato in: {found_folder}")

    # --- OpenGL (force_driver) + GPU datate ---
    def _client_dir(self, folder):
        # Cartella di wotsc.exe (li' vive settings.json). Cache in memoria,
        # mai su disco: la persistenza resta a _do_launch.
        wp = self.wotsc_path_cached
        if wp and os.path.exists(wp):
            return os.path.dirname(wp)
        try:
            for root_dir, _dirs, files in os.walk(folder):
                for f in files:
                    if f.lower() == GAME_EXE:
                        return root_dir
        except Exception:
            pass
        return folder

    def read_force_driver(self, folder):
        try:
            with open(os.path.join(self._client_dir(folder), "settings.json"),
                      encoding="utf-8") as fh:
                data = json.load(fh)
            return 1 if isinstance(data, dict) and data.get("force_driver") == 1 else 0
        except Exception:
            return 0

    def write_force_driver(self, folder, value):
        # Merge chirurgico: solo la chiave force_driver. Su JSON illeggibile
        # non scrive (non distrugge impostazioni utente). Ritorna True/False.
        d = self._client_dir(folder)
        fp = os.path.join(d, "settings.json")
        data = {}
        if os.path.exists(fp):
            try:
                with open(fp, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                return False
            if not isinstance(data, dict):
                return False
            bak = fp + ".bak"
            if not os.path.exists(bak):
                try:
                    shutil.copy2(fp, bak)
                except Exception:
                    pass
        data["force_driver"] = 1 if value else 0
        try:
            os.makedirs(d, exist_ok=True)
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, fp)
            return True
        except Exception:
            return False

    def refresh_opengl_flag(self):
        folder = self.folder_var.get().strip()
        valid = bool(folder and os.path.exists(folder))
        try:
            self.opengl_check.configure(state="normal" if valid else "disabled")
        except Exception:
            pass
        self._syncing_opengl = True
        try:
            self.opengl_var.set(valid and self.read_force_driver(folder) == 1)
        finally:
            self._syncing_opengl = False

    def toggle_opengl(self):
        if self._syncing_opengl:
            return
        folder = self.folder_var.get().strip()
        if not folder or not os.path.exists(folder):
            self.refresh_opengl_flag()
            self.safe_status("Seleziona una cartella valida")
            return
        want = bool(self.opengl_var.get())
        if self.write_force_driver(folder, want):
            self.safe_status("OpenGL attivato (force_driver=1)" if want
                             else "DirectX ripristinato (force_driver=0)")
        else:
            self.refresh_opengl_flag()
            self.safe_status("settings.json illeggibile, modifica non applicata")

    def safe_status(self, txt):
        self.current_status_base = txt
        if not self.dots_running:
            self.status_label.after(0, lambda: self.status_label.configure(text=txt))

    def animate_dots(self, base_text):
        if not self.dots_running:
            return
        current_base = self.current_status_base or base_text
        current = self.status_label.cget("text")
        dots = current.count(".")
        new_text = current_base + "." * ((dots % 3) + 1)
        self.status_label.configure(text=new_text)
        self.status_label.after(500, lambda: self.animate_dots(base_text))

    def start_download(self, force=False):
        if self.thread and self.thread.is_alive():
            info_styled(self.root, "Info", "Attendere completamento in corso")
            return
        if self.is_game_running():
            continua = ask_styled(
                self.root, "Gioco in esecuzione",
                f"Il gioco risulta in uso ({GAME_EXE} avviato).\n"
                "I file aperti non possono essere sostituiti, quindi "
                "l'aggiornamento non verrà portato a termine.\n\n"
                "Chiudi il gioco e riprova, oppure continua comunque.\n"
                "(Sì = continua, No = annulla)")
            if not continua:
                self.safe_status("Aggiornamento annullato (gioco in esecuzione)")
                return
        self.exclude_list = load_exclude_list()
        self.mac_ignore_list = load_mac_ignore_list()
        self.thread = threading.Thread(target=self.download_and_extract, args=(force,), daemon=True)
        self.thread.start()

    def is_game_running(self):
        if self.test_flag("force_game_running"):
            return True
        if _is_mac_runtime():
            # Mac: il processo vivo e' il runtime `cuo` (lo script `wotsc`
            # fa exec di runtime/osx-*/cuo), quindi pgrep su `cuo`.
            try:
                r = subprocess.run(
                    ["pgrep", "-x", "cuo"],
                    capture_output=True, text=True, timeout=10)
                return r.returncode == 0 and bool(r.stdout.strip())
            except Exception:
                return False
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return False
            return any(f'"{GAME_EXE}"' in line.lower() for line in r.stdout.splitlines())
        except Exception:
            return False

    def check_dotnet10(self):
        if self.test_flag("force_dotnet_missing"):
            return False, ""
        if _is_mac_runtime():
            # Mac: niente ProgramFiles; directory tipiche + list-runtimes.
            for d in ("/usr/local/share/dotnet/shared",
                      "/opt/homebrew/share/dotnet/shared",
                      os.path.expanduser("~/.dotnet/shared")):
                for fx in ("Microsoft.NETCore.App", "Microsoft.AspNetCore.App"):
                    dd = os.path.join(d, fx)
                    try:
                        if os.path.isdir(dd) and any(n.startswith("10.") for n in os.listdir(dd)):
                            return True, f"{fx} 10.x in {dd}"
                    except Exception:
                        continue
        else:
            for env in ("ProgramFiles", "ProgramFiles(x86)"):
                root = os.environ.get(env)
                if not root:
                    continue
                for fx in ("Microsoft.WindowsDesktop.App", "Microsoft.NETCore.App"):
                    d = os.path.join(root, "dotnet", "shared", fx)
                    try:
                        if os.path.isdir(d) and any(n.startswith("10.") for n in os.listdir(d)):
                            return True, f"{fx} 10.x in {d}"
                    except Exception:
                        continue
        try:
            r = subprocess.run(["dotnet", "--list-runtimes"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    low = line.strip().lower()
                    if low.startswith(("microsoft.netcore.app 10.", "microsoft.windowsdesktop.app 10.")):
                        return True, line.strip()
        except Exception:
            pass
        return False, ""

    def get_latest_release(self, force_refresh=False):
        # Cache condivisa da 30 minuti tra Gioca e Aggiorna: prima riusa la
        # cache, solo dopo chiama l'API. Ritorna dict oppure None; il dettaglio
        # resta in self._last_release_error, con _last_release_stale=True se si
        # sta riusando una cache valida dopo un errore fresco.
        # Non solleva: _launch_version_check2 conta sul ritorno None.
        now = time.monotonic()
        with self._release_cache_lock:
            if (not force_refresh and self._release_cache_data is not None
                    and (now - self._release_cache_ts) < RELEASE_CACHE_TTL):
                self._last_release_error = None
                self._last_release_stale = False
                return self._release_cache_data
            if (not force_refresh and now < self._release_next_retry):
                # Backoff: non bruciare quota con retry destinati a fallire.
                if self._release_cache_data is not None:
                    try:
                        age_min = int((now - self._release_cache_ts) // 60)
                    except Exception:
                        age_min = 0
                    wait_s = int(self._release_next_retry - now)
                    raw = self._release_backoff_error or "API non disponibile"
                    self._last_release_error = (
                        f"uso dati di {age_min} min fa: {raw} (riprovo tra {wait_s}s)")
                    self._last_release_stale = True
                    return self._release_cache_data
                return None
            self._last_release_error = None
            self._last_release_stale = False
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
            try:
                r = requests.get(url, timeout=20, headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "WOTSCLauncher",
                    "X-GitHub-Api-Version": "2022-11-28",
                })
            except requests.RequestException as e:
                return self._release_error_or_stale(f"rete: {e}")
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    return self._release_error_or_stale("risposta GitHub non valida (non JSON)")
                if not isinstance(data, dict):
                    return self._release_error_or_stale("risposta GitHub inattesa")
                self._release_cache_data = data
                self._release_cache_ts = time.monotonic()
                self._release_next_retry = 0.0
                self._release_backoff_error = None
                self._save_release_cache(data)
                return data
            body = (r.text or "")[:200]
            if r.status_code == 403 and "rate limit" in body.lower():
                reset = r.headers.get("X-RateLimit-Reset", "")
                when = ""
                if reset.isdigit():
                    try:
                        when = time.strftime("%H:%M:%S", time.localtime(int(reset)))
                    except Exception:
                        when = ""
                suffix = f" (reset ore {when})" if when else ""
                return self._release_error_or_stale(
                    f"limite richieste GitHub esaurito{suffix}: riprova più tardi")
            if r.status_code == 404:
                return self._release_error_or_stale(
                    "release non trovata (repo privato o senza release?)")
            return self._release_error_or_stale(f"GitHub ha risposto {r.status_code}: {body}")

    def _release_error_or_stale(self, message):
        # Se esiste una cache valida, riusala con avviso; altrimenti None.
        # In entrambi i casi arma il backoff per non riprovare subito.
        # Chiamare solo con _release_cache_lock acquisito.
        try:
            self._release_next_retry = time.monotonic() + RELEASE_RETRY_BACKOFF
        except Exception:
            pass
        self._release_backoff_error = message
        if self._release_cache_data is not None:
            try:
                age_min = int((time.monotonic() - self._release_cache_ts) // 60)
            except Exception:
                age_min = 0
            self._last_release_error = f"uso dati di {age_min} min fa: {message}"
            self._last_release_stale = True
            return self._release_cache_data
        self._last_release_error = message
        self._last_release_stale = False
        return None

    def _load_release_cache(self):
        # Riusa tra riavvii l'ultima risposta valida (< TTL). File corrotto,
        # futuro o scaduto -> ignorato, si va in rete come prima.
        try:
            if not os.path.exists(RELEASE_CACHE_FILE):
                return
            import json
            with open(RELEASE_CACHE_FILE, "r", encoding="utf-8") as f:
                obj = json.load(f)
            data = obj.get("data")
            fetched = obj.get("fetched_at")
            if not isinstance(data, dict) or not isinstance(fetched, (int, float)):
                return
            age = time.time() - fetched
            if not (0 <= age < RELEASE_CACHE_TTL):
                return
            self._release_cache_data = data
            self._release_cache_ts = time.monotonic() - age
        except Exception:
            pass

    def _save_release_cache(self, data):
        try:
            import json
            tmp = RELEASE_CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "data": data}, f)
            os.replace(tmp, RELEASE_CACHE_FILE)
        except Exception:
            pass

    def read_last_release(self):
        return open(STATE_FILE).read().strip() if os.path.exists(STATE_FILE) else None

    def read_last_asset(self):
        # Ritorna (name, url) dell'ultimo asset .7z scaricato con successo.
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
                name = cfg.get("Settings", "last_asset_name", fallback="") or None
                url = cfg.get("Settings", "last_asset_url", fallback="") or None
                if name and url:
                    return name, url
        except Exception:
            pass
        return None, None

    def save_last_asset(self, name, url):
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
            if "Settings" not in cfg:
                cfg["Settings"] = {}
            cfg["Settings"]["last_asset_name"] = name or ""
            cfg["Settings"]["last_asset_url"] = url or ""
            with open(CONFIG_FILE, "w") as f:
                cfg.write(f)
        except Exception:
            pass

    def read_last_mac_asset(self):
        # Ritorna (name, url) dell'ultimo overlay Mac (.zip) applicato.
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
                name = cfg.get("Settings", "last_mac_asset_name", fallback="") or None
                url = cfg.get("Settings", "last_mac_asset_url", fallback="") or None
                if name and url:
                    return name, url
        except Exception:
            pass
        return None, None

    def save_last_mac_asset(self, name, url):
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
            if "Settings" not in cfg:
                cfg["Settings"] = {}
            cfg["Settings"]["last_mac_asset_name"] = name or ""
            cfg["Settings"]["last_mac_asset_url"] = url or ""
            with open(CONFIG_FILE, "w") as f:
                cfg.write(f)
        except Exception:
            pass

    def test_flag(self, name):
        # Flag di prova da config.ini [Test]: rilette ogni volta così si
        # possono attivare/disattivare a app aperta, senza ricompilare.
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
                return cfg.getboolean("Test", name, fallback=False)
        except Exception:
            pass
        return False

    def save_last_release(self, tag):
        with open(STATE_FILE,"w") as f: f.write(tag)

    def _find_local_test_overlay(self):
        # Zip di prova locale (per collaudare senza release): priorita' a
        # config.ini [Test] local_overlay (assoluto o relativo a BASE_DIR),
        # poi overlay-test.zip accanto all'app. Ritorna il path o None.
        try:
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
                cand = cfg.get("Test", "local_overlay", fallback="") or ""
                if cand:
                    cand = cand.strip().strip('"').strip("'")
                    if not os.path.isabs(cand):
                        cand = os.path.join(BASE_DIR, cand)
                    if os.path.isfile(cand):
                        return cand
        except Exception:
            pass
        auto = os.path.join(BASE_DIR, "overlay-test.zip")
        return auto if os.path.isfile(auto) else None

    def _resolve_asset(self, force=False):
        # Ritorna (tag, base_name, base_url, mac_name, mac_url, mac_local).
        # mac_local = path di uno zip di prova locale (se presente): la logica
        # di rete (base .7z) gira normalmente, l'overlay viene dal file locale.
        # Con force=True e asset noti in cache riusa gli URL SENZA chiamare
        # l'API (mac_local resta comunque prioritario se il file esiste).
        local = self._find_local_test_overlay()
        if force:
            last_known = self.read_last_release()
            cached_name, cached_url = self.read_last_asset()
            cached_mac_name, cached_mac_url = self.read_last_mac_asset()
            if last_known and cached_name and cached_url and (cached_mac_url or local):
                return (last_known, cached_name, cached_url,
                        cached_mac_name or (os.path.basename(local) if local else None),
                        cached_mac_url, local)

        self.safe_status("Recupero release...")
        latest = self.get_latest_release()
        if not latest:
            detail = getattr(self, "_last_release_error", None)
            self.safe_status(f"Errore nel recupero release{(': ' + detail) if detail else ''}")
            return None, None, None, None, None, local
        if getattr(self, "_last_release_stale", False):
            detail = getattr(self, "_last_release_error", None)
            if detail:
                self.safe_status(detail)

        latest_tag = latest.get("tag_name")
        last_known = self.read_last_release()
        if latest_tag == last_known and not force:
            self.safe_status("Nessuna nuova release")
            return None, None, None, None, None, local

        asset_name = None
        asset_url = None
        mac_name = None
        mac_url = None
        for a in latest.get("assets", []):
            an = a.get("name", "")
            low = an.lower()
            if low.endswith(".7z") and not asset_url:
                asset_name = an
                asset_url = a.get("browser_download_url")
            elif MAC_OVERLAY_HINT in low and low.endswith(".zip") and not mac_url:
                mac_name = an
                mac_url = a.get("browser_download_url")
        if not asset_url:
            self.safe_status("Nessun file .7z trovato")
            return None, None, None, None, None, local
        if local:
            # Prova locale: la base arriva dalla rete, l'overlay dal file.
            self.safe_status(f"Overlay di prova: {os.path.basename(local)}")
            return latest_tag, asset_name, asset_url, os.path.basename(local), "local:test-overlay", local
        if not mac_url:
            self.safe_status("Overlay Mac (.zip) non trovato nella release")
            return None, None, None, None, None, None
        return latest_tag, asset_name, asset_url, mac_name, mac_url, None

    def download_and_extract(self, force=False):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.exists(folder):
            self.safe_status("Seleziona una cartella valida")
            return

        resolved = self._resolve_asset(force)
        latest_tag, asset_name, asset_url, mac_name, mac_url, mac_local = resolved
        if not asset_url or (not mac_url and not mac_local):
            return

        os.makedirs(folder, exist_ok=True)
        tmp_dir = tempfile.mkdtemp()
        tmp_file = os.path.join(tmp_dir, "download.7z")
        tmp_mac_file = os.path.join(tmp_dir, "overlay-mac.zip")
        tmp_extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(tmp_extract_dir, exist_ok=True)

        def _fetch(url, dest, label):
            # Scarica uno dei due stage. Ritorna True se ok.
            self.safe_status(f"Download {label} in corso... 0 MB/s")
            start = time.time()
            try:
                r = requests.get(url, stream=True)
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                last_update = start
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            fh.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last_update > 0.5:
                                speed = downloaded / 1024 / 1024 / (now - start)
                                self.safe_status(f"Download {label} in corso... {speed:.2f} MB/s")
                                if total > 0:
                                    self.progress_bar.set(downloaded / total)
                                last_update = now
                if total != 0 and downloaded != total:
                    self.safe_status(f"Download {label} incompleto!")
                    return False
                return True
            except Exception as e:
                self.safe_status(f"Errore download {label}: {e}")
                return False

        # --- DOWNLOAD (base 7z + overlay Mac) ---
        self.progress_bar.set(0)
        self.dots_running = True
        self.animate_dots("Download in corso")
        if not _fetch(asset_url, tmp_file, "base"):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.dots_running = False
            return
        self.progress_bar.set(0)
        if mac_local:
            # Prova locale: copia lo zip invece di scaricarlo.
            self.safe_status(f"Installo overlay di prova: {os.path.basename(mac_local)}")
            try:
                shutil.copyfile(mac_local, tmp_mac_file)
            except Exception as e:
                self.safe_status(f"Overlay di prova illeggibile: {e}")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self.dots_running = False
                return
        elif not _fetch(mac_url, tmp_mac_file, "overlay Mac"):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.dots_running = False
            return

        # --- ESTRAZIONE base + overlay ---
        self.safe_status("Estrazione base in corso")
        self.animate_dots("Estrazione in corso")
        try:
            with py7zr.SevenZipFile(tmp_file, mode='r') as archive:
                archive.extractall(path=tmp_extract_dir)
        except Exception as e:
            self.dots_running = False
            self.safe_status(f"Errore estrazione base: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return
        tmp_mac_extract = os.path.join(tmp_dir, "extracted-mac")
        os.makedirs(tmp_mac_extract, exist_ok=True)
        self.safe_status("Estrazione overlay Mac in corso")
        try:
            with zipfile.ZipFile(tmp_mac_file, "r") as zf:
                zf.extractall(tmp_mac_extract)
        except Exception as e:
            self.dots_running = False
            self.safe_status(f"Errore estrazione overlay Mac: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        self.dots_running = False
        self.progress_bar.set(0)
        self.safe_status(f"Download ed estrazione completati: {latest_tag}")

        def _merge(src, ignore_extra):
            # Copia/merge con exclude (+ eventuale mac-ignore per la base).
            # L'overlay vince: a parita' di percorso sovrascrive la base.
            # mac-ignore supporta wildcard (*.pdb); le dir di log utente
            # (JournalLogs, Logs) non si visitano mai.
            import fnmatch as _fnm
            ign = {p.lower() for p in (ignore_extra or [])}

            def _ignored(rel_low, base_low):
                for pat in ign:
                    if not pat:
                        continue
                    if "*" in pat or "?" in pat or "[" in pat:
                        if _fnm.fnmatchcase(rel_low, pat) or _fnm.fnmatchcase(base_low, pat):
                            return True
                    elif rel_low == pat or base_low == pat:
                        return True
                return False

            n_ok, n_skip = 0, 0
            for root_dir, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d.lower() not in ("journallogs", "logs")]
                for f in files:
                    full = os.path.join(root_dir, f)
                    rel = os.path.relpath(full, src)
                    dest_full = os.path.join(folder, rel)

                    low = f.lower()
                    if _ignored(rel.lower(), low):
                        n_skip += 1
                        continue
                    if low in self.exclude_list and os.path.exists(dest_full):
                        n_skip += 1
                        continue
                    if os.path.lexists(dest_full) and os.path.isdir(dest_full) \
                            and not os.path.isdir(full):
                        # File contro directory: mai fondere, si tiene dest.
                        n_skip += 1
                        continue

                    os.makedirs(os.path.dirname(dest_full), exist_ok=True)
                    if os.path.exists(dest_full):
                        os.remove(dest_full)
                    shutil.move(full, dest_full)
                    n_ok += 1
            return n_ok, n_skip

        # --- Copia/merge: prima la base (con mac-ignore), poi l'overlay ---
        try:
            mac_ignore = getattr(self, "mac_ignore_list", []) or []
            # La base .7z ha un wrapper singolo (`WOTSC client/`): si appiattisce
            # alla root di gioco, altrimenti base e overlay finirebbero sfalsati.
            base_src = tmp_extract_dir
            try:
                if _HAS_MAC_CORE:
                    base_src = _core_strip_wrapper(tmp_extract_dir)
                else:
                    _entries = [e for e in os.listdir(tmp_extract_dir)
                                if not e.startswith(".")]
                    if len(_entries) == 1 and os.path.isdir(
                            os.path.join(tmp_extract_dir, _entries[0])):
                        base_src = os.path.join(tmp_extract_dir, _entries[0])
            except Exception:
                base_src = tmp_extract_dir
            _merge(base_src, set(mac_ignore))
            _merge(tmp_mac_extract, set())

            # Il binario Mac deve essere eseguibile.
            for root_dir, _dirs, files in os.walk(folder):
                for f in files:
                    if f == GAME_EXE:
                        fp = os.path.join(root_dir, f)
                        try:
                            if _HAS_MAC_CORE:
                                _core_ensure_executable(fp)
                            elif os.name != "nt":
                                st = os.stat(fp)
                                os.chmod(fp, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        except Exception:
                            pass

            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        self.save_last_release(latest_tag)
        self.save_last_asset(asset_name, asset_url)
        self.save_last_mac_asset(mac_name, mac_url)

    def launch_wotsc(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.exists(folder):
            info_styled(self.root, "Errore", "Cartella di destinazione non valida")
            return

        if self.thread and self.thread.is_alive():
            vai = ask_styled(
                self.root, "Aggiornamento in corso",
                "Un aggiornamento è attualmente in corso.\n"
                "Avviare il gioco adesso potrebbe interferire con "
                "il completamento dell'update.\n\n"
                "Vuoi attendere e avviare lo stesso?\n"
                "(Sì = avvia comunque, No = aspetta)")
            if not vai:
                self.safe_status("Avvio annullato (aggiornamento in corso)")
                return

        # Controllo versione in background per non bloccare la GUI
        self.safe_status("Verifica versione...")
        threading.Thread(target=self._launch_version_check, args=(folder,), daemon=True).start()

    def _launch_version_check(self, folder):
        ok, _detail = self.check_dotnet10()
        if not ok:
            self.root.after(0, lambda: self._ask_dotnet(folder))
            return
        self._launch_version_check2(folder)

    def _ask_dotnet(self, folder):
        apri = ask_styled(
            self.root, "Serve .NET 10",
            ".NET 10 non trovato su questo Mac.\n"
            f"Serve per avviare {GAME_EXE}.\n\n"
            "Vuoi aprire la pagina di download Microsoft?\n"
            "(Sì = apri download e annulla avvio, No = avvia comunque)")
        if apri:
            try:
                webbrowser.open(DOTNET_DOWNLOAD_URL)
            except Exception as e:
                info_styled(self.root, "Errore", f"Impossibile aprire il browser: {e}")
            self.safe_status("Aprire pagina download .NET 10...")
        else:
            threading.Thread(target=self._launch_version_check2, args=(folder,), daemon=True).start()

    def _launch_version_check2(self, folder):
        try:
            latest = self.get_latest_release()
        except Exception:
            latest = None
        if not latest:
            self.root.after(0, lambda: self._do_launch(folder, note="Verifica versione fallita, avvio comunque"))
            return

        latest_tag = latest.get("tag_name")
        last_known = self.read_last_release()
        outdated = self.test_flag("force_outdated") or (latest_tag and latest_tag != last_known)
        if outdated:
            self.root.after(0, lambda: self._ask_outdated(folder, latest_tag, last_known))
        else:
            self.root.after(0, lambda: self._do_launch(folder))

    def _ask_outdated(self, folder, latest_tag, last_known):
        known_txt = last_known if last_known else "sconosciuta"
        aggiorna = ask_styled(
            self.root, "Client non aggiornato",
            f"Il client risulta alla versione {known_txt}, ma è disponibile {latest_tag}.\n\n"
            "Vuoi aggiornare ora?\n(Sì = aggiorna, No = gioca comunque)")
        if aggiorna:
            self.start_download()
        else:
            self._do_launch(folder)

    def _do_launch(self, folder, note=None):
        if note:
            self.safe_status(note)

        wotsc_path = self.wotsc_path_cached
        if not wotsc_path or not os.path.exists(wotsc_path):
            for root_dir, dirs, files in os.walk(folder):
                for f in files:
                    if f.lower() == GAME_EXE:
                        wotsc_path = os.path.join(root_dir, f)
                        self.wotsc_path_cached = wotsc_path
                        cfg = configparser.ConfigParser()
                        if os.path.exists(CONFIG_FILE):
                            cfg.read(CONFIG_FILE)
                        if "Settings" not in cfg:
                            cfg["Settings"] = {}
                        cfg["Settings"]["wotsc_path"] = wotsc_path
                        with open(CONFIG_FILE,"w") as fcfg:
                            cfg.write(fcfg)
                        break
                if wotsc_path:
                    break

        if not wotsc_path or not os.path.exists(wotsc_path):
            scarica = ask_styled(
                self.root, "Client non trovato",
                "Il client non risulta installato in questa cartella.\n\n"
                "Vuoi scaricarlo ora?\n(Sì = scarica, No = annulla)")
            if scarica:
                self.start_download()
            else:
                self.safe_status("Download annullato (client non trovato)")
            return

        try:
            subprocess.Popen([wotsc_path], cwd=os.path.dirname(wotsc_path))
            self.safe_status("WOTSC lanciato")
        except Exception as e:
            info_styled(self.root, "Errore", f"Impossibile lanciare WOTSC: {e}")

def _report_startup_crash(err_text):
    # L'app e' windowed: senza questo, un'eccezione all'avvio chiude l'icona
    # in un secondo senza lasciare traccia visibile. Si scrive sempre un log
    # accanto all'app e si mostra un dialogo (Tk, fallback osascript).
    import datetime
    log_path = os.path.join(BASE_DIR, "launcher-crash.log")
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{err_text}\n")
    except Exception:
        pass
    short = err_text[-900:] if len(err_text) > 900 else err_text
    try:
        import tkinter.messagebox as _mb
        _r = ctk.CTk()
        _r.withdraw()
        _mb.showerror("WOTSC Launcher (Mac)",
                      f"Avvio fallito. Invia questo file a chi sviluppa:\n{log_path}\n\n{short}")
        try:
            _r.destroy()
        except Exception:
            pass
    except Exception:
        try:
            msg = ("WOTSC Launcher: avvio fallito. Vedi " + log_path).replace('"', "'")
            subprocess.run(["/usr/bin/osascript", "-e",
                            f'display alert "WOTSC Launcher" message "{msg}"'],
                           timeout=15)
        except Exception:
            pass
    return log_path

if __name__ == "__main__":
    import traceback
    try:
        root = ctk.CTk()
        app = WOTSCDownloader(root)
        root.mainloop()
    except Exception:
        _report_startup_crash(traceback.format_exc())
        raise SystemExit(1)
