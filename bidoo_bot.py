#!/usr/bin/env python3
"""
Bidoo bot
=========
Flusso:
  1. Si collega a Gmail via IMAP e cerca le email NON lette di Bidoo.
  2. Per ogni email: ne mostra l'HTML dentro un browser headless e
     CLICCA DAVVERO il link (l'ancora) contenuto nell'email.
  3. Dopo l'atterraggio su Bidoo, se serve, fa login nel modale
     (username + password + ENTRA).
  4. (Opzionale) segna l'email come letta.

Tutte le credenziali arrivano da variabili d'ambiente (GitHub Secrets
quando gira su GitHub Actions, oppure un file .env locale per i test).
NIENTE password nel codice.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import sys
import time
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# --------------------------------------------------------------------------- #
# Config (tutto via env / Secrets, con default ragionevoli)
# --------------------------------------------------------------------------- #

SCRIPT_DIR = Path(__file__).resolve().parent
SHOTS_DIR = SCRIPT_DIR / "screenshots"


def _load_dotenv(path: Path) -> None:
    """Carica un .env locale (per i test sul PC). Ignorato da git."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "si", "sì", "on"}


_load_dotenv(SCRIPT_DIR / ".env")

# Gmail / IMAP
GMAIL_USER = env("GMAIL_USER")
GMAIL_APP_PASSWORD = env("GMAIL_APP_PASSWORD")
IMAP_HOST = env("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(env("IMAP_PORT", "993"))
IMAP_FOLDER = env("IMAP_FOLDER", "INBOX")

# Filtro mittente Bidoo (sottostringa cercata nel campo FROM)
BIDOO_FROM = env("BIDOO_FROM", "bidoo")
MAX_EMAILS = int(env("MAX_EMAILS", "5"))

# Credenziali Bidoo.
# Account di basso valore (solo monete virtuali): su esplicita richiesta
# dell'utente, username e password sono scritti qui come default, cosi' non
# serve configurarli. Si possono comunque sovrascrivere con un Secret/variabile
# BIDOO_USER / BIDOO_PASS senza toccare il codice.
# NB: proprio perche' la password e' nel codice, tieni il repository PRIVATO.
BIDOO_USER = env("BIDOO_USER") or "Minonne49"
BIDOO_PASS = env("BIDOO_PASS") or "vai via vai via vai via"

# Comportamento
MARK_SEEN = env_bool("MARK_SEEN", True)          # segna le email come lette
CLICK_ALL = env_bool("CLICK_ALL", False)         # clicca tutti i link buoni, non solo il primo
DRY_RUN = env_bool("DRY_RUN", False)             # non clicca/non fa login: solo log
HEADLESS = env_bool("HEADLESS", True)            # False per vedere il browser in locale
NAV_TIMEOUT = int(env("NAV_TIMEOUT_MS", "30000"))

# Link da NON cliccare (unsubscribe, social, ecc.)
EXCLUDE_LINK = [
    "unsub", "disiscri", "cancella", "preferenz", "privacy", "policy",
    "termini", "cookie", "mailto:", "tel:",
    "facebook", "instagram", "twitter", "x.com", "youtube", "linkedin",
    "tiktok", "whatsapp", "telegram",
    "play.google", "apps.apple", "itunes.apple",
]

# Testi dei bottoni di consenso cookie
COOKIE_TEXTS = [
    "Accetta tutto", "Accetta tutti", "Accetta e chiudi", "Accetta",
    "Accetto", "Acconsento", "Consenti tutti", "Consenti", "Ho capito",
    "OK", "Accept all", "Accept",
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Gmail / IMAP
# --------------------------------------------------------------------------- #

def decode_hdr(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def extract_html(msg: Message) -> str | None:
    """Ritorna l'HTML dell'email (o il testo convertito in HTML)."""
    html = None
    text = None

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            ctype = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if payload is None:
                continue
            decoded = payload.decode(charset, errors="replace")
            if ctype == "text/html" and html is None:
                html = decoded
            elif ctype == "text/plain" and text is None:
                text = decoded
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        decoded = payload.decode(charset, errors="replace") if payload else ""
        if msg.get_content_type() == "text/html":
            html = decoded
        else:
            text = decoded

    if html:
        return html
    if text:
        return plain_to_html(text)
    return None


def plain_to_html(text: str) -> str:
    """Converte gli URL di un'email testuale in ancore cliccabili."""
    url_re = re.compile(r"(https?://[^\s<>\"']+)")
    body = url_re.sub(r'<a href="\1">\1</a>', text)
    body = body.replace("\n", "<br>\n")
    return f"<html><body>{body}</body></html>"


def fetch_bidoo_emails(imap: imaplib.IMAP4_SSL):
    """Ritorna una lista di (uid, Message) delle email Bidoo non lette."""
    imap.select(IMAP_FOLDER)
    # UNSEEN + FROM contenente BIDOO_FROM
    criteria = f'(UNSEEN FROM "{BIDOO_FROM}")'
    status, data = imap.search(None, criteria)
    if status != "OK":
        log(f"Ricerca IMAP fallita: {status}")
        return []
    uids = data[0].split()
    log(f"Email Bidoo non lette trovate: {len(uids)}")
    uids = uids[-MAX_EMAILS:]  # le piu' recenti

    out = []
    for uid in uids:
        # BODY.PEEK => NON segna come letta durante il fetch
        status, msg_data = imap.fetch(uid, "(BODY.PEEK[])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = decode_hdr(msg.get("Subject"))
        log(f"  - UID {uid.decode()}: {subject!r}")
        out.append((uid, msg))
    return out


def mark_seen(imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
    try:
        imap.store(uid, "+FLAGS", "\\Seen")
    except Exception as exc:
        log(f"Impossibile segnare come letta {uid!r}: {exc}")


# --------------------------------------------------------------------------- #
# Browser: click sul link dell'email + login Bidoo
# --------------------------------------------------------------------------- #

def accept_cookies(page) -> None:
    for text in COOKIE_TEXTS:
        try:
            btn = page.get_by_role("button", name=re.compile(re.escape(text), re.I))
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(400)
                log("Banner cookie accettato.")
                return
        except Exception:
            pass
    for sel in (
        "#onetrust-accept-btn-handler",
        ".iubenda-cs-accept-btn",
        "button[aria-label*='accett' i]",
        "button[id*='accept' i]",
    ):
        try:
            el = page.locator(sel)
            if el.count() and el.first.is_visible():
                el.first.click(timeout=3000)
                page.wait_for_timeout(400)
                log("Banner cookie accettato.")
                return
        except Exception:
            pass


def pick_email_links(page) -> list[int]:
    """Indici delle ancore 'buone' (verso Bidoo, non unsubscribe/social)."""
    anchors = page.locator("a[href]")
    total = anchors.count()
    good = []
    for i in range(total):
        href = (anchors.nth(i).get_attribute("href") or "").strip()
        low = href.lower()
        if not low.startswith("http"):
            continue
        if "bidoo" not in low:
            continue
        if any(bad in low for bad in EXCLUDE_LINK):
            continue
        good.append(i)
    log(f"Link cliccabili verso Bidoo nell'email: {len(good)} (su {total} totali)")
    return good


def click_anchor(page, index: int) -> str:
    """Clicca davvero l'ancora index-esima e naviga nella stessa scheda."""
    anchor = page.locator("a[href]").nth(index)
    href = anchor.get_attribute("href") or ""
    # forza la navigazione nella stessa scheda (niente target=_blank)
    try:
        anchor.evaluate("el => el.setAttribute('target', '_self')")
    except Exception:
        pass
    log(f"Click sul link dell'email -> {href}")
    try:
        with page.expect_navigation(timeout=NAV_TIMEOUT):
            anchor.click(timeout=NAV_TIMEOUT)
    except PWTimeout:
        # fallback: alcune email usano redirect JS; apriamo l'href
        log("Navigazione da click non rilevata, apro l'URL direttamente.")
        page.goto(href, timeout=NAV_TIMEOUT)
    page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    return href


def find_username_field(page):
    sel = (
        'input:not([type="password"]):not([type="hidden"])'
        ':not([type="checkbox"]):not([type="submit"])'
        ':not([type="button"]):not([type="radio"]):not([type="file"])'
    )
    loc = page.locator(sel)
    for i in range(loc.count()):
        if loc.nth(i).is_visible():
            return loc.nth(i)
    return loc.first


def is_logged_in(page) -> bool:
    """Euristica: se c'e' ancora un bottone/link LOGIN visibile, non siamo loggati."""
    try:
        login = page.get_by_text(re.compile(r"^\s*login\s*$", re.I))
        for i in range(login.count()):
            if login.nth(i).is_visible():
                return False
    except Exception:
        pass
    return True


def ensure_logged_in(page) -> None:
    if not BIDOO_USER or not BIDOO_PASS:
        log("ATTENZIONE: BIDOO_USER / BIDOO_PASS non impostati: salto il login.")
        return

    accept_cookies(page)

    if is_logged_in(page):
        log("Sembra gia' loggato: salto il login.")
        return

    # Apri il modale di login se il campo password non e' gia' visibile
    pwd = page.locator('input[type="password"]')
    if not (pwd.count() and pwd.first.is_visible()):
        for attempt in (
            lambda: page.get_by_role("button", name=re.compile("login", re.I)).first.click(timeout=4000),
            lambda: page.get_by_role("link", name=re.compile("login", re.I)).first.click(timeout=4000),
            lambda: page.get_by_text(re.compile(r"^\s*login\s*$", re.I)).first.click(timeout=4000),
        ):
            try:
                attempt()
                page.wait_for_timeout(1200)
                break
            except Exception:
                continue

    pwd = page.locator('input[type="password"]')
    try:
        pwd.first.wait_for(state="visible", timeout=12000)
    except PWTimeout:
        save_shot(page, "login-no-modal")
        log("Modale di login non apparso: controlla lo screenshot.")
        return

    user_field = find_username_field(page)
    try:
        user_field.fill(BIDOO_USER, timeout=8000)
        pwd.first.fill(BIDOO_PASS, timeout=8000)
    except Exception as exc:
        save_shot(page, "login-fill-error")
        log(f"Errore nel compilare il form di login: {exc}")
        return

    log("Credenziali inserite, invio il form (ENTRA).")
    submitted = False
    for attempt in (
        lambda: page.get_by_role("button", name=re.compile("entra", re.I)).first.click(timeout=4000),
        lambda: page.get_by_text(re.compile(r"^\s*entra\s*$", re.I)).first.click(timeout=4000),
        lambda: pwd.first.press("Enter"),
    ):
        try:
            attempt()
            submitted = True
            break
        except Exception:
            continue

    if not submitted:
        save_shot(page, "login-no-submit")
        log("Non sono riuscito a cliccare ENTRA: controlla lo screenshot.")
        return

    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    except PWTimeout:
        pass
    page.wait_for_timeout(1500)
    save_shot(page, "after-login")
    if is_logged_in(page):
        log("Login eseguito (nessun bottone LOGIN residuo).")
    else:
        log("Login forse NON riuscito: vedo ancora LOGIN. Controlla lo screenshot/credenziali.")


def save_shot(page, name: str) -> None:
    SHOTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    path = SHOTS_DIR / f"{ts}-{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"Screenshot: {path.name}")
    except Exception as exc:
        log(f"Screenshot fallito ({name}): {exc}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def process_emails(emails, imap) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            locale="it-IT",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )
        context.set_default_timeout(NAV_TIMEOUT)
        page = context.new_page()
        logged_in = False

        for uid, msg in emails:
            subject = decode_hdr(msg.get("Subject"))
            log(f"=== Email: {subject!r} ===")
            html = extract_html(msg)
            if not html:
                log("Nessun corpo HTML/testo: salto.")
                continue

            # Mostra l'HTML dell'email nel browser, cosi' il click e' reale
            page.set_content(html, wait_until="domcontentloaded")
            links = pick_email_links(page)
            if not links:
                log("Nessun link Bidoo utile in questa email: salto.")
                continue

            targets = links if CLICK_ALL else links[:1]
            for idx in targets:
                if DRY_RUN:
                    href = page.locator("a[href]").nth(idx).get_attribute("href")
                    log(f"[DRY_RUN] Avrei cliccato: {href}")
                    continue

                click_anchor(page, idx)
                accept_cookies(page)
                if not logged_in:
                    ensure_logged_in(page)
                    logged_in = is_logged_in(page)
                save_shot(page, "landing")
                # torna a mostrare l'email per l'eventuale link successivo
                page.set_content(html, wait_until="domcontentloaded")

            if MARK_SEEN and not DRY_RUN:
                mark_seen(imap, uid)
                log("Email segnata come letta.")

        context.close()
        browser.close()


def main() -> int:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log("ERRORE: imposta GMAIL_USER e GMAIL_APP_PASSWORD (Secrets o .env).")
        return 2

    log(f"Connessione a {IMAP_HOST}:{IMAP_PORT} come {GMAIL_USER} ...")
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    except imaplib.IMAP4.error as exc:
        log(f"Login IMAP fallito: {exc}")
        log("Su Gmail serve una 'Password per le app' (non quella normale) "
            "e l'accesso IMAP attivo.")
        return 2

    try:
        emails = fetch_bidoo_emails(imap)
        if not emails:
            log("Nessuna email Bidoo da processare. Fine.")
            return 0
        if DRY_RUN:
            log(">>> DRY_RUN attivo: nessun click/login verra' eseguito. <<<")
        process_emails(emails, imap)
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    log("Fatto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
