#!/usr/bin/env python3
"""
La Matinale — quotidien refresh.

Reads the existing data.json, fetches fresh content from RSS + market feeds,
optionally runs an editorial pass via the Anthropic API, then writes the
updated data.json back to the repo root.

Designed to be run by a GitHub Actions cron at 07:00 Europe/London every day.

Resilience:
  - Each section is wrapped in try/except: a failing source preserves the
    previous day's content for that section rather than wiping it.
  - The script exits 0 even on partial failure, so the workflow stays green.
"""

from __future__ import annotations
import os
import sys
import json
import time
import html
import datetime as dt
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import feedparser

# ──────────────────────────────────────────────────────────────
# Paths & TZ guard
# ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data.json"
LDN = ZoneInfo("Europe/London")

TARGET_HOUR = int(os.environ.get("TARGET_HOUR", "7"))
SKIP_GUARD = os.environ.get("SKIP_HOUR_CHECK") == "1"


def log(msg: str) -> None:
    print(f"[matinale] {msg}", flush=True)


def guard_hour() -> None:
    """Only proceed if the current London hour matches TARGET_HOUR.
    The GitHub workflow schedules two UTC slots (06:00 and 07:00) so that
    one of them lands on 07:00 London regardless of BST/GMT — this guard
    silently skips the other one."""
    now = dt.datetime.now(LDN)
    if SKIP_GUARD:
        log(f"Guard skipped (now London = {now:%H:%M %Z}).")
        return
    if now.hour != TARGET_HOUR:
        log(f"Not {TARGET_HOUR}h London (now {now.hour}h). Exiting cleanly.")
        sys.exit(0)
    log(f"London time {now:%H:%M %Z} — running.")


# ──────────────────────────────────────────────────────────────
# RSS sources
# Edit this dict to add / remove / replace feeds. Each item lists:
#   source_label, url, region tag.
# ──────────────────────────────────────────────────────────────
SOURCES = {
    "france": [
        ("Le Monde",   "https://www.lemonde.fr/economie/rss_full.xml"),
        ("Les Échos",  "https://syndication.lesechos.fr/rss/rss_economie-france.xml"),
        ("France Info","https://www.francetvinfo.fr/economie.rss"),
    ],
    "uk": [
        ("BBC Business",  "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Guardian UK",   "https://www.theguardian.com/uk-news/rss"),
        ("Sky News Biz",  "https://feeds.skynews.com/feeds/rss/business.xml"),
    ],
    "world": [
        ("Le Monde Intl", "https://www.lemonde.fr/international/rss_full.xml"),
        ("Reuters World", "https://feeds.reuters.com/Reuters/worldNews"),
    ],
    "markets": [
        ("Reuters Markets", "https://feeds.reuters.com/reuters/businessNews"),
        ("FT Markets",      "https://www.ft.com/markets?format=rss"),
    ],
    "music_industry": [
        ("Music Business Worldwide", "https://www.musicbusinessworldwide.com/feed/"),
        ("Music Ally",               "https://musically.com/feed/"),
    ],
    "music_atelier": [
        ("Synthtopia",          "https://www.synthtopia.com/content/feed/"),
        ("KVR Audio",           "https://www.kvraudio.com/news.rss"),
        ("Bedroom Producers",   "https://bedroomproducersblog.com/feed/"),
    ],
    "ps5": [
        ("Push Square",  "https://www.pushsquare.com/feeds/latest"),
        ("Eurogamer",    "https://www.eurogamer.net/feed"),
    ],
}


def fetch_feed(label: str, url: str, limit: int = 8) -> list[dict[str, Any]]:
    """Fetch one RSS feed and return a normalised item list."""
    try:
        parsed = feedparser.parse(url)
    except Exception as e:
        log(f"  ! {label}: parse failed ({e})")
        return []
    items = []
    for entry in parsed.entries[:limit]:
        items.append({
            "source": label,
            "title":   entry.get("title", "").strip(),
            "link":    entry.get("link", ""),
            "summary": html.unescape(entry.get("summary", ""))[:600],
            "published": entry.get("published", ""),
        })
    log(f"  · {label}: {len(items)} items")
    return items


def fetch_region(region: str) -> list[dict[str, Any]]:
    out = []
    for label, url in SOURCES.get(region, []):
        out.extend(fetch_feed(label, url))
    return out


# ──────────────────────────────────────────────────────────────
# Markets — yfinance for indices and movers (free, no API key)
# ──────────────────────────────────────────────────────────────
INDEX_TICKERS = [
    ("CAC 40",       "^FCHI"),
    ("FTSE 100",     "^FTSE"),
    ("EuroStoxx 50", "^STOXX50E"),
    ("DAX",          "^GDAXI"),
    ("S&P 500",      "^GSPC"),
    ("Nikkei 225",   "^N225"),
]

# Watchlist used to derive the "Principaux mouvements" panel.
# Adjust to your taste — keep ~10 to get 6 movers.
MOVERS_WATCHLIST = [
    ("Rheinmetall", "RHM.DE"),
    ("ASML",        "ASML.AS"),
    ("Stellantis",  "STLAM.MI"),
    ("Burberry",    "BRBY.L"),
    ("Verallia",    "VRLA.PA"),
    ("BT Group",    "BT-A.L"),
    ("LVMH",        "MC.PA"),
    ("BP",          "BP.L"),
    ("Schroders",   "SDR.L"),
    ("TotalEnergies","TTE.PA"),
]


def fmt_index_val(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def fmt_chg(pct: float) -> str:
    sign = "+" if pct >= 0 else "−"
    return f"{sign}{abs(pct):.2f} %"


def fetch_markets_snapshot() -> list[dict[str, Any]]:
    try:
        import yfinance as yf
    except Exception as e:
        log(f"  ! yfinance import failed: {e}")
        return []
    out = []
    for name, tk in INDEX_TICKERS:
        try:
            t = yf.Ticker(tk)
            hist = t.history(period="2d", auto_adjust=False)
            if len(hist) < 2:
                continue
            close_now = float(hist["Close"].iloc[-1])
            close_prev = float(hist["Close"].iloc[-2])
            pct = (close_now - close_prev) / close_prev * 100
            out.append({
                "name": name,
                "val":  fmt_index_val(close_now),
                "chg":  fmt_chg(pct),
                "dir":  "up" if pct >= 0 else "down",
            })
            log(f"  · {name}: {close_now:.2f} ({pct:+.2f}%)")
        except Exception as e:
            log(f"  ! {name}: {e}")
    return out


def fetch_movers() -> list[dict[str, Any]]:
    try:
        import yfinance as yf
    except Exception:
        return []
    quotes = []
    for name, tk in MOVERS_WATCHLIST:
        try:
            t = yf.Ticker(tk)
            hist = t.history(period="2d", auto_adjust=False)
            if len(hist) < 2:
                continue
            close_now = float(hist["Close"].iloc[-1])
            close_prev = float(hist["Close"].iloc[-2])
            pct = (close_now - close_prev) / close_prev * 100
            currency = "€" if tk.endswith((".DE", ".PA", ".MI", ".AS")) else "p" if tk.endswith(".L") else "$"
            price = f"{close_now:,.2f} {currency}".replace(",", " ")
            if currency == "p":  # London prices are in pence
                price = f"{close_now:,.1f} p".replace(",", " ")
            quotes.append({
                "name": name, "ticker": tk.split(".")[0],
                "price": price,
                "chg":   fmt_chg(pct),
                "dir":   "up" if pct >= 0 else "down",
                "pct":   pct,
            })
        except Exception as e:
            log(f"  ! mover {name}: {e}")
    # Top 6 by |%| movement
    quotes.sort(key=lambda q: abs(q["pct"]), reverse=True)
    for q in quotes:
        q.pop("pct", None)
    return quotes[:6]


# ──────────────────────────────────────────────────────────────
# Editorial pass via Claude (optional)
# Requires ANTHROPIC_API_KEY. Falls back to raw RSS shaping if absent.
# ──────────────────────────────────────────────────────────────
EDITORIAL_SYSTEM = """Tu es rédacteur en chef d'un journal du matin numérique fait sur mesure pour un unique lecteur :
— Français, vivant à Londres
— Producteur de musique professionnel
— Lecteur sophistiqué qui veut comprendre ce qui structure la journée

Tu hérites de la rigueur éditoriale du Financial Times, du Wall Street Journal et du Monde.

Règles de curation strictes :
1. Privilégier l'économique, le politique, la régulation, la géopolitique, la défense, l'énergie, la macroéconomie, l'industriel.
2. EXCLURE absolument les faits divers, célébrités, anecdotes locales, drames sensationnels sans portée structurelle.
3. Pour chaque item retenu, écrire une ligne "pourquoi ça compte" — italique, 1 à 2 phrases, jamais alarmiste.
4. Hiérarchiser. Maximum 3 articles par section régionale, le premier en "large".
5. Reformuler le titre si nécessaire pour qu'il soit informatif et net (pas de clickbait).
6. Ton : sobre, intelligent, informé. Jamais d'emoji, jamais de superlatifs creux.

Tu réponds STRICTEMENT en JSON valide, sans préambule ni explication."""


def claude_editorialise(region_label: str, raw_items: list[dict], n: int = 3) -> list[dict] | None:
    """Run the editorial pass for one region. Returns None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not raw_items:
        return None
    try:
        from anthropic import Anthropic
    except Exception as e:
        log(f"  ! anthropic import failed: {e}")
        return None

    # Trim payload to keep tokens reasonable
    trimmed = [{
        "source": i["source"],
        "title":  i["title"],
        "summary": i["summary"][:400],
        "link":   i["link"],
    } for i in raw_items[:24]]

    user = f"""Voici une liste brute d'articles RSS pour la rubrique « {region_label} » de l'édition de ce matin.

Sélectionne les {n} articles les plus importants selon les règles ci-dessus.
Pour chaque article retenu, produis un objet JSON avec exactement ces champs :
  - source       (string, le label du média)
  - time         (string, ex. "il y a 2 h" — choisis librement, plausible)
  - headline     (string, le titre reformulé si nécessaire — clair, informatif)
  - summary      (string, 1 à 2 phrases, contextualisé)
  - why          (string, 1 à 2 phrases italiques — pourquoi cela compte)
  - large        (boolean — true pour le premier seulement, sinon false)
  - extra: {{ body: [string, string], link: string }}    (deux paragraphes développant l'article, et le lien original)

Réponds par un tableau JSON pur de {n} objets, RIEN d'autre.

Articles disponibles :
{json.dumps(trimmed, ensure_ascii=False, indent=2)}"""

    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=EDITORIAL_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text.strip()
        # Strip code fences if Claude added them
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        stories = json.loads(text)
        log(f"  ✓ Claude pass for {region_label}: {len(stories)} stories")
        return stories
    except Exception as e:
        log(f"  ! Claude pass failed for {region_label}: {e}")
        return None


def claude_world_scan(raw_items: list[dict], n: int = 5) -> list[dict] | None:
    """Compact world digest — 5 items, each {region, head, sub}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not raw_items:
        return None
    try:
        from anthropic import Anthropic
    except Exception:
        return None
    trimmed = [{
        "source": i["source"], "title": i["title"], "summary": i["summary"][:300],
    } for i in raw_items[:20]]
    user = f"""Pour la rubrique « Tour du monde » de l'édition matinale, sélectionne {n} dépêches internationales les plus importantes.
Tu réponds par un tableau JSON de {n} objets avec exactement :
  - region (string, ville/capitale concernée — ex. "Washington", "Pékin")
  - head   (string, titre court et informatif)
  - sub    (string, 1 phrase italique de contexte)

RIEN d'autre que le JSON.

Articles :
{json.dumps(trimmed, ensure_ascii=False, indent=2)}"""
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=EDITORIAL_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"): text = text[4:]
            if text.endswith("```"):    text = text[:-3]
        return json.loads(text.strip())
    except Exception as e:
        log(f"  ! Claude world scan failed: {e}")
        return None


def claude_lede(all_morning_items: list[dict]) -> dict | None:
    """Generate the morning editorial lede + 3 numbered briefing facts."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not all_morning_items:
        return None
    try:
        from anthropic import Anthropic
    except Exception:
        return None
    trimmed = [{"source": i["source"], "title": i["title"]} for i in all_morning_items[:40]]
    user = f"""À partir des titres ci-dessous, rédige le lede éditorial du matin (style FT/Le Monde, sobre, élégant) :

Réponds en JSON strict avec :
  - headline_html (string, le grand titre du lede — autorise <em>...</em> pour souligner un mot-clé)
  - body         (tableau de 2 paragraphes — chacun 2-3 phrases — qui posent le matin)
  - briefing     (tableau de 3 phrases courtes — les trois faits qui structurent la journée)

RIEN d'autre que le JSON.

Titres :
{json.dumps(trimmed, ensure_ascii=False, indent=2)}"""
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=EDITORIAL_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"): text = text[4:]
            if text.endswith("```"):    text = text[:-3]
        return json.loads(text.strip())
    except Exception as e:
        log(f"  ! Claude lede failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# Fallback shaping when no API key is set
# Just maps raw RSS to the data shape, with no curation.
# ──────────────────────────────────────────────────────────────
def raw_shape(items: list[dict], n: int = 3) -> list[dict]:
    out = []
    for idx, it in enumerate(items[:n]):
        out.append({
            "source":  it["source"],
            "time":    "ce matin",
            "headline": it["title"],
            "summary": (it["summary"] or "")[:280],
            "why":     "",
            "large":   idx == 0,
            "extra":   {"body": [it["summary"] or ""], "link": it["link"]},
        })
    return out


def raw_world(items: list[dict], n: int = 5) -> list[dict]:
    out = []
    for it in items[:n]:
        out.append({"region": it["source"], "head": it["title"], "sub": (it["summary"] or "")[:140]})
    return out


# ──────────────────────────────────────────────────────────────
# Music atelier — categorisation by simple keyword heuristics
# ──────────────────────────────────────────────────────────────
ATELIER_BUCKETS = {
    "plugins":     ["plugin", "vst", "au ", "effect", "compressor", "eq ", "reverb", "delay", "saturation", "ozone", "fabfilter", "izotope", "soundtoys"],
    "synths_midi": ["synth", "synthesizer", "synthé", "keyboard", "midi", "controller", "contrôleur", "korg", "moog", "sequential", "arturia", "roli"],
    "daw":         ["ableton", "logic pro", "bitwig", "pro tools", "studio one", "fl studio", "cubase", "daw "],
    "ai_tools":    ["ai ", "ia ", "machine learning", "neural", "suno", "udio", "stable audio"],
    "libraries":   ["sample", "library", "loops", "pack", "spitfire", "kontakt", "preset"],
}


def categorise_atelier(items: list[dict]) -> dict[str, list[dict]]:
    buckets = {k: [] for k in ATELIER_BUCKETS}
    for it in items:
        text = (it["title"] + " " + it["summary"]).lower()
        for bucket, kws in ATELIER_BUCKETS.items():
            if any(kw in text for kw in kws):
                buckets[bucket].append(it)
                break
    return buckets


def shape_tool(it: dict, tag: str = "Nouveauté") -> dict:
    return {
        "name": it["title"][:80],
        "tag":  tag,
        "tagClass": "",
        "desc": (it["summary"] or "")[:240],
        "meta": [it["source"]],
        "extra": {"body": [(it["summary"] or "")[:600]], "link": it["link"]},
    }


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
def main() -> int:
    guard_hour()

    # Load previous data — used as fallback per-section if a fetch fails
    if DATA_PATH.exists():
        prev = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    else:
        log("data.json missing — refusing to bootstrap. Commit a seed first.")
        return 1

    new = json.loads(json.dumps(prev))  # deep copy

    # 1) Masthead — date + edition number ----------------------------------
    today_ldn = dt.datetime.now(LDN).date()
    epoch = dt.date(2026, 1, 1)
    edition = (today_ldn - epoch).days + 1
    new["masthead"] = {
        "date_iso":  today_ldn.isoformat(),
        "date_long": today_ldn.strftime("%A %d %B %Y").replace("January","janvier")
                        .replace("February","février").replace("March","mars")
                        .replace("April","avril").replace("May","mai").replace("June","juin")
                        .replace("July","juillet").replace("August","août")
                        .replace("September","septembre").replace("October","octobre")
                        .replace("November","novembre").replace("December","décembre")
                        .replace("Monday","Lundi").replace("Tuesday","Mardi")
                        .replace("Wednesday","Mercredi").replace("Thursday","Jeudi")
                        .replace("Friday","Vendredi").replace("Saturday","Samedi")
                        .replace("Sunday","Dimanche").lstrip("0"),
        "edition":   f"N° {edition}",
        "place":     "Londres / Paris",
    }
    log(f"Edition {new['masthead']['edition']} — {new['masthead']['date_long']}")

    # 2) News by region -----------------------------------------------------
    log("Fetching France RSS…")
    fr_raw = fetch_region("france")
    log("Fetching UK RSS…")
    uk_raw = fetch_region("uk")
    log("Fetching World RSS…")
    world_raw = fetch_region("world")

    fr_stories = claude_editorialise("France",        fr_raw, n=3) or raw_shape(fr_raw)
    uk_stories = claude_editorialise("Royaume-Uni",   uk_raw, n=3) or raw_shape(uk_raw)
    if fr_stories: new["region_france"] = fr_stories
    if uk_stories: new["region_uk"]     = uk_stories

    world_scan = claude_world_scan(world_raw, n=5) or raw_world(world_raw, n=5)
    if world_scan: new["world"] = world_scan

    # 3) Lede + briefing ----------------------------------------------------
    lede = claude_lede(fr_raw + uk_raw + world_raw)
    if lede: new["impact_lede"] = lede

    # 4) Markets ------------------------------------------------------------
    log("Fetching market snapshot…")
    snapshot = fetch_markets_snapshot()
    if snapshot: new["market_snapshot"] = snapshot

    log("Fetching movers…")
    movers = fetch_movers()
    if movers: new["movers"] = movers

    # Markets stories (editorial)
    log("Fetching markets RSS…")
    mkt_raw = fetch_region("markets")
    mkt_stories = claude_editorialise("Marchés", mkt_raw, n=3) or raw_shape(mkt_raw)
    if mkt_stories: new["markets_stories"] = mkt_stories

    # NOTE: `ratings` and `watch` stay as previous (mock) values until
    # wired to a sell-side data provider. Replace this block when ready.
    # SOURCE: Bloomberg/Refinitiv/Visible Alpha for analyst rating changes.

    # 5) Music industry + atelier -------------------------------------------
    log("Fetching music industry RSS…")
    ind_raw = fetch_region("music_industry")
    ind_stories = claude_editorialise("Industrie musicale", ind_raw, n=4) or raw_shape(ind_raw, n=4)
    if ind_stories: new["industry"] = ind_stories

    log("Fetching atelier RSS…")
    atelier_raw = fetch_region("music_atelier")
    buckets = categorise_atelier(atelier_raw)
    if any(buckets.values()):
        # Keep the same shape as the seed; only overwrite buckets we have content for
        for bucket, items in buckets.items():
            if items:
                new["atelier"][bucket] = [shape_tool(i) for i in items[:4]]

    # 6) PS5 ----------------------------------------------------------------
    log("Fetching PS5 RSS…")
    ps5_raw = fetch_region("ps5")
    ps5_stories = claude_editorialise("PS5 / jeu vidéo", ps5_raw, n=5) or raw_shape(ps5_raw, n=5)
    if ps5_stories:
        new["ps5"] = {"lead": ps5_stories[0], "items": ps5_stories[1:]}

    # 7) Write --------------------------------------------------------------
    DATA_PATH.write_text(
        json.dumps(new, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Wrote {DATA_PATH.relative_to(ROOT)} ({DATA_PATH.stat().st_size // 1024} KB).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
