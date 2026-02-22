#!/usr/bin/env python3
"""
Flask-Webserver für Trautermin-Automatisierung.
Passwortgeschütztes Config-Formular, Scheduler für Buchung am Stichtag 00:00.
"""

import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from flask import Flask, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from book_trautermin import (
    BOOKING_URL,
    get_run_date,
    is_today_run_date,
    load_config,
    run_booking,
    setup_logging,
)

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

CONFIG_EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.yaml"
ROOMS_ORDER = ["Rentkammer", "Weißer Saal", "Rathaus Spanischer Bau", "Rathaus Porz"]


def get_config_path() -> Path:
    """Config-Pfad (Env CONFIG_PATH oder data/config.yaml im Projektverzeichnis)."""
    p = os.environ.get("CONFIG_PATH")
    if p:
        return Path(p)
    # Standard: data/config.yaml im Projektverzeichnis (persistent, wie bei Docker)
    base = Path(__file__).resolve().parent
    return base / "data" / "config.yaml"


def ensure_config_exists() -> Path:
    """Erstellt config.yaml aus config.example.yaml falls nicht vorhanden."""
    path = get_config_path()
    if not path.is_file() and CONFIG_EXAMPLE_PATH.is_file():
        import shutil

        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(CONFIG_EXAMPLE_PATH, path)
        logging.info("Config aus config.example.yaml erstellt: %s", path)
    return path


def get_admin_password_hash() -> Optional[str]:
    """Liefert den Passwort-Hash aus ADMIN_PASSWORD (plain oder bereits Hash)."""
    pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not pw:
        return None
    if pw.startswith(("pbkdf2:", "scrypt:")):
        return pw
    return generate_password_hash(pw, method="pbkdf2:sha256")


def verify_password(password: str) -> bool:
    """Prüft Passwort gegen ADMIN_PASSWORD (plain oder Hash)."""
    stored = get_admin_password_hash()
    if not stored:
        return False
    if stored.startswith(("pbkdf2:", "scrypt:")):
        return check_password_hash(stored, password)
    return password == os.environ.get("ADMIN_PASSWORD", "")


def login_required(f):
    """Decorator für passwortgeschützte Routen."""

    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapped


def form_to_config(request_form) -> dict:
    """Baut Config-Dict aus Formulardaten."""
    opts = request_form
    room_priority = [r for r in ROOMS_ORDER if opts.get(f"room_{r}") == "on"]

    return {
        "desired_date": opts.get("desired_date", "").strip(),
        "desired_time": opts.get("desired_time", "").strip(),
        "room_priority": room_priority if room_priority else ROOMS_ORDER.copy(),
        "person1": {
            "anrede": opts.get("person1_anrede", "Herr"),
            "vorname": opts.get("person1_vorname", "").strip(),
            "nachname": opts.get("person1_nachname", "").strip(),
            "geburtsdatum": opts.get("person1_geburtsdatum", "").strip(),
            "staatsangehoerigkeit": opts.get("person1_staatsangehoerigkeit", "").strip(),
            "wohnsitz_koeln": opts.get("person1_wohnsitz_koeln", "Ja"),
        },
        "person2": {
            "anrede": opts.get("person2_anrede", "Frau"),
            "vorname": opts.get("person2_vorname", "").strip(),
            "nachname": opts.get("person2_nachname", "").strip(),
            "geburtsdatum": opts.get("person2_geburtsdatum", "").strip(),
            "staatsangehoerigkeit": opts.get("person2_staatsangehoerigkeit", "").strip(),
            "wohnsitz_koeln": opts.get("person2_wohnsitz_koeln", "Ja"),
        },
        "contact": {
            "email": opts.get("contact_email", "").strip(),
            "telefon": opts.get("contact_telefon", "").strip(),
        },
        "options": {
            "allow_after_stichtag": opts.get("options_allow_after_stichtag") == "on",
            "browser_headed": False,  # Im Server-Betrieb immer headless
            "screenshot_on_success": True,
            "screenshot_on_error": True,
            "screenshot_dir": "screenshots",
            "log_file": "",
            "notify": {
                "enabled": False,
                "smtp_host": "",
                "smtp_port": 587,
                "smtp_user": "",
                "smtp_password": "",
                "from_email": "",
                "to_email": "",
            },
        },
    }


def save_config(config: dict) -> None:
    """Speichert Config als YAML."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_runs_path() -> Path:
    """Pfad zur runs.json (neben config)."""
    base = get_config_path().parent
    return base / "runs.json"


def get_screenshot_dir() -> Path:
    """Pfad zum Screenshot-Verzeichnis (relativ zum Projektroot)."""
    base = Path(__file__).resolve().parent
    try:
        config = load_config(get_config_path())
        dir_name = (config.get("options") or {}).get("screenshot_dir") or "screenshots"
    except FileNotFoundError:
        dir_name = "screenshots"
    return base / dir_name


def _augment_run_screenshots(run: Dict[str, Any]) -> None:
    """Ergänzt alte Runs (nur 'screenshot') um alle Screenshots aus derselben Session."""
    if "screenshots" in run and run["screenshots"]:
        return
    single = run.get("screenshot")
    if not single:
        run["screenshots"] = []
        return
    try:
        ts_str = run.get("timestamp", "")
        if not ts_str:
            run["screenshots"] = [single]
            return
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        date_part = dt.strftime("%Y%m%d")
        run_seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
        # Screenshots von 30 min vorher bis 1 min nachher
        time_min, time_max = run_seconds - 30 * 60, run_seconds + 60
        screenshot_dir = get_screenshot_dir()
        if not screenshot_dir.is_dir():
            run["screenshots"] = [single]
            return
        candidates: list[tuple[int, str]] = []
        for f in screenshot_dir.glob("*.png"):
            name = f.name
            # Format: prefix_YYYYMMDD_HHMMSS.png
            parts = name.rsplit("_", 2)
            if len(parts) < 3:
                continue
            try:
                file_date = parts[1]
                time_str = parts[2].replace(".png", "")
                if file_date != date_part:
                    continue
                h, m, s = int(time_str[:2]), int(time_str[2:4]), int(time_str[4:6]) if len(time_str) >= 6 else 0
                file_seconds = h * 3600 + m * 60 + s
                if time_min <= file_seconds <= time_max:
                    candidates.append((file_seconds, name))
            except (ValueError, IndexError):
                continue
        candidates.sort(key=lambda x: x[0])
        run["screenshots"] = [c[1] for c in candidates] if candidates else [single]
    except (ValueError, KeyError):
        run["screenshots"] = [single]


def load_runs() -> List[Dict[str, Any]]:
    """Lädt Run-History aus runs.json."""
    path = get_runs_path()
    if not path.is_file():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        runs = data.get("runs", [])
        for run in runs:
            _augment_run_screenshots(run)
        return runs
    except (json.JSONDecodeError, IOError):
        return []


def save_run(
    timestamp: str,
    success: bool,
    attempts: int,
    log_output: str,
    screenshot_paths: Optional[List[str]] = None,
) -> None:
    """Fügt einen Run zur History hinzu."""
    path = get_runs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = load_runs()
    # Nur Dateinamen speichern für URL-Generierung
    screenshot_filenames = [
        Path(p).name for p in (screenshot_paths or []) if p
    ]
    runs.insert(0, {
        "timestamp": timestamp,
        "success": success,
        "attempts": attempts,
        "log": log_output,
        "screenshots": screenshot_filenames,
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"runs": runs[:100]}, f, indent=2, ensure_ascii=False)


@app.route("/")
def index():
    if session.get("logged_in"):
        return redirect(url_for("config_form"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if verify_password(password):
            session["logged_in"] = True
            return redirect(url_for("config_form"))
        return render_template("login.html", error="Falsches Passwort")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/config", methods=["GET", "POST"])
@login_required
def config_form():
    ensure_config_exists()
    if request.method == "POST":
        config = form_to_config(request.form)
        save_config(config)
        return redirect(url_for("config_form"))

    try:
        config = load_config(get_config_path())
    except FileNotFoundError:
        config = {}
    return render_template("config.html", config=config, rooms=ROOMS_ORDER)


@app.route("/screenshots/<path:filename>")
@login_required
def serve_screenshot(filename: str):
    """Served Screenshots für die Run-History."""
    return send_from_directory(get_screenshot_dir(), filename)


@app.route("/runs")
@login_required
def runs_history():
    """Zeigt Run-History mit Versuchen und Logs."""
    runs = load_runs()
    return render_template("runs.html", runs=runs)


@app.route("/runs/trigger", methods=["POST"])
@login_required
def trigger_run():
    """Startet manuell einen Buchungsversuch (zum Testen)."""
    ensure_config_exists()
    try:
        config = load_config(get_config_path())
    except FileNotFoundError:
        return redirect(url_for("runs_history"))

    config.setdefault("options", {})["allow_after_stichtag"] = True
    setup_logging(config)

    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(handler)

    try:
        exit_code, attempts, screenshot_paths = run_booking(config=config)
        success = exit_code == 0
    finally:
        logging.getLogger().removeHandler(handler)
        log_output = log_buffer.getvalue()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_run(
        timestamp=timestamp,
        success=success,
        attempts=attempts,
        log_output=log_output,
        screenshot_paths=screenshot_paths,
    )
    return redirect(url_for("runs_history"))


def scheduled_booking_job():
    """Wird vom Scheduler um 00:00 am Stichtag aufgerufen."""
    ensure_config_exists()
    try:
        config = load_config(get_config_path())
    except FileNotFoundError:
        logging.warning("config.yaml nicht gefunden, Buchung übersprungen")
        return

    desired_date_str = (config.get("desired_date") or "").strip()
    if not desired_date_str:
        logging.warning("desired_date fehlt in Config, Buchung übersprungen")
        return

    if not is_today_run_date(desired_date_str):
        return

    now = datetime.now()
    if now.hour != 0 or now.minute > 1:
        return

    setup_logging(config)
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(handler)

    try:
        log = logging.getLogger(__name__)
        log.info("Scheduler: Stichtag für %s – Starte Buchung.", desired_date_str)
        exit_code, attempts, screenshot_paths = run_booking(config=config)
        success = exit_code == 0
    finally:
        logging.getLogger().removeHandler(handler)
        log_output = log_buffer.getvalue()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_run(
        timestamp=timestamp,
        success=success,
        attempts=attempts,
        log_output=log_output,
        screenshot_paths=screenshot_paths,
    )


def main():
    if not os.environ.get("ADMIN_PASSWORD"):
        logging.error("ADMIN_PASSWORD Umgebungsvariable fehlen. Server startet nicht.")
        raise SystemExit(1)
    if not os.environ.get("FLASK_SECRET_KEY"):
        logging.warning("FLASK_SECRET_KEY nicht gesetzt – Session-Cookies unsicher!")

    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

    ensure_config_exists()

    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_booking_job, "interval", minutes=1)
    scheduler.start()

    logging.info("Scheduler gestartet (prüft jede Minute auf Stichtag 00:00)")

    port = int(os.environ.get("PORT", "4000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
