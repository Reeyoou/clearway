"""Download the full Federal Register text for every NPRM in data/challenge4_ad_index.xlsx.

Why: the summaries are only ~150 pages, and serial-number / applicability rules live in the
full documents. The hackathon allows and encourages fetching them (public, no login).

Sources (both public, no key):
  1. federal_register_link  -> Federal Register API -> raw text (keeps [[Page N]] markers)
  2. no link (older rows)    -> govinfo link service by volume + first page (e.g. /link/fr/70/54316)

Run on a laptop with internet:
    python3 -m pip install pandas openpyxl certifi
    python3 fetch_fulltext.py              # everything not yet downloaded
    python3 fetch_fulltext.py --limit 40   # try a few first
    python3 -m adtrace.ingest              # rebuild the index with the full text
Files go to data/fulltext/<record_id>.txt and are reused, so the demo then runs offline.
"""
import argparse
import html
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
SHEET = ROOT / "data" / "challenge4_ad_index.xlsx"
OUT = ROOT / "data" / "fulltext"
UA = {"User-Agent": "ADTrace hackathon prototype (Autonomous SG 2026)"}


def _ssl_context():
    """python.org builds of Python on macOS ship without root certificates.
    Use certifi's bundle when available (pip install certifi), otherwise the system default."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


def get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</pre>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def from_federal_register(url):
    m = re.search(r"/documents/\d{4}/\d{2}/\d{2}/([0-9]{4}-[0-9]+)", url)
    if not m:
        raise ValueError("no document number in link")
    meta, _ = get(f"https://www.federalregister.gov/api/v1/documents/{m.group(1)}.json?fields[]=raw_text_url&fields[]=body_html_url")
    meta = json.loads(meta)
    if meta.get("raw_text_url"):
        txt, _ = get(meta["raw_text_url"])
        return html_to_text(txt)
    if meta.get("body_html_url"):
        txt, _ = get(meta["body_html_url"])
        return html_to_text(txt)
    raise ValueError("API returned no text URL")


def from_govinfo(citation, pages):
    vol = re.search(r"Volume (\d+)", str(citation), re.I)
    page = re.search(r"\d+", str(pages))
    if not vol or not page:
        raise ValueError("no volume/page in citation")
    txt, final = get(f"https://www.govinfo.gov/link/fr/{vol.group(1)}/{page.group(0)}?link-type=html")
    if "<html" in txt[:500].lower() or "<pre" in txt.lower():
        txt = html_to_text(txt)
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after this many downloads (0 = all)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_excel(SHEET, sheet_name="nprm_index", header=2).astype(object)
    ok = skip = fail = 0
    failures = []
    for _, r in df.iterrows():
        rid = str(r["record_id"]).strip()
        dest = OUT / f"{rid}.txt"
        if dest.exists() and dest.stat().st_size > 500:
            skip += 1
            continue
        if args.limit and ok >= args.limit:
            break
        link = str(r.get("federal_register_link") or "").strip()
        try:
            if re.match(r"https?://(www\.)?federalregister\.gov/documents/", link):
                text, src = from_federal_register(link), "federalregister.gov"
            else:
                text, src = from_govinfo(r.get("citation"), r.get("pages")), "govinfo.gov"
            if len(text) < 500 or not re.search(r"airworthiness|directive|withdraw", text, re.I):
                raise ValueError("downloaded text does not look like an AD document")
            dest.write_text(text, encoding="utf-8")
            ok += 1
            print(f"{rid}  {src:18s} {len(text):>8,} chars")
        except Exception as ex:
            fail += 1
            failures.append(f"{rid}: {ex}")
            print(f"{rid}  FAILED  {ex}")
        time.sleep(0.4)  # be polite to public servers
    print(f"\ndownloaded {ok}, already had {skip}, failed {fail}. Files in {OUT}")
    if failures:
        (OUT / "_failures.txt").write_text("\n".join(failures), encoding="utf-8")
        print(f"failures listed in {OUT / '_failures.txt'}")
    print("Next: python3 -m adtrace.ingest   (then restart the app)")


if __name__ == "__main__":
    sys.exit(main())
