#!/usr/bin/env python3
"""AKV Insolvenzticker -> Atom-Feed (ganz Österreich).

Holt https://www.akv.at/insolvenzticker, parst alle Einträge
(Firmenname, Eröffnungsdatum, Aktenzeichen, Gericht) und schreibt docs/feed.xml.
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

SOURCE = "https://www.akv.at/insolvenzticker"
OUT = Path("docs/feed.xml")
FEED_URL = "https://8bpgrr7n74-bit.github.io/akv-feed/feed.xml"

ENTRY_HREF = re.compile(r"/insolvenz/\d+-")
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
AZ_RE = re.compile(r"\b\d+\s+S\s+\d+/\d+[a-z]\b")
COURT_RE = re.compile(r"(Handelsgericht|Landesgericht|Bezirksgericht)[^|]*")


def fetch() -> str:
    r = requests.get(
        SOURCE,
        headers={"User-Agent": "Mozilla/5.0 (akv-feed; personal RSS)"},
        timeout=30,
    )
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
        # Firmenname = alles vor dem Datum
        name = text[: d.start()].replace("|", " ").strip(" ,")
        az = AZ_RE.search(text)
        court = COURT_RE.search(text[d.end():])
        seen.add(url)
        entries.append({
            "url": url,
            "name": re.sub(r"\s+", " ", name),
            "date": datetime(int(d[3]), int(d[2]), int(d[1]), tzinfo=timezone.utc),
            "date_str": d[0],
            "az": az[0] if az else "",
            "court": court[0].strip() if court else "",
        })
    return entries


def atom(entries: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    for e in entries:
        ts = e["date"].strftime("%Y-%m-%dT%H:%M:%SZ")
        summary = f"Eröffnung: {e['date_str']} · {e['az']} · {e['court']}"
        items.append(f"""  <entry>
    <title>{escape(e['name'])}</title>
    <link href="{escape(e['url'])}"/>
    <id>{escape(e['url'])}</id>
    <updated>{ts}</updated>
    <category term="{escape(e['court'])}"/>
    <summary>{escape(summary)}</summary>
  </entry>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>AKV Insolvenzticker Österreich</title>
  <link href="{SOURCE}"/>
  <link rel="self" href="{FEED_URL}"/>
  <id>{SOURCE}</id>
  <author><name>AKV EUROPA</name></author>
  <updated>{now}</updated>
{chr(10).join(items)}
</feed>
"""


def main() -> int:
    entries = parse(fetch())
    if not entries:
        print("Keine Einträge gefunden - Markup geändert? Feed bleibt unverändert.")
        return 1  # Workflow schlägt fehl -> GitHub schickt Mail
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(atom(entries), encoding="utf-8")
    print(f"{len(entries)} Einträge geschrieben, neuester: {entries[0]['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
