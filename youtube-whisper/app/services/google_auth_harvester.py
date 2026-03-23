"""Google Auth with 2FA support via Dashboard."""

import json
import logging
import time
import base64
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

COOKIE_PATH = Path("/app/data/cookies.txt")
HISTORY_PATH = Path("/app/data/cookie-history.json")
SCREENSHOT_PATH = Path("/app/data/2fa-screenshot.png")
STATE_PATH = Path("/app/data/google-auth-state.json")


def _cookies_to_netscape(cookies):
    lines = ["# Netscape HTTP Cookie File\n# Google Auth Harvester\n"]
    for c in cookies:
        domain = c.get("domain", "")
        if not domain.startswith("."):
            domain = "." + domain
        expires = c.get("expires", -1)
        if expires < 0:
            expires = int(time.time()) + 86400 * 30
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        lines.append(f"{domain}\tTRUE\t{c.get('path','/')}\t{secure}\t{int(expires)}\t{c.get('name','')}\t{c.get('value','')}")
    return "\n".join(lines)


def _save_history(method, count):
    hist = []
    if HISTORY_PATH.exists():
        try: hist = json.loads(HISTORY_PATH.read_text())
        except: pass
    hist.append({"method": method, "entries": count,
                 "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")})
    HISTORY_PATH.write_text(json.dumps(hist[-20:]))


def _save_state(state):
    STATE_PATH.write_text(json.dumps(state))


def _load_state():
    if STATE_PATH.exists():
        try: return json.loads(STATE_PATH.read_text())
        except: pass
    return {}


# Global: keep browser alive for 2FA
_browser_context = {"browser": None, "context": None, "page": None, "playwright": None}


def start_google_login(email, password):
    """Step 1: Start login, return screenshot if 2FA needed."""
    from playwright.sync_api import sync_playwright

    # Close any existing session
    close_browser()

    p = sync_playwright().start()
    browser = p.firefox.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="de-DE",
        timezone_id="Europe/Berlin",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    )
    page = context.new_page()

    _browser_context["playwright"] = p
    _browser_context["browser"] = browser
    _browser_context["context"] = context
    _browser_context["page"] = page

    try:
        # Go to Google login
        page.goto("https://accounts.google.com/signin/v2/identifier?service=youtube&hl=de",
                  wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Enter email
        email_input = page.locator('input[type="email"]')
        if not email_input.is_visible(timeout=5000):
            screenshot = _take_screenshot(page)
            return {"status": "error", "message": "Email-Feld nicht gefunden", "screenshot": screenshot}

        email_input.fill(email)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)

        # Enter password
        pw_input = page.locator('input[type="password"]')
        if not pw_input.is_visible(timeout=5000):
            screenshot = _take_screenshot(page)
            return {"status": "error", "message": "Passwort-Feld nicht gefunden", "screenshot": screenshot}

        pw_input.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        # Check what happened
        url = page.url

        # Check if logged in successfully (redirected to youtube or myaccount)
        if "youtube.com" in url or "myaccount.google" in url:
            return _finish_login()

        # 2FA or challenge
        screenshot = _take_screenshot(page)
        _save_state({"status": "2fa_waiting", "email": email, "timestamp": datetime.now().isoformat()})

        # Detect 2FA type
        page_text = page.text_content("body") or ""
        if "Authenticator" in page_text or "Code" in page_text or "code" in page_text:
            hint = "Google verlangt einen 2FA-Code (Authenticator App)"
        elif "Smartphone" in page_text or "Handy" in page_text or "phone" in page_text.lower():
            hint = "Google verlangt Bestaetigung auf deinem Smartphone"
        else:
            hint = "Google verlangt eine Verifizierung"

        return {
            "status": "2fa_required",
            "message": hint,
            "screenshot": screenshot,
        }

    except Exception as e:
        close_browser()
        return {"status": "error", "message": str(e)[:200]}


def submit_2fa_code(code):
    """Step 2: Submit 2FA code."""
    page = _browser_context.get("page")
    if not page:
        return {"status": "error", "message": "Keine aktive Login-Session. Starte Login erneut."}

    try:
        # Try to find and fill code input
        for selector in [
            'input[type="tel"]',
            'input[name="totpPin"]',
            'input[id="totpPin"]',
            'input[aria-label*="Code"]',
            'input[aria-label*="code"]',
            'input[type="text"]',
        ]:
            try:
                inp = page.locator(selector).first
                if inp.is_visible(timeout=2000):
                    inp.fill(code)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(5000)
                    break
            except:
                continue

        # Check result
        url = page.url
        if "youtube.com" in url or "myaccount.google" in url:
            return _finish_login()

        # Maybe another challenge
        screenshot = _take_screenshot(page)
        return {
            "status": "2fa_required",
            "message": "Noch eine Verifizierung noetig",
            "screenshot": screenshot,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


def confirm_phone_2fa():
    """Step 2b: Just wait for phone confirmation (user taps on phone)."""
    page = _browser_context.get("page")
    if not page:
        return {"status": "error", "message": "Keine aktive Session"}

    try:
        # Wait up to 60 seconds for redirect
        for i in range(12):
            page.wait_for_timeout(5000)
            url = page.url
            if "youtube.com" in url or "myaccount.google" in url:
                return _finish_login()

        screenshot = _take_screenshot(page)
        return {"status": "2fa_required", "message": "Timeout - bitte auf dem Handy bestaetigen", "screenshot": screenshot}

    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


def _finish_login():
    """Navigate to YouTube and extract cookies."""
    page = _browser_context.get("page")
    context = _browser_context.get("context")

    try:
        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # GDPR
        for sel in ['button:has-text("Alle akzeptieren")', 'button:has-text("Accept all")']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    page.wait_for_timeout(1500)
                    break
            except: pass

        cookies = context.cookies()
        yt_cookies = [c for c in cookies if ".youtube.com" in c.get("domain","") or ".google.com" in c.get("domain","")]
        login_cookies = [c for c in yt_cookies if c.get("name") in ("SID","HSID","SSID","SAPISID","LOGIN_INFO")]

        netscape = _cookies_to_netscape(yt_cookies)
        COOKIE_PATH.write_text(netscape, encoding="utf-8")
        _save_history("google-login-2fa", len(yt_cookies))
        _save_state({"status": "logged_in", "cookie_count": len(yt_cookies),
                     "login_cookies": len(login_cookies), "timestamp": datetime.now().isoformat()})

        close_browser()

        return {
            "status": "ok",
            "message": f"{len(yt_cookies)} Cookies (davon {len(login_cookies)} Login-Cookies)",
            "cookie_count": len(yt_cookies),
            "logged_in": len(login_cookies) >= 2,
        }
    except Exception as e:
        close_browser()
        return {"status": "error", "message": str(e)[:200]}


def _take_screenshot(page):
    """Take screenshot and return as base64."""
    page.screenshot(path=str(SCREENSHOT_PATH))
    return base64.b64encode(SCREENSHOT_PATH.read_bytes()).decode()


def close_browser():
    try:
        if _browser_context.get("context"): _browser_context["context"].close()
    except: pass
    try:
        if _browser_context.get("browser"): _browser_context["browser"].close()
    except: pass
    try:
        if _browser_context.get("playwright"): _browser_context["playwright"].stop()
    except: pass
    _browser_context.clear()
