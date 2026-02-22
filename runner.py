"""
Playwright-Flow für Trautermin-Buchung: Standort → Termin → Ihre Daten → Bestätigung.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 30_000
STEP_TIMEOUT_MS = 15_000


def _screenshot(page, config: dict, prefix: str) -> str | None:
    """Speichert Screenshot. Gibt relativen Pfad zurück oder None bei Fehler/Deaktivierung."""
    opts = config.get("options") or {}
    if not opts.get("screenshot_on_success") and prefix == "success":
        return None
    if not opts.get("screenshot_on_error") and prefix == "error":
        return None
    dir_path = Path(opts.get("screenshot_dir") or "screenshots")
    dir_path.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path = dir_path / name
    try:
        page.screenshot(path=path)
        log.info("Screenshot gespeichert: %s", path)
        return str(path)
    except Exception as e:
        log.warning("Screenshot fehlgeschlagen: %s", e)
        return None


def _notify(config: dict, success: bool, message: str) -> None:
    opts = config.get("options") or {}
    notify_cfg = opts.get("notify") or {}
    if not notify_cfg.get("enabled"):
        return
    try:
        from notify import send_notification
        send_notification(config=config, success=success, message=message)
    except Exception as e:
        log.warning("Benachrichtigung fehlgeschlagen: %s", e)


def _step_standort(page, config: dict) -> bool:
    """Standort wählen (erster aus room_priority der sichtbar ist), dann Weiter."""
    room_priority = config.get("room_priority") or [
        "Rentkammer", "Weißer Saal", "Rathaus Spanischer Bau", "Rathaus Porz"
    ]
    log.info("Standort-Auswahl: Reihenfolge %s", room_priority)
    for room_name in room_priority:
        try:
            # 1. Stadt Köln: data-testid location-container + location-checkbox
            try:
                container = page.locator('[data-testid^="location-container-"]').filter(
                    has_text=room_name
                )
                container.locator('[data-testid^="location-checkbox-"]').check(timeout=5000)
            except Exception:
                # 2. Checkbox per accessible name (Label-Zuordnung)
                try:
                    page.get_by_role("checkbox", name=room_name).check(timeout=5000)
                except Exception:
                    # 3. Label/Text klicken
                    loc = page.get_by_text(room_name, exact=False).first
                    loc.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
                    loc.click()
            log.info("→ Standort gewählt: %s", room_name)
            break
        except PlaywrightTimeout:
            log.info("→ Standort %s nicht verfügbar, versuche nächsten", room_name)
            continue
    else:
        log.error("Kein Standort aus room_priority gefunden: %s", room_priority)
        return False

    # "Weiter" klicken
    try:
        page.get_by_role("button", name="Weiter").click(timeout=STEP_TIMEOUT_MS)
    except Exception:
        page.get_by_text("Weiter", exact=True).first.click(timeout=STEP_TIMEOUT_MS)
    return True


def _step_termin(page, config: dict) -> bool:
    """Zieldatum suchen und klicken, dann Wunschzeit oder erste verfügbare Uhrzeit."""
    desired_date = (config.get("desired_date") or "").strip()
    desired_time = (config.get("desired_time") or "").strip()
    if not desired_date:
        log.error("desired_date fehlt in Config")
        return False

    # Datum im deutschen Format auf der Seite: z.B. "Donnerstag 19.03.2026" oder "19.03.2026"
    try:
        d = datetime.strptime(desired_date, "%Y-%m-%d")
        date_de = d.strftime("%d.%m.%Y")  # 19.09.2026
    except ValueError:
        log.error("desired_date ungültig: %s", desired_date)
        return False

    # Klick auf den Tag (Seite zeigt "Tage mit verfügbaren Terminen", dann Buttons/Links pro Tag)
    log.info("Suche Datum %s...", date_de)
    try:
        # 1. Stadt Köln: button[data-testid^="slot_date_button-"] mit aria-label
        try:
            date_btn = page.locator(
                f'button[data-testid^="slot_date_button-"][aria-label="{date_de}"]'
            )
            date_btn.first.wait_for(state="visible", timeout=5000)
            date_btn.first.click()
        except Exception:
            # 2. Button per aria-label oder Text
            try:
                page.get_by_role("button", name=date_de).click(timeout=5000)
            except Exception:
                date_loc = page.get_by_text(date_de).first
                date_loc.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
                date_loc.click()
        log.info("→ Datum gewählt: %s", date_de)
    except PlaywrightTimeout:
        log.error("Datum %s auf der Seite nicht gefunden (nicht freigeschaltet oder ausgebucht)", date_de)
        return False

    # Warte auf Zeitauswahl (Überschrift z.B. "Termine Donnerstag, 06.08.2026" und Buttons mit Uhrzeiten)
    page.wait_for_timeout(2500)
    # Explizit warten bis mindestens ein Zeit-Slot sichtbar ist
    try:
        page.wait_for_selector("text=/\\d{1,2}:\\d{2}/", state="attached", timeout=10000)
    except PlaywrightTimeout:
        log.warning("Timeout beim Warten auf Zeit-Slots (fahre trotzdem fort)")
    # Wunschzeit klicken (z.B. "12:00") oder ersten verfügbaren
    time_pattern = re.compile(r"^\d{1,2}:\d{2}$")
    time_clicked = False

    # 1. Versuch: Wunschzeit (zuerst Stadt-Köln-Slot-Buttons, dann Role/Text)
    if desired_time:
        log.info("Suche Wunschzeit %s...", desired_time)
        # Stadt Köln: button[data-testid^="slot_button"] mit strong.slot_from_parsed
        try:
            slot_btn = page.locator('button[data-testid^="slot_button"]').filter(
                has_text=re.compile(r"^" + re.escape(desired_time) + r"$")
            )
            slot_btn.first.wait_for(state="visible", timeout=3000)
            slot_btn.first.click()
            log.info("→ Wunschzeit %s verfügbar, gewählt.", desired_time)
            time_clicked = True
        except (PlaywrightTimeout, Exception):
            pass
        if not time_clicked:
            for role in ["link", "button"]:
                try:
                    loc = page.get_by_role(role, name=desired_time)
                    loc.first.wait_for(state="visible", timeout=3000)
                    loc.first.click()
                    log.info("→ Wunschzeit %s verfügbar, gewählt.", desired_time)
                    time_clicked = True
                    break
                except (PlaywrightTimeout, Exception):
                    continue
        if not time_clicked:
            try:
                time_loc = page.get_by_text(desired_time, exact=True).first
                time_loc.wait_for(state="visible", timeout=3000)
                time_loc.click()
                log.info("→ Wunschzeit %s verfügbar, gewählt.", desired_time)
                time_clicked = True
            except (PlaywrightTimeout, Exception):
                log.warning("→ Wunschzeit %s nicht verfügbar, suche früheren Termin.", desired_time)

    # 2. Fallback: Frühesten früheren Termin (vor Wunschzeit) oder ersten verfügbaren
    if not time_clicked:

        def _parse_time(s: str):
            """Parst HH:MM aus String, auch wenn weitere Zeichen folgen (z.B. '10:20' in '10:20 - 10:40')."""
            if not s:
                return None
            match = re.search(r"(\d{1,2}):(\d{2})", s.strip())
            if match:
                try:
                    return (int(match.group(1)), int(match.group(2)))
                except (ValueError, IndexError):
                    pass
            return None

        def _fmt_time(pt: tuple) -> str:
            return f"{pt[0]:02d}:{pt[1]:02d}" if pt else ""

        def _collect_candidates():
            out = []
            # Strategie 0: Stadt Köln – button[data-testid^="slot_button"] (Zeit in strong.slot_from_parsed)
            try:
                locs = page.locator('button[data-testid^="slot_button"]')
                n = locs.count()
                log.info("Strategie slot_button (Stadt Köln): %d Treffer", n)
                seen_times = set()
                for i in range(n):
                    el = locs.nth(i)
                    if el.is_visible():
                        t = (el.inner_text() or "").strip()
                        pt = _parse_time(t)
                        if pt and pt not in seen_times:
                            seen_times.add(pt)
                            out.append((pt, el))
            except Exception as e:
                log.debug("Strategie slot_button Fehler: %s", e)
            # Strategie A: get_by_role(link/button) mit Regex
            for role in ["link", "button"]:
                try:
                    locs = page.get_by_role(role, name=time_pattern)
                    n = locs.count()
                    log.info("Strategie get_by_role(%s): %d Treffer", role, n)
                    for i in range(min(n, 30)):
                        el = locs.nth(i)
                        if el.is_visible():
                            t = (el.inner_text() or "").strip()
                            pt = _parse_time(t)
                            if pt:
                                out.append((pt, el))
                except Exception as e:
                    log.debug("Strategie get_by_role(%s) Fehler: %s", role, e)
            # Strategie B: get_by_text mit Regex (alle Treffer prüfen, Zeit-Slots können weiter hinten sein)
            if not out:
                try:
                    locs = page.get_by_text(time_pattern)
                    n = locs.count()
                    log.info("Strategie get_by_text: %d Treffer", n)
                    seen_times = set()
                    for i in range(n):
                        el = locs.nth(i)
                        try:
                            el.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                        if el.is_visible():
                            t = (el.inner_text() or "").strip()
                            pt = _parse_time(t)
                            if pt and pt not in seen_times:
                                seen_times.add(pt)
                                out.append((pt, el))
                except Exception as e:
                    log.debug("Strategie get_by_text Fehler: %s", e)
            # Strategie C: locator a, button mit has_text Filter (Stadt Köln nutzt oft Links)
            if not out:
                try:
                    for selector in ["a", "button", "[role='button']", "li a", "li button"]:
                        locs = page.locator(selector).filter(has_text=time_pattern)
                        n = locs.count()
                        if n > 0:
                            log.info("Strategie locator(%s): %d Treffer", selector, n)
                        for i in range(min(n, 30)):
                            el = locs.nth(i)
                            if el.is_visible():
                                t = (el.inner_text() or "").strip()
                                if time_pattern.match(t):
                                    pt = _parse_time(t)
                                    if pt:
                                        out.append((pt, el))
                                        break
                        if out:
                            break
                except Exception as e:
                    log.debug("Strategie locator Fehler: %s", e)
            # Strategie D: Text-Locator (beliebiges Element mit exakt HH:MM)
            if not out:
                try:
                    locs = page.locator("text=/^\\d{1,2}:\\d{2}$/")
                    n = locs.count()
                    log.info("Strategie text-Locator: %d Treffer", n)
                    seen_times = set()
                    for i in range(n):
                        el = locs.nth(i)
                        try:
                            el.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                        if el.is_visible():
                            t = (el.inner_text() or "").strip()
                            pt = _parse_time(t)
                            if pt and pt not in seen_times:
                                seen_times.add(pt)
                                out.append((pt, el))
                except Exception as e:
                    log.debug("Strategie text-Locator Fehler: %s", e)
            # Strategie E: Klick auf Eltern-Element falls Text in Kind (z.B. span in a)
            if not out:
                try:
                    locs = page.locator("text=/^\\d{1,2}:\\d{2}$/")
                    for i in range(min(locs.count(), 30)):
                        el = locs.nth(i)
                        if el.is_visible():
                            t = (el.inner_text() or "").strip()
                            pt = _parse_time(t)
                            if pt:
                                # Versuche klickbares Elternelement (a, button)
                                try:
                                    parent = el.locator("xpath=ancestor::a[1] | ancestor::button[1]")
                                    if parent.count() > 0 and parent.first.is_visible():
                                        out.append((pt, parent.first))
                                    else:
                                        out.append((pt, el))
                                except Exception:
                                    out.append((pt, el))
                except Exception:
                    pass
            return out

        desired_parsed = _parse_time(desired_time) if desired_time else None
        candidates = _collect_candidates()

        if not candidates:
            log.warning("Keine Zeit-Slots gefunden (Selektoren: role link/button, get_by_text, locator a/button)")

        def _get_clickable(el):
            """Liefert klickbares Element (self oder Eltern a/button)."""
            for tag in ["a", "button"]:
                try:
                    parent = el.locator(f"xpath=ancestor::{tag}[1]")
                    if parent.count() > 0 and parent.first.is_visible():
                        return parent.first
                except Exception:
                    pass
            return el

        if candidates:
            times_str = ", ".join(_fmt_time(pt) for pt in sorted(set(c[0] for c in candidates)))
            log.info("Gefundene Termine: %s", times_str or "(keine)")

            if desired_parsed:
                earlier = [(pt, el) for pt, el in candidates if pt < desired_parsed]
                if earlier:
                    pt, el = max(earlier, key=lambda x: x[0])
                    _get_clickable(el).click()
                    log.info("→ Früherer Termin gewählt: %s (spätester vor %s)", _fmt_time(pt), desired_time)
                    time_clicked = True
                else:
                    log.info("→ Kein Termin vor %s verfügbar, nehme ersten verfügbaren.", desired_time)

            if not time_clicked:
                pt, el = min(candidates, key=lambda x: x[0])
                _get_clickable(el).click()
                log.info("→ Erste verfügbare Uhrzeit gewählt: %s", _fmt_time(pt))
                time_clicked = True

    if not time_clicked:
        log.error("Keine Uhrzeit auswählbar")
        return False
    return True


def _geburtsdatum_to_iso(value: str) -> str:
    """Konvertiert TT.MM.JJJJ → YYYY-MM-DD für input type='date'."""
    if not value or not value.strip():
        return ""
    try:
        parts = value.strip().split(".")
        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        pass
    return value


def _fill_geburtsdatum(page, data_testid: str, value: str) -> None:
    """Füllt Geburtsdatum-Feld (input type=date erwartet YYYY-MM-DD)."""
    if not value:
        return
    iso_date = _geburtsdatum_to_iso(value)
    if not iso_date:
        return
    try:
        inp = page.get_by_test_id(data_testid)
        inp.fill(iso_date)
        page.wait_for_timeout(150)
    except Exception as e:
        log.debug("_fill_geburtsdatum %s: %s", data_testid, e)


def _step_ihre_daten(page, config: dict) -> bool:
    """Formular 'Ihre Daten' ausfüllen und 'Termin buchen' klicken."""
    def fill_by_label(label_text: str, value: str, exact: bool = False, alt_labels: list = None) -> None:
        if not value:
            return
        labels_to_try = [label_text] + (alt_labels or [])
        for lbl in labels_to_try:
            try:
                loc = page.get_by_label(lbl, exact=False)
                if loc.count() > 0:
                    el = loc.first
                    el.click()
                    el.fill(value)
                    page.wait_for_timeout(150)
                    return
            except Exception:
                pass
        try:
            page.get_by_placeholder(label_text).first.fill(value)
            return
        except Exception:
            pass
        try:
            page.get_by_placeholder("TT.MM.JJJJ").first.fill(value)
        except Exception as e:
            log.debug("fill_by_label %s: %s", label_text, e)

    def select_by_label(label_text: str, value: str) -> None:
        if not value:
            return
        try:
            page.get_by_label(label_text).first.select_option(value)
        except Exception:
            try:
                page.get_by_role("combobox", name=label_text).first.select_option(value)
            except Exception as e:
                log.debug("select_by_label %s: %s", label_text, e)

    p1 = config.get("person1") or {}
    p2 = config.get("person2") or {}
    contact = config.get("contact") or {}

    # Person 1
    select_by_label("Anrede", p1.get("anrede") or "")
    fill_by_label("Nachname", p1.get("nachname") or "")
    fill_by_label("Vorname", p1.get("vorname") or "")
    # Geburtsdatum: input type="date" erwartet YYYY-MM-DD (Stadt Köln: data-testid)
    _fill_geburtsdatum(page, "person1geburtsdatum_input", p1.get("geburtsdatum") or "")
    fill_by_label("Staatsangehörigkeit", p1.get("staatsangehoerigkeit") or "")
    select_by_label("Wohnsitz innerhalb von Köln", p1.get("wohnsitz_koeln") or "Ja")

    # Person 2 (Labels können "2. Person" enthalten)
    select_by_label("Anrede (2. Person)", p2.get("anrede") or "")
    fill_by_label("Nachname (2. Person)", p2.get("nachname") or "")
    fill_by_label("Vorname (2. Person)", p2.get("vorname") or "")
    _fill_geburtsdatum(page, "person2geburtsdatum_input", p2.get("geburtsdatum") or "")
    fill_by_label("Staatsangehörigkeit (2. Person)", p2.get("staatsangehoerigkeit") or "")
    select_by_label("Wohnsitz innerhalb von Köln (2. Person)", p2.get("wohnsitz_koeln") or "Ja")

    # Kontakt
    email = contact.get("email") or ""
    fill_by_label("E-Mail", email)
    fill_by_label("E-Mail bestätigen", email)
    fill_by_label("Telefonnummer", contact.get("telefon") or "")

    # Kurz warten damit Validierung greift, dann "Termin buchen" klicken
    page.wait_for_timeout(500)
    try:
        page.get_by_role("button", name="Termin buchen").or_(
            page.get_by_text("Termin buchen").first
        ).click(timeout=STEP_TIMEOUT_MS)
    except Exception:
        page.get_by_text("Termin buchen").first.click(timeout=STEP_TIMEOUT_MS)
    return True


def _step_bestaetigung(page, config: dict) -> tuple[bool, str | None]:
    """Bestätigungsseite prüfen, ggf. weiteren Button klicken, Screenshot. Returns (success, screenshot_path)."""
    page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(2000)
    text_lower = (page.inner_text("body") or "").lower()
    # Erfolgsindikatoren
    if "bestätigung" in text_lower or "erfolgreich" in text_lower or "gebucht" in text_lower:
        log.info("→ Buchung erfolgreich (Bestätigung/Erfolg erkannt)")
        path = _screenshot(page, config, "success")
        _notify(config, True, "Trautermin erfolgreich gebucht.")
        return True, path
    # Weitere Bestätigung nötig?
    try:
        btn = page.get_by_role("button", name="Bestätigen").or_(page.get_by_text("Bestätigen").first)
        if btn.is_visible():
            log.info("→ Bestätigen-Button gefunden, klicke...")
            btn.click()
            page.wait_for_timeout(2000)
            path = _screenshot(page, config, "success")
            _notify(config, True, "Trautermin bestätigt.")
            return True, path
    except Exception:
        pass
    log.warning("→ Kein Erfolgsindikator gefunden (bestätigung/erfolgreich/gebucht)")
    path = _screenshot(page, config, "error")
    _notify(config, False, "Trautermin-Buchung: Unklarer Abschluss (siehe Screenshot/Log).")
    return False, path


def run_booking_flow(config: dict, url: str) -> tuple[bool, list[str]]:
    """Führt den kompletten Buchungsflow aus. Returns (success, list_of_screenshot_paths)."""
    screenshots: list[str] = []

    def capture_screenshot(path: str | None) -> str | None:
        if path:
            screenshots.append(path)
        return path

    opts = config.get("options") or {}
    headless = not opts.get("browser_headed", False)
    with sync_playwright() as p:
        log.info("Starte Browser (headless=%s)...", headless)
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        page = context.new_page()
        try:
            log.info("Lade Buchungsseite...")
            page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            # "load" statt "networkidle" – viele Seiten erreichen networkidle nie (Analytics, Polling)
            page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT_MS)
            log.info("Seite geladen, wähle Standort...")
        except PlaywrightTimeout:
            log.warning("Seite lädt langsam, fahre trotzdem fort")
        except Exception as e:
            log.exception("Seite konnte nicht geladen werden: %s", e)
            capture_screenshot(_screenshot(page, config, "error"))
            browser.close()
            return False, screenshots

        try:
            capture_screenshot(_screenshot(page, config, "step_0_seite"))

            log.info("Schritt 1/4: Standort")
            if not _step_standort(page, config):
                capture_screenshot(_screenshot(page, config, "error"))
                return False, screenshots
            page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT_MS)
            page.wait_for_timeout(1000)
            capture_screenshot(_screenshot(page, config, "step_1_standort"))

            log.info("Schritt 2/4: Termin (Datum + Uhrzeit)")
            if not _step_termin(page, config):
                capture_screenshot(_screenshot(page, config, "error"))
                return False, screenshots
            page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT_MS)
            page.wait_for_timeout(1000)
            capture_screenshot(_screenshot(page, config, "step_2_termin"))

            log.info("Schritt 3/4: Ihre Daten")
            if not _step_ihre_daten(page, config):
                capture_screenshot(_screenshot(page, config, "error"))
                return False, screenshots
            page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT_MS)
            capture_screenshot(_screenshot(page, config, "step_3_daten"))

            log.info("Schritt 4/4: Bestätigung")
            success, path = _step_bestaetigung(page, config)
            capture_screenshot(path)
            return success, screenshots
        except PlaywrightTimeout as e:
            log.exception("Timeout: %s", e)
            capture_screenshot(_screenshot(page, config, "error"))
            return False, screenshots
        except Exception as e:
            log.exception("Buchungsflow fehlgeschlagen: %s", e)
            capture_screenshot(_screenshot(page, config, "error"))
            return False, screenshots
        finally:
            browser.close()
    return False, screenshots
