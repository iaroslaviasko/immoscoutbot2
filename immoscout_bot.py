#!/usr/bin/env python3
"""
ImmobilienScout24 → Telegram Apartment Bot (ScraperAPI, v4 diagnostic edition)
"""

import os
import json
import time
import logging
import schedule
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
SCRAPERAPI_KEY  = os.environ["SCRAPERAPI_KEY"]

TARGET_URL = (
    "https://www.immobilienscout24.de/Suche/shape/wohnung-mit-einbaukueche-mieten"
    "?shape=b213ZEh5d2tlQWpMd19AcE95bkBgaUB9c0FzR3lsQ3dDfWVAZ0V5VW9Ba1pwWntqQmNnQGVfQ1d1eUFhZ0BfTn1vQT90SG5iQXd2QHpxRGJNdmJBZUR2RWdsQHlYc1thQHlgQWJeZVB_XF9OeH1DYktodERoQ2hDeGlDbGNA"
    "&numberofrooms=2.0-"
    "&price=-1600.0"
    "&livingspace=55.0-"
    "&exclusioncriteria=swapflat"
    "&pricetype=calculatedtotalrent"
    "&sorting=2"
    "&pagenumber=1"
)

POLL_INTERVAL_SECONDS = 900   # 15 min (premium requests cost 10-25 credits each)
SEEN_IDS_FILE = "seen_ids.json"

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── SCRAPERAPI WRAPPER ──────────────────────────────────────────────────────

def scraperapi_url(target: str) -> str:
    """Wrap a target URL in a ScraperAPI request — with premium + JS render for ImmoScout."""
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          target,
        "country_code": "de",
        "premium":      "true",     # residential premium IPs (harder to block)
        "render":       "true",     # execute JS (handles Cloudflare challenge)
    }
    return f"https://api.scraperapi.com/?{urlencode(params)}"

# ─── PERSISTENCE ─────────────────────────────────────────────────────────────

def load_seen_ids() -> set:
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen_ids(ids: set):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)

# ─── SCRAPING ────────────────────────────────────────────────────────────────

def fetch_listings() -> list[dict]:
    log.info("Requesting ImmoScout via ScraperAPI…")
    try:
        resp = requests.get(scraperapi_url(TARGET_URL), timeout=120)
        log.info(f"Response status: {resp.status_code}, size: {len(resp.text)} bytes")
    except Exception as e:
        log.error(f"Request failed: {e}")
        return []

    if resp.status_code != 200:
        log.error(f"Non-200 status code: {resp.status_code}")
        log.error(f"Response preview: {resp.text[:500]}")
        return []

    # Diagnostic: detect Cloudflare/block pages
    text_lower = resp.text.lower()
    if "cf-chl" in text_lower or "cloudflare" in text_lower and "challenge" in text_lower:
        log.warning("Looks like a Cloudflare challenge page was returned!")
    if "captcha" in text_lower:
        log.warning("Page contains CAPTCHA keyword")

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    all_li = soup.select("li[data-id]")
    log.info(f"Found {len(all_li)} li[data-id] elements in HTML")

    for item in all_li:
        listing_id = item.get("data-id", "").strip()
        if not listing_id:
            continue

        title_el = item.select_one("h5.result-list-entry__brand-title, .result-list-entry__headline")
        title = title_el.get_text(strip=True) if title_el else "Wohnung"

        price_el = item.select_one(
            ".result-list-entry__primary-criterion dt:-soup-contains('Kaltmiete') + dd,"
            ".result-list-entry__price"
        )
        price = price_el.get_text(strip=True) if price_el else "Preis unbekannt"

        criteria = {
            el.find("dt").get_text(strip=True): el.find("dd").get_text(strip=True)
            for el in item.select(".result-list-entry__primary-criterion")
            if el.find("dt") and el.find("dd")
        }
        size  = criteria.get("Wohnfläche", "—")
        rooms = criteria.get("Zimmer",     "—")

        addr_el = item.select_one(".result-list-entry__address")
        address = addr_el.get_text(strip=True) if addr_el else "Adresse unbekannt"

        link_el = item.select_one("a.result-list-entry__brand-title-container, a[href*='/expose/']")
        href = link_el["href"] if link_el and link_el.get("href") else ""
        if href.startswith("/"):
            href = "https://www.immobilienscout24.de" + href

        listings.append({
            "id":      listing_id,
            "title":   title,
            "price":   price,
            "size":    size,
            "rooms":   rooms,
            "address": address,
            "url":     href,
        })

    log.info(f"Parsed {len(listings)} listings from ImmoScout24")
    return listings

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram message sent ✓")
    except requests.RequestException as e:
        log.error(f"Telegram send failed: {e}")

def format_listing(l: dict) -> str:
    return (
        f"🏠 <b>{l['title']}</b>\n"
        f"💶 {l['price']}   🛏 {l['rooms']} Zi.   📐 {l['size']}\n"
        f"📍 {l['address']}\n"
        f"🔗 <a href=\"{l['url']}\">Zur Anzeige →</a>"
    )

# ─── MAIN LOOP ───────────────────────────────────────────────────────────────

def check_for_new():
    seen = load_seen_ids()
    listings = fetch_listings()

    if not seen and listings:
        log.info(f"First run — saving {len(listings)} listings as baseline")
        save_seen_ids({l["id"] for l in listings})
        return

    new = [l for l in listings if l["id"] not in seen]
    if not new:
        log.info("No new listings.")
        return

    log.info(f"Found {len(new)} new listing(s)! Sending to Telegram…")
    for l in new:
        send_telegram(format_listing(l))
        seen.add(l["id"])
        time.sleep(0.5)
    save_seen_ids(seen)

def main():
    log.info("ImmoScout24 → Telegram bot starting (ScraperAPI premium)…")
    send_telegram("🤖 ImmoScout24-Bot (v4) gestartet!")

    check_for_new()
    schedule.every(POLL_INTERVAL_SECONDS).seconds.do(check_for_new)

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
