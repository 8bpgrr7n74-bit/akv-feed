#!/usr/bin/env python3
"""AKV Insolvenzticker -> Atom-Feed + anonymisierte Statistik (ganz Österreich).

Holt https://www.akv.at/insolvenzticker plus die neun Bundesland-Filter,
schreibt docs/feed.xml und zählt neue Verfahren nach Eröffnungsdatum,
Bundesland und Rechtsform in docs/stats/daily.csv. Namen werden nicht gespeichert,
zur Dublettenerkennung nur ein Hash der AKV-ID (data/seen.json).
"""
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

SOURCE = "https://www.akv.at/insolvenzticker"
FEED_URL = "https://8bpgrr7n74-bit.github.io/akv-feed/feed.xml"
BUNDESLAENDER = ["wien", "niederoesterreich", "burgenland", "oberoesterreich",
                 "salzburg", "tirol", "vorarlberg", "steiermark", "kaernten"]

FEED_OUT = Path("docs/feed.xml")
DAILY_CSV = Path("docs/stats/daily.csv")
META_JSON = Path("docs/stats/meta.json")
SEEN_JSON = Path("data/seen.json")
FEED_MAX = 100
SEEN_KEEP_DAYS = 120

ENTRY_HREF = re.compile(r"/insolvenz/(\d+)-")
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
AZ_RE = re.compile(r"\b\d+\s+S\s+\d+/\d+[a-z]\b")
COURT_RE = re.compile(r"(Handelsgericht|Landesgericht|Bezirksgericht)[^|]*")

# Gericht -> Bundesland (Reihenfolge zählt: spezifisch vor allgemein)
COURT_MAP = [
    ("Wiener Neustadt", "Niederösterreich"), ("St. Pölten", "Niederösterreich"),
    ("Krems", "Niederösterreich"), ("Korneuburg", "Niederösterreich"),
    ("Wien", "Wien"), ("Eisenstadt", "Burgenland"),
    ("Linz", "Oberösterreich"), ("Wels", "Oberösterreich"),
    ("Ried", "Oberösterreich"), ("Steyr", "Oberösterreich"),
    ("Salzburg", "Salzburg"), ("Innsbruck", "Tirol"), ("Feldkirch", "Vorarlberg"),
    ("Graz", "Steiermark"), ("Leoben", "Steiermark"), ("Klagenfurt", "Kärnten"),
]

RECHTSFORMEN = [
    (r"Verlassenschaft", "Verlassenschaft"),
    (r"Privatstiftung", "Privatstiftung"),
    (r"GmbH\s*&\s*Co\.?\s*KG|GesmbH\s*&\s*Co", "GmbH & Co KG"),
    (r"\bGmbH\b|Ges\.?\s*m\.?\s*b\.?\s*H|GesmbH|m\.b\.H", "GmbH"),
    (r"\bAG\b|Aktiengesellschaft", "AG"),
    (r"\bKG\b", "KG"),
    (r"\bOG\b", "OG"),
    (r"\be\.\s?U\.?", "e.U."),
    (r"\bVerein\b", "Verein"),
    (r"\beGen\b|Genossenschaft", "Genossenschaft"),
]


def bundesland(court: str) -> str:
    for key, bl in COURT_MAP:
        if key in court:
            return bl
    return "unbekannt"


def rechtsform(name: str) -> str:
    for pattern, label in RECHTSFORMEN:
        if re.search(pattern, name):
            return label
    return "Einzelperson"


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (akv-feed; personal RSS)"},
                     timeout=30)
    r.raise_for_status()
    return r.text


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries, seen = [], set()
    for a in soup.find_all("a", href=ENTRY_HREF):
        url = a["href"]
        if url.startswith("/"):
            url = "https://www.akv.at" + url
        if url in seen:
            continue
        parts = [p.strip() for p in a.get_text("|").split("|") if p.strip()]
        text = "|".join(parts)
        d = DATE_RE.search(text)
        if not d:
            continue
        name = re.sub(r"\s+", " ", text[: d.start()].replace("|", " ")).strip(" ,")
        az = AZ_RE.search(text)
        court = COURT_RE.search(text[d.end():])
        court = court[0].strip() if court else ""
        seen.add(url)
        entries.append({
            "id": ENTRY_HREF.search(url)[1],
            "url": url,
            "name": name,
            "date": date(int(d[3]), int(d[2]), int(d[1])),
            "date_str": d[0],
            "az": az[0] if az else "",
            "court": court,
            "bl": bundesland(court),
            "rf": rechtsform(name),
        })
    return entries


def collect() -> tuple[list[dict], date | None]:
    """Alle Seiten holen. Liefert Einträge und den Tag, ab dem alle Seiten lückenlos sind."""
    pages = [SOURCE] + [f"{SOURCE}?fwp_categories={b}" for b in BUNDESLAENDER]
    by_id, min_dates = {}, []
    for i, url in enumerate(pages):
        try:
            entries = parse(fetch(url))
        except Exception as exc:  # einzelne Bundesland-Seite darf ausfallen
            if i == 0:
                raise
            print(f"WARN {url}: {exc}")
            continue
        if entries:
            min_dates.append(min(e["date"] for e in entries))
        for e in entries:
            by_id.setdefault(e["id"], e)
        time.sleep(1)
    complete_from = max(min_dates) if min_dates else None
    return list(by_id.values()), complete_from


def write_feed(entries: list[dict]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    newest = sorted(entries, key=lambda e: (e["date"], int(e["id"])), reverse=True)[:FEED_MAX]
    items = []
    for e in newest:
        ts = f"{e['date'].isoformat()}T00:00:00Z"
        summary = f"Eröffnung: {e['date_str']} · {e['az']} · {e['court']}"
        items.append(f"""  <entry>
    <title>{escape(e['name'])}</title>
    <link href="{escape(e['url'])}"/>
    <id>{escape(e['url'])}</id>
    <updated>{ts}</updated>
    <category term="{escape(e['bl'])}"/>
    <summary>{escape(summary)}</summary>
  </entry>""")
    FEED_OUT.parent.mkdir(parents=True, exist_ok=True)
    FEED_OUT.write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>AKV Insolvenzticker Österreich</title>
  <link href="{SOURCE}"/>
  <link rel="self" href="{FEED_URL}"/>
  <id>{SOURCE}</id>
  <author><name>AKV EUROPA</name></author>
  <updated>{now}</updated>
{chr(10).join(items)}
</feed>
""", encoding="utf-8")


def update_stats(entries: list[dict], complete_from: date | None) -> int:
    today = date.today()
    seen = json.loads(SEEN_JSON.read_text()) if SEEN_JSON.exists() else {}
    counts: dict[tuple, int] = {}
    if DAILY_CSV.exists():
        with DAILY_CSV.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                counts[(row["datum"], row["bundesland"], row["rechtsform"])] = int(row["anzahl"])

    new = 0
    for e in entries:
        h = hashlib.sha256(f"akv:{e['id']}".encode()).hexdigest()[:16]
        if h in seen:
            continue
        seen[h] = today.isoformat()
        key = (e["date"].isoformat(), e["bl"], e["rf"])
        counts[key] = counts.get(key, 0) + 1
        new += 1

    cutoff = (today - timedelta(days=SEEN_KEEP_DAYS)).isoformat()
    seen = {h: d for h, d in seen.items() if d >= cutoff}

    SEEN_JSON.parent.mkdir(parents=True, exist_ok=True)
    SEEN_JSON.write_text(json.dumps(seen, sort_keys=True, indent=0))
    DAILY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DAILY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["datum", "bundesland", "rechtsform", "anzahl"])
        for (d, bl, rf), n in sorted(counts.items()):
            w.writerow([d, bl, rf, n])

    meta = json.loads(META_JSON.read_text()) if META_JSON.exists() else {}
    if "complete_from" not in meta and complete_from:
        meta["complete_from"] = complete_from.isoformat()  # nur beim allerersten Lauf
    meta["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    META_JSON.write_text(json.dumps(meta, indent=2))
    return new


def main() -> int:
    entries, complete_from = collect()
    if not entries:
        print("Keine Einträge gefunden - Markup geändert? Nichts geschrieben.")
        return 1  # Workflow schlägt fehl -> GitHub schickt Mail
    write_feed(entries)
    new = update_stats(entries, complete_from)
    print(f"{len(entries)} Einträge gesehen, {new} neu gezählt, lückenlos ab {complete_from}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
