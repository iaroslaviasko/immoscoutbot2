#!/usr/bin/env python3
"""
Kleinanzeigen → Telegram Apartment Bot
Polls kleinanzeigen.de for new rental listings and sends them to Telegram.
"""

import os
import json
import time
import logging
import schedule
import requests
from bs4 import BeautifulSoup

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID        = os.environ["CHAT_ID"]

# Munich rentals · min 2 rooms · max 1600€ · min 55m²
# To change filters: go to https://www.kleinanzeigen.de/s-wohnung-mieten/
# apply filters in the browser, then copy the URL from the address bar
SEARCH_URL = (
    "https://www.kleinanzeigen.de/s-wohnung-mieten/muenchen/"
    "anzeige:angebote/preis::1600/"
    "c203l6411+wohnung_mieten.qm_d:55,"
    "+wohnung_mieten.zimmer_d:2,"
)

POLL_INTERVAL_SECONDS = 120   # 2 minutes — be gentle
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
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

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
    try:
        resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Request failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    for item in soup.select("article.aditem"):
        ad_id = item.get("data-adid", "").strip()
        if not ad_id:
            continue

        title_el = item.select_one("a.ellipsis")
        title = title_el.get_text(strip=True) if title_el else "Wohnung"
        href = title_el["href"] if title_el and title_el.get("href") else ""
        if href.startswith("/"):
            href = "https://www.kleinanzeigen.de" + href

        price_el = item.select_one(".aditem-main--middle--price-shipping--price, .aditem-main--middle--price")
        price = price_el.get_text(strip=True) if price_el else "Preis n/a"

        loc_el = item.select_one(".aditem-main--top--left")
        location = loc_el.get_text(strip=True) if loc_el else ""

        tags = [t.get_text(strip=True) for t in item.select(".simpletag")]
        tags_str = " · ".join(tags) if tags else ""

        listings.append({
            "id":       ad_id,
            "title":    title,
            "price":    price,
            "location": location,
            "tags":     tags_str,
            "url":      href,
        })

    log.info(f"Fetched {len(listings)} listings from Kleinanzeigen")
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
        f"💶 {l['price']}\n"
        f"📍 {l['location']}\n"
        f"🏷 {l['tags']}\n"
        f"🔗 <a href=\"{l['url']}\">Zur Anzeige →</a>"
    )

# ─── MAIN LOOP ───────────────────────────────────────────────────────────────

def check_for_new():
    seen = load_seen_ids()
    listings = fetch_listings()

    # First run: save all as baseline so you don't get spammed with 25 old ads
    if not seen and listings:
        log.info("First run — saving existing listings as baseline, no notifications")
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
    log.info("Kleinanzeigen → Telegram bot starting…")
    send_telegram("🤖 Kleinanzeigen-Bot gestartet! Ich benachrichtige dich über neue Wohnungen in München.")

    check_for_new()
    schedule.every(POLL_INTERVAL_SECONDS).seconds.do(check_for_new)

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
