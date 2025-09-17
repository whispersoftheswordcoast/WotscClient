import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import requests
import os
import tempfile
import configparser
import py7zr
import shutil
import subprocess
import sys
import time

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
BACKGROUND_IMAGE = os.path.join(MEI_DIR, "background.jpg")  # 600x420 consigliati

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
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = root
        self.root.geometry("600x420")
        self.root.title("WOTSC Downloader")
        self.root.resizable(False, False)

        self.exclude_list = load_exclude_list()
        self.dots_running = False
        self.current_status_base = ""
        self.wotsc_path_cached = None

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
        self.pixel_font = ("Fixedsys", 12)

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
        self.download_btn = ctk.CTkButton(root, text="Controlla e aggiorna",
                                          command=self.start_download,
                                          width=200, height=30, font=self.pixel_font,
                                          fg_color="#8B4513", hover_color="#A0522D", corner_radius=3)
        self.download_btn.place(x=200, y=145)  # più vicino alla barra

        self.launch_btn = ctk.CTkButton(root, text="GIOCA!", command=self.launch_wotsc,
                                        width=100, height=30, font=self.pixel_font,
                                        fg_color="#8B4513", hover_color="#A0522D", corner_radius=3)
        self.launch_btn.place(x=490, y=380)

        self.thread = None
        self.load_folder()

        # --- Aggiungi ombre ai pulsanti ---
        self.root.after(100, lambda: self.add_shadow(self.select_btn))
        self.root.after(100, lambda: self.add_shadow(self.download_btn))
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

    def start_download(self):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("Info", "Attendere completamento in corso")
            return
        self.exclude_list = load_exclude_list()
        self.thread = threading.Thread(target=self.download_and_extract, daemon=True)
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
        self.progress_bar.set(0)
        self.safe_status("Download in corso... 0 MB/s")
        self.dots_running = True
        self.animate_dots("Download in corso")
        start_time = time.time()
        try:
            r = requests.get(asset_url, stream=True)
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            last_update = start_time
            with open(tmp_file, "wb") as fh:
                for chunk in r.iter_content(8192):
                    if chunk:
                        fh.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_update > 0.5:
                            speed = downloaded / 1024 / 1024 / (now - start_time)
                            self.safe_status(f"Download in corso... {speed:.2f} MB/s")
                            if total > 0:
                                self.progress_bar.set(downloaded/total)
                            last_update = now
            if total != 0 and downloaded != total:
                self.safe_status("Download incompleto!")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self.dots_running = False
                return
        except Exception as e:
            self.safe_status(f"Errore download: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.dots_running = False
            return

        # --- ESTRAZIONE ---
        self.safe_status("Estrazione in corso")
        self.animate_dots("Estrazione in corso")
        try:
            with py7zr.SevenZipFile(tmp_file, mode='r') as archive:
                archive.extractall(path=tmp_extract_dir)
        except Exception as e:
            self.dots_running = False
            self.safe_status(f"Errore estrazione: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        self.dots_running = False
        self.progress_bar.set(0)
        self.safe_status(f"Download ed estrazione completati: {latest_tag}")

        # --- Copia/merge con exclude ---
        try:
            for root_dir, dirs, files in os.walk(tmp_extract_dir):
                for f in files:
                    full = os.path.join(root_dir, f)
                    rel = os.path.relpath(full, tmp_extract_dir)
                    dest_full = os.path.join(folder, rel)

                    if f.lower() in self.exclude_list and os.path.exists(dest_full):
                        continue

                    os.makedirs(os.path.dirname(dest_full), exist_ok=True)
                    if os.path.exists(dest_full):
                        os.remove(dest_full)
                    shutil.move(full, dest_full)

            shutil.rmtree(tmp_extract_dir, ignore_errors=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        self.save_last_release(latest_tag)

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
            messagebox.showerror("Errore", "Wotsc.exe non trovato")
            return

        try:
            subprocess.Popen([wotsc_path], cwd=os.path.dirname(wotsc_path))
            self.safe_status("WOTSC lanciato")
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile lanciare WOTSC: {e}")

if __name__ == "__main__":
    root = ctk.CTk()
    app = WOTSCDownloader(root)
    root.mainloop()
