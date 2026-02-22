# Cron-Anleitung (Trautermin um 0:00)

Das Skript soll **täglich um 0:00 Uhr** laufen. Es prüft selbst, ob heute der **Stichtag** (Zieldatum minus 6 Monate) ist und führt nur dann die Buchung aus.

## Cron-Eintrag

1. Crontab bearbeiten:
   ```bash
   crontab -e
   ```
2. Zeile eintragen (Pfade anpassen):
   ```cron
   0 0 * * * /pfad/zum/wedding-catcher/.venv/bin/python /pfad/zum/wedding-catcher/book_trautermin.py >> /pfad/zum/wedding-catcher/cron.log 2>&1
   ```

**Wichtig:**

- **Absoluter Pfad** zum Python der Venv verwenden (z. B. `/home/user/wedding-catcher/.venv/bin/python`), damit Cron das richtige Python und die installierten Pakete (Playwright) nutzt.
- **Absoluter Pfad** zum Skript (`book_trautermin.py`), damit das Arbeitsverzeichnis beim Import (z. B. für `config.yaml`) eindeutig ist. Am besten vorher ins Projektverzeichnis wechseln und das Skript mit vollem Pfad aufrufen, z. B.:
  ```cron
  0 0 * * * cd /pfad/zum/wedding-catcher && .venv/bin/python /pfad/zum/wedding-catcher/book_trautermin.py >> cron.log 2>&1
  ```
- Optional: Logging in eine Datei (z. B. `cron.log`) wie oben, oder in der Config `options.log_file` setzen.

## Playwright-Browser

Einmalig nach der Installation ausführen (innerhalb der Venv):

```bash
source /pfad/zum/wedding-catcher/.venv/bin/activate
playwright install chromium
```

Cron läuft oft mit minimaler Umgebung; Chromium muss im gleichen Nutzerkontext installiert sein wie der Cron-Job.

## Stichtag prüfen

Ohne Buchung testen, ob für dein Wunschdatum der richtige Lauf-Tag berechnet wird:

```bash
cd /pfad/zum/wedding-catcher
.venv/bin/python book_trautermin.py --dry-run
```

An einem anderen Tag als dem Stichtag gibt das Skript aus: „Heute ist nicht der Stichtag … Nichts zu tun.“ Am Stichtag (z. B. 19.03. für Wunsch 19.09.): „Dry-run: Stichtag erkannt …“.
