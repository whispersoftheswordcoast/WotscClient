import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import requests
import os
import tempfile
import configparser
import py7zr
import shutil
import subprocess
from PIL import Image, ImageTk
import sys

# --- Config paths robusti per PyInstaller ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    MEI_DIR = getattr(sys, '_MEIPASS', BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    MEI_DIR = BASE_DIR

REPO_OWNER = "whispersoftheswordcoast"
REPO_NAME = "WotscClient"
STATE_FILE = os.path.join(BASE_DIR, "last_release.txt")
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")
EXCLUDE_INI = os.path.join(BASE_DIR, "exclude.ini")
BACKGROUND_IMAGE = os.path.join(MEI_DIR, "background.jpg")  # ora legge da _MEIPASS in onefile

def load_exclude_list():
    cfg = configparser.ConfigParser()
    if not os.path.exists(EXCLUDE_INI):
        return []
    cfg.read(EXCLUDE_INI)
    if cfg.has_option("Exclude", "files"):
        raw = cfg.get("Exclude", "files")
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return []

class WOTSCDownloader:
    def __init__(self, root):
        self.root = root
        self.root.geometry("600x420")
        self.root.title("WOTSC Downloader")
        self.exclude_list = load_exclude_list()
        self.dots_running = False
        self.wotsc_path_cached = None

        # --- Background ---
        try:
            if os.path.exists(BACKGROUND_IMAGE):
                bg = Image.open(BACKGROUND_IMAGE).resize((600,400)).convert("RGB")
                self.bg_photo = ImageTk.PhotoImage(bg)
                tk.Label(root, image=self.bg_photo).place(x=0,y=0,relwidth=1,relheight=1)
        except Exception as e:
            print("Errore caricamento background:", e)

        # --- Font ---
        self.font = ("Times New Roman", 12)

        # --- Cartella ---
        tk.Label(root, text="Cartella di destinazione:", fg="#2e1f0f", font=self.font).place(x=120,y=20)
        self.folder_var = tk.StringVar()
        self.entry_folder = tk.Entry(root, textvariable=self.folder_var, width=40, bd=2, relief="groove",
                                     bg="#fff8dc", fg="#2e1f0f", font=self.font)
        self.entry_folder.place(x=120,y=40)
        tk.Button(root, text="Sfoglia", bg="#f4d03f", fg="#2e1f0f", font=self.font,
                  command=self.select_folder).place(x=400,y=38)

        # --- Status ---
        self.status_label = tk.Label(root, text="Pronto", fg="#2e1f0f", font=self.font)
        self.status_label.place(x=120,y=80)

        # --- Progress bar ---
        style = ttk.Style()
        style.theme_use('default')
        style.configure("download.Horizontal.TProgressbar", troughcolor='#f5f5f5', background='#f4d03f', thickness=14)
        style.configure("unzip.Horizontal.TProgressbar", troughcolor='#f5f5f5', background='#9b9b9b', thickness=10)

        self.progress_download = ttk.Progressbar(root, length=420, style="download.Horizontal.TProgressbar")
        self.progress_download.place(x=90, y=110)
        self.progress_unzip = ttk.Progressbar(root, length=420, style="unzip.Horizontal.TProgressbar")
        self.progress_unzip.place(x=90, y=140)

        # --- Bottone download/aggiorna ---
        self.download_btn = tk.Button(root, text="Controlla e aggiorna", bg="#f4d03f", fg="#2e1f0f",
                                      font=(self.font[0],12,"bold"), command=self.start_download)
        self.download_btn.place(x=200, y=180)
        self.download_btn.bind("<Enter>", lambda e:self.download_btn.config(bg="#ffe066"))
        self.download_btn.bind("<Leave>", lambda e:self.download_btn.config(bg="#f4d03f"))

        # --- Bottone lancia WOTSC ---
        self.launch_btn = tk.Button(root, text="GIOCA!", bg="#f4d03f", fg="#2e1f0f",
                                    font=(self.font[0],12,"bold"), command=self.launch_wotsc)
        self.launch_btn.place(x=490, y=380, width=100, height=30)
        self.launch_btn.bind("<Enter>", lambda e:self.launch_btn.config(bg="#ffe066"))
        self.launch_btn.bind("<Leave>", lambda e:self.launch_btn.config(bg="#f4d03f"))

        self.thread = None
        self.load_folder()

    def load_folder(self):
        cfg = configparser.ConfigParser()
        if os.path.exists(CONFIG_FILE):
            cfg.read(CONFIG_FILE)
            path = cfg.get("Settings","extract_folder",fallback="")
            if os.path.exists(path): self.folder_var.set(path)
            self.wotsc_path_cached = cfg.get("Settings","wotsc_path",fallback=None)

    def select_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_var.set(path)
            cfg = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                cfg.read(CONFIG_FILE)
            cfg["Settings"] = {"extract_folder": path}
            with open(CONFIG_FILE,"w") as f: cfg.write(f)

    def safe_status(self, txt):
        self.status_label.after(0, lambda: self.status_label.config(text=txt))

    def animate_dots(self, base_text):
        if not self.dots_running:
            return
        current = self.status_label.cget("text")
        dots = current.count(".")
        new_text = base_text + "." * ((dots % 3) + 1)
        self.status_label.config(text=new_text)
        self.status_label.after(500, lambda: self.animate_dots(base_text))

    def start_download(self):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("Info", "Attendere completamento in corso")
            return
        self.exclude_list = load_exclude_list()
        self.thread = threading.Thread(target=self.download_and_extract)
        self.thread.start()

    def get_latest_release(self):
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
        r = requests.get(url)
        return r.json() if r.status_code==200 else None

    def read_last_release(self):
        return open(STATE_FILE).read().strip() if os.path.exists(STATE_FILE) else None

    def save_last_release(self, tag):
        with open(STATE_FILE,"w") as f: f.write(tag)

    def download_and_extract(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.exists(folder):
            self.safe_status("Seleziona una cartella valida")
            return

        self.safe_status("Recupero release...")
        latest = self.get_latest_release()
        if not latest:
            self.safe_status("Errore nel recupero release")
            return

        latest_tag = latest.get("tag_name")
        last_known = self.read_last_release()
        if latest_tag == last_known:
            self.safe_status("Nessuna nuova release")
            return

        asset_url = None
        for a in latest.get("assets", []):
            if a.get("name","").lower().endswith(".7z"):
                asset_url = a.get("browser_download_url")
                break
        if not asset_url:
            self.safe_status("Nessun file .7z trovato")
            return

        os.makedirs(folder, exist_ok=True)
        tmp_dir = tempfile.mkdtemp()
        tmp_file = os.path.join(tmp_dir, "download.7z")
        tmp_extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(tmp_extract_dir, exist_ok=True)

        # --- DOWNLOAD ---
        self.safe_status("Download in corso...")
        try:
            r = requests.get(asset_url, stream=True)
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_file, "wb") as fh:
                for chunk in r.iter_content(8192):
                    if chunk:
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            p = int(downloaded * 100 / total)
                            self.progress_download.after(0, lambda x=p: self.progress_download.config(value=x))
            if total != 0 and downloaded != total:
                self.safe_status("Download incompleto!")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return
        except Exception as e:
            self.safe_status(f"Errore download: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        # --- ESTRAZIONE ---
        self.dots_running = True
        self.animate_dots("Estrazione archivio")
        try:
            with py7zr.SevenZipFile(tmp_file, mode='r') as archive:
                archive.extractall(path=tmp_extract_dir)
        except Exception as e:
            self.dots_running = False
            self.safe_status(f"Errore estrazione archivio: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return
        self.dots_running = False
        self.safe_status("Estrazione completata")

        # --- SPOSTAMENTO / MERGE ---
        self.safe_status("Spostamento file nella cartella di destinazione...")
        files_to_move = []
        for root_dir, dirs, files in os.walk(tmp_extract_dir):
            for f in files:
                full = os.path.join(root_dir, f)
                rel = os.path.relpath(full, tmp_extract_dir)
                files_to_move.append((full, rel))

        total_files = len(files_to_move) if files_to_move else 1
        moved = 0
        for src_full, rel_path in files_to_move:
            dest_full = os.path.join(folder, rel_path)
            dest_dir = os.path.dirname(dest_full)
            basename = os.path.basename(rel_path).lower()

            should_exclude = False
            for ex in self.exclude_list:
                ex = ex.strip().lower()
                if basename == ex or rel_path.lower().endswith(ex):
                    if os.path.exists(dest_full):
                        should_exclude = True
                        break

            if should_exclude:
                moved += 1
                p = int(moved * 100 / total_files)
                self.progress_unzip.after(0, lambda x=p: self.progress_unzip.config(value=x))
                continue

            os.makedirs(dest_dir, exist_ok=True)
            try:
                if os.path.exists(dest_full):
                    os.remove(dest_full)
            except Exception:
                pass

            try:
                shutil.move(src_full, dest_full)
            except Exception:
                try:
                    shutil.copy2(src_full, dest_full)
                    os.remove(src_full)
                except Exception:
                    pass

            moved += 1
            p = int(moved * 100 / total_files)
            self.progress_unzip.after(0, lambda x=p: self.progress_unzip.config(value=x))

        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

        self.progress_download.after(0, lambda: self.progress_download.config(value=0))
        self.progress_unzip.after(0, lambda: self.progress_unzip.config(value=0))
        self.save_last_release(latest_tag)
        self.safe_status(f"Download ed estrazione completati: {latest_tag}")

    # --- Lancia Wotsc.exe e salva path ---
    def launch_wotsc(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.exists(folder):
            messagebox.showerror("Errore", "Cartella di destinazione non valida")
            return

        wotsc_path = self.wotsc_path_cached
        if not wotsc_path or not os.path.exists(wotsc_path):
            for root_dir, dirs, files in os.walk(folder):
                for f in files:
                    if f.lower() == "wotsc.exe":
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
            messagebox.showerror("Errore", "Wotsc.exe non trovato nella cartella di destinazione")
            return

        try:
            subprocess.Popen([wotsc_path], cwd=os.path.dirname(wotsc_path))
            self.safe_status("WOTSC lanciato")
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile lanciare WOTSC: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = WOTSCDownloader(root)
    root.mainloop()
