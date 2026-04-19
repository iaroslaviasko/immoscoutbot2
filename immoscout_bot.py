#!/usr/bin/env python3
"""
ImmobilienScout24 → Telegram Apartment Bot
Polls ImmoScout24 for new listings and sends them instantly to Telegram.

Setup:
  1. pip install requests python-telegram-bot schedule beautifulsoup4 lxml
  2. Fill in TELEGRAM_TOKEN, CHAT_ID, and SEARCH_URL below
  3. python immoscout_bot.py
"""

import os
import json
import time
import logging
import schedule
import requests
from datetime import datetime
from bs4 import BeautifulSoup

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]   # set in Railway dashboard → Variables
CHAT_ID        = os.environ["CHAT_ID"]           # set in Railway dashboard → Variables

# Your custom shape-based search:
# Munich area · furnished kitchen · 2+ rooms · max 1600€ warm · 55m²+
SEARCH_URL = (
    "https://www.immobilienscout24.de/Suche/shape/wohnung-mit-einbaukueche-mieten"
    "?shape=b213ZEh5d2tlQWpMd19AcE95bkBgaUB9c0FzR3lsQ3dDfWVAZ0V5VW9Ba1pwWntqQmNnQGVfQ1d1eUFhZ0BfTn1vQT90SG5iQXd2QHpxRGJNdmJBZUR2RWdsQHlYc1thQHlgQWJeZVB_XF9OeH1DYktodERoQ2hDeGlDbGNA"
    "&numberofrooms=2.0-"
    "&price=-1600.0"
    "&livingspace=55.0-"
    "&exclusioncriteria=swapflat"
    "&pricetype=calculatedtotalrent"
    "&sorting=2"        # sort by newest first
    "&pagenumber=1"
)

POLL_INTERVAL_SECONDS = 60   # check every 60 seconds
SEEN_IDS_FILE = "seen_ids.json"

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Priority": "u=0, i",
}

# Persistent session — keeps cookies (including Cloudflare clearance) between requests
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def warmup_session():
    """Visit the homepage first to get cookies set, like a real user would."""
    try:
        log.info("Warming up session (fetching homepage first)…")
        SESSION.get("https://www.immobilienscout24.de/", timeout=15)
        time.sleep(2)   # small pause like a human
    except requests.RequestException as e:
        log.warning(f"Warmup failed (continuing anyway): {e}")

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
    """Scrape the first page of ImmoScout24 results."""
    try:
        resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Request failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    # ImmoScout24 wraps each result in a <li> with data-id
    for item in soup.select("li[data-id]"):
        listing_id = item.get("data-id", "").strip()
        if not listing_id:
            continue

        # Title
        title_el = item.select_one("h5.result-list-entry__brand-title, .result-list-entry__headline")
        title = title_el.get_text(strip=True) if title_el else "Wohnung"

        # Price
        price_el = item.select_one(".result-list-entry__primary-criterion dt:-soup-contains('Kaltmiete') + dd,"
                                   ".result-list-entry__price")
        price = price_el.get_text(strip=True) if price_el else "Preis unbekannt"

        # Size & rooms — grab all criteria
        criteria = {
            el.find("dt").get_text(strip=True): el.find("dd").get_text(strip=True)
            for el in item.select(".result-list-entry__primary-criterion")
            if el.find("dt") and el.find("dd")
        }

        size  = criteria.get("Wohnfläche", "—")
        rooms = criteria.get("Zimmer",     "—")

        # Address
        addr_el = item.select_one(".result-list-entry__address")
        address = addr_el.get_text(strip=True) if addr_el else "Adresse unbekannt"

        # Direct link
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

    log.info(f"Fetched {len(listings)} listings from ImmoScout24")
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
    new = [l for l in listings if l["id"] not in seen]

    if not new:
        log.info("No new listings.")
        return

    log.info(f"Found {len(new)} new listing(s)! Sending to Telegram…")
    for l in new:
        send_telegram(format_listing(l))
        seen.add(l["id"])
        time.sleep(0.5)   # avoid Telegram rate limit

    save_seen_ids(seen)

def main():
    log.info("ImmoScout24 → Telegram bot starting…")

    send_telegram("🤖 ImmoScout24-Bot gestartet! Ich benachrichtige dich über neue Wohnungen.")

    # Run once immediately, then on schedule
    check_for_new()
    schedule.every(POLL_INTERVAL_SECONDS).seconds.do(check_for_new)

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
