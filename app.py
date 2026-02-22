#!/usr/bin/env python3
"""
Flask-Webserver für Trautermin-Automatisierung.
Passwortgeschütztes Config-Formular, Scheduler für Buchung am Stichtag 00:00.
"""

import logging
import os
from datetime import date, datetime
from pathlib import Path

import yaml
from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from book_trautermin import (
    BOOKING_URL,
    DEFAULT_CONFIG_PATH,
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


def ensure_config_exists() -> Path:
    """Erstellt config.yaml aus config.example.yaml falls nicht vorhanden."""
    if not DEFAULT_CONFIG_PATH.is_file() and CONFIG_EXAMPLE_PATH.is_file():
        import shutil

        shutil.copy(CONFIG_EXAMPLE_PATH, DEFAULT_CONFIG_PATH)
        logging.info("config.yaml aus config.example.yaml erstellt")
    return DEFAULT_CONFIG_PATH


def get_admin_password_hash() -> str | None:
    """Liefert den Passwort-Hash aus ADMIN_PASSWORD (plain oder bereits Hash)."""
    pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not pw:
        return None
    if pw.startswith(("pbkdf2:", "scrypt:")):
        return pw
    return generate_password_hash(pw)


def verify_password(password: str) -> bool:
    """Prüft Passwort gegen ADMIN_PASSWORD."""
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
            "screenshot_on_success": opts.get("options_screenshot_on_success") == "on",
            "screenshot_on_error": opts.get("options_screenshot_on_error") == "on",
            "screenshot_dir": opts.get("options_screenshot_dir", "screenshots").strip() or "screenshots",
            "log_file": opts.get("options_log_file", "").strip(),
            "notify": {
                "enabled": opts.get("notify_enabled") == "on",
                "smtp_host": opts.get("notify_smtp_host", "").strip(),
                "smtp_port": int(opts.get("notify_smtp_port") or "587"),
                "smtp_user": opts.get("notify_smtp_user", "").strip(),
                "smtp_password": opts.get("notify_smtp_password", "").strip(),
                "from_email": opts.get("notify_from_email", "").strip(),
                "to_email": opts.get("notify_to_email", "").strip(),
            },
        },
    }


def save_config(config: dict) -> None:
    """Speichert Config als YAML."""
    with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


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
        config = load_config(DEFAULT_CONFIG_PATH)
    except FileNotFoundError:
        config = {}
    return render_template("config.html", config=config, rooms=ROOMS_ORDER)


def scheduled_booking_job():
    """Wird vom Scheduler um 00:00 am Stichtag aufgerufen."""
    ensure_config_exists()
    try:
        config = load_config(DEFAULT_CONFIG_PATH)
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
    log = logging.getLogger(__name__)
    log.info("Scheduler: Stichtag für %s – Starte Buchung.", desired_date_str)
    run_booking(config=config)


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

    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
