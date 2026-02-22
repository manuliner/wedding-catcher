# Trautermin-Automatisierung (Stadt Köln)

Automatische Buchung eines Trautermins auf [termine.stadt-koeln.de](https://termine.stadt-koeln.de). Das Skript läuft am **Stichtag** (siehe unten) um 0:00 Uhr und führt den Buchungsflow durch: Standort (Rentkammer bevorzugt) → Datum/Uhrzeit → Ihre Daten → Bestätigung.

## 6-Monats-Regel

Termine werden **um 0:00 Uhr** für den Tag freigeschaltet, der **genau 6 Kalendermonate** später liegt (First come, first serve).

- **Beispiel:** Wunschtermin **19.09.2026** → Am **19.03.2026 um 0:00 Uhr** werden die Termine für den 19.09.2026 freigeschaltet. Das Skript muss an diesem Tag (19.03.) um 0:00 Uhr laufen.
- **Stichtag** = Zieldatum minus 6 Monate. Nur wenn **heute** der Stichtag ist, führt das Skript die Buchung aus.

## Voraussetzungen

- Python 3.8+
- Linux (für Cron um 0:00)

## Installation

```bash
cd wedding-catcher
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Konfiguration

1. `config.example.yaml` nach `config.yaml` kopieren:
   ```bash
   cp config.example.yaml config.yaml
   ```
2. `config.yaml` mit echten Daten ausfüllen:
   - **desired_date**: Wunschdatum der Trauung (YYYY-MM-DD)
   - **desired_time**: Wunschuhrzeit (HH:MM); wenn nicht verfügbar, wird der erste freie Termin an dem Tag gewählt
   - **room_priority**: Liste der Räume in Reihenfolge (Rentkammer zuerst, dann Fallbacks)
   - **person1** / **person2**: Anrede, Vorname, Nachname, Geburtsdatum (tt.mm.jjjj), Staatsangehörigkeit, Wohnsitz Köln
   - **contact**: E-Mail, optional Telefon

`config.yaml` wird von Git ignoriert und nicht versioniert.

## Nutzung

- **Normal (nur am Stichtag buchen):**  
  Skript von Cron täglich um 0:00 aufrufen. Es bucht nur, wenn heute der Stichtag für `desired_date` ist.

- **Manuell testen (ohne Stichtag-Prüfung):**  
  ```bash
  python book_trautermin.py --force
  ```

- **Dry-Run (nur Config/Stichtag prüfen, kein Browser):**  
  ```bash
  python book_trautermin.py --dry-run
  ```

## Web-UI mit Docker (passwortgeschützt)

Für den Betrieb auf einem Webserver mit passwortgeschütztem Config-Formular und automatischer Buchung am Stichtag 00:00:

1. `.env` anlegen (aus `.env.example`):
   ```bash
   cp .env.example .env
   # ADMIN_PASSWORD und FLASK_SECRET_KEY eintragen
   ```

2. Container starten:
   ```bash
   docker compose up -d
   ```

3. Im Browser `http://localhost:5000` öffnen, mit Passwort anmelden, Config im Formular ausfüllen und speichern.

Die Buchung läuft automatisch am Stichtag um 00:00. Config und Screenshots liegen in `./data/`.

## Cron einrichten

Siehe [scheduler/README.md](scheduler/README.md) für die genaue Cron-Zeile und Hinweise (z. B. absoluter Pfad zum Python der Venv, Arbeitsverzeichnis).

Kurzbeispiel (täglich 0:00):

```cron
0 0 * * * /pfad/zum/wedding-catcher/.venv/bin/python /pfad/zum/wedding-catcher/book_trautermin.py
```

## Ablauf im Skript

1. **Standort:** Ersten verfügbaren Raum aus `room_priority` wählen (z. B. Rentkammer), dann „Weiter“.
2. **Termin:** Zieldatum in der Liste der verfügbaren Tage anklicken, dann Wunschzeit oder erste verfügbare Uhrzeit.
3. **Ihre Daten:** Formular aus der Config ausfüllen, „Termin buchen“ klicken.
4. **Bestätigung:** Erfolg/Fehler auswerten, optional Screenshot und E-Mail-Benachrichtigung.

Bei Fehlern oder Timeout werden Screenshots unter `screenshots/` (oder in `options.screenshot_dir`) gespeichert, wenn in der Config aktiviert.

## Optionale E-Mail-Benachrichtigung

In `config.yaml` unter `options.notify` SMTP-Daten eintragen und `enabled: true` setzen. Nach erfolgreicher Buchung oder bei Fehler wird eine E-Mail gesendet (siehe `config.example.yaml`).
