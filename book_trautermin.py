#!/usr/bin/env python3
"""
Trautermin-Automatisierung Stadt Köln.
Läuft am Stichtag (Zieldatum minus 6 Monate) um 0:00 und führt die Buchung durch.
"""

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import yaml

# Stichtag: Termine für einen Tag werden um 0:00 am Tag (Zieldatum - 6 Monate) freigeschaltet.
# Beispiel: Wunsch 19.09.2026 → am 19.03.2026 um 0:00 buchen.
BOOKING_URL = (
    "https://termine.stadt-koeln.de/m/standesamt/extern/calendar/"
    "?uid=2eff05cf-a96f-484d-9f8c-c847b5a23bac"
)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path: Path) -> dict:
    """Lädt YAML-Config. Wirft bei Fehlern."""
    if not path.is_file():
        raise FileNotFoundError(f"Config nicht gefunden: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def add_months(d: date, months: int) -> date:
    """Addiert calendar months (nicht 4-Wochen)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    # letzter Tag im Zielmonat
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    from datetime import timedelta
    last = next_first - timedelta(days=1)
    # Tag beibehalten, aber nicht über Ende des Monats
    day = min(d.day, last.day)
    return date(year, month, day)


def get_run_date(desired_date: date) -> date:
    """Stichtag = genau 6 Monate vor dem Zieldatum (an dem um 0:00 freigeschaltet wird)."""
    return add_months(desired_date, -6)


def is_today_run_date(desired_date_str: str) -> bool:
    """True, wenn heute der Stichtag für desired_date ist."""
    try:
        year, month, day = map(int, desired_date_str.strip().split("-"))
        desired = date(year, month, day)
    except (ValueError, AttributeError):
        return False
    run_date = get_run_date(desired)
    return date.today() == run_date


def setup_logging(config: dict) -> None:
    """Logging nach Config (log_file) und stdout."""
    opts = config.get("options") or {}
    log_file = opts.get("log_file") or ""
    level = logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Trautermin Stadt Köln automatisch buchen")
    parser.add_argument(
        "--config",
        type=Path,
        default=os.environ.get("TRAUTERMIN_CONFIG", DEFAULT_CONFIG_PATH),
        help="Pfad zur config.yaml",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Buchung auch ausführen wenn heute nicht der Stichtag ist (z.B. zum Testen)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur prüfen ob Stichtag, Config laden, kein Browser starten",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    setup_logging(config)
    log = logging.getLogger(__name__)

    desired_date_str = (config.get("desired_date") or "").strip()
    if not desired_date_str:
        log.error("desired_date in Config fehlt")
        return 1

    allow_after_stichtag = (config.get("options") or {}).get("allow_after_stichtag", False)
    should_run = (
        args.force
        or allow_after_stichtag
        or is_today_run_date(desired_date_str)
    )
    if not should_run:
        log.info(
            "Heute ist nicht der Stichtag für %s (Stichtag wäre 6 Monate vorher). Nichts zu tun.",
            desired_date_str,
        )
        return 0

    if args.dry_run:
        log.info("Dry-run: Buchung würde ausgeführt, Config geladen. Kein Browser gestartet.")
        return 0

    exit_code, _, _ = run_booking(config=config, config_path=args.config)
    return exit_code


def run_booking(config: dict, config_path: Path = None):
    """
    Führt die Buchung durch (mit Retries).
    Wird von CLI und Scheduler genutzt.
    Returns (exit_code, attempts_used, screenshot_paths): 0 bei Erfolg, 1 bei Fehlschlag;
    attempts_used 1–4; screenshot_paths ist Liste relativer Pfade (alle Schritte).
    """
    from runner import run_booking_flow

    log = logging.getLogger(__name__)
    desired_date_str = (config.get("desired_date") or "").strip()
    if not desired_date_str:
        log.error("desired_date in Config fehlt")
        return (1, 0, [])

    log.info("Stichtag für %s – Starte Buchungsflow.", desired_date_str)
    max_attempts = 4  # 1 initial + 3 Wiederholungen bei Fehlschlag
    all_screenshots: list[str] = []

    for attempt in range(1, max_attempts + 1):
        log.info("Versuch %d/%d...", attempt, max_attempts)
        success, screenshots = run_booking_flow(config=config, url=BOOKING_URL)
        all_screenshots.extend(screenshots)
        if success:
            log.info("Buchung erfolgreich – Abbruch.")
            return (0, attempt, all_screenshots)
        if attempt < max_attempts:
            log.warning("Versuch %d fehlgeschlagen, erneuter Versuch in 3 Sekunden...", attempt)
            time.sleep(3)
    log.error("Alle %d Versuche fehlgeschlagen.", max_attempts)
    return (1, max_attempts, all_screenshots)


if __name__ == "__main__":
    sys.exit(main())
