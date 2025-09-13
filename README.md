Repository per la pubblicazione e gestione del client di gioco di Whispers of the Sword Coast, shard di ultimna online basato su regole miste dnd 3.5/pathfinder.

ISTRUZIONI PER CREARE LO STANDALONE WOTSC LAUNCHER

1. Assicurati di avere Python 3.10+ installato.
2. Installa le dipendenze phyton:
   pip install pyinstaller
   pip install requests
   pip install pillow
   pip install py7zr 
   
4. Posiziona tutti i file del progetto nella stessa cartella:
   - InstallerWotsc.py
   - background.jpg
   - exclude.ini (opzionale)
   - icona.ico (opzionale)
5. Apri il terminale / cmd nella cartella del progetto.
6. Esegui il comando per creare lo standalone (EXE):
   pyinstaller --onefile --windowed --icon="icona.ico" --add-data "background.jpg;." InstallerWotsc.py
   (per minor problemi di falsi positivi, onedir potrebbe essere piú indicato)
8. Troverai l’EXE nella cartella `dist`.
9. Copia l’EXE e il background nella stessa cartella se vuoi che funzioni senza PyInstaller.
10. Config.ini verrà creato automaticamente nella stessa cartella dell’EXE.
11. L’exclude.ini può essere fornito dall’utente e modificato senza ricompilare l’EXE.
