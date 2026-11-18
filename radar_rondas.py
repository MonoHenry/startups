#!/usr/bin/env python3
"""
radar_rondas: a daily list of startups that just raised a post-seed round.

Pipeline (each step is one section below):
  1. fetch    - download every RSS/Atom feed (a plain HTTP GET)
  2. parse    - turn the XML into items: title, link, date, source
  3. extract  - keep headlines that announce a round; pull out company, round, amount
  4. filter   - post-seed = Series A or later, or an unlabeled round >= ~US$8M
  5. dedupe   - skip companies already listed in the last 90 days (data/companies.csv)
  6. write    - daily/YYYY-MM-DD.md + append rows to data/companies.csv

Standard library only, so it runs anywhere Python 3.9+ exists.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import html
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

FEEDS = {
    "Tech Funding News": "https://techfundingnews.com/feed/",
    "Tech.eu": "https://tech.eu/feed/",
    "TechCrunch": "https://techcrunch.com/category/venture/feed/",
    "Crunchbase News": "https://news.crunchbase.com/feed/",
    "Sifted": "https://sifted.eu/feed",
    "BetaKit": "https://betakit.com/feed/",
    "Startupi": "https://startupi.com.br/feed/",
    # These two sometimes block automated requests; a failure is logged, not fatal.
    "EU-Startups": "https://www.eu-startups.com/feed/",
    "FinSMEs": "https://www.finsmes.com/feed",
}

# A bare "$" means different currencies depending on who writes it.
SOURCE_DOLLAR = {"BetaKit": "CAD"}

# Rough rates, used ONLY to compare round sizes against the threshold.
USD_PER = {"USD": 1.0, "EUR": 1.1, "GBP": 1.3, "BRL": 0.18, "CAD": 0.72, "AUD": 0.65, "INR": 0.012}
SYMBOL = {"USD": "US$", "EUR": "€", "GBP": "£", "BRL": "R$", "CAD": "CA$", "AUD": "A$", "INR": "₹"}

# --------------------------------------------------------------------------- 1. fetch

USER_AGENT = "Mozilla/5.0 (compatible; radar-rondas/1.0)"


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

# --------------------------------------------------------------------------- 2. parse

ATOM = "{http://www.w3.org/2005/Atom}"


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("\xa0", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = s.strip()
    try:                                   # RSS: "Mon, 16 Nov 2026 10:00:00 +0000"
        return email.utils.parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        pass
    try:                                   # Atom: "2026-11-16T10:00:00Z"
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    root = ET.fromstring(xml_bytes.lstrip())
    items = []
    for node in root.iter("item"):                      # RSS 2.0
        items.append({
            "source": source,
            "title": clean_text(node.findtext("title")),
            "link": (node.findtext("link") or "").strip(),
            "date": parse_date(node.findtext("pubDate")),
        })
    for node in root.iter(f"{ATOM}entry"):              # Atom
        link = node.find(f"{ATOM}link")
        items.append({
            "source": source,
            "title": clean_text(node.findtext(f"{ATOM}title")),
            "link": link.get("href", "") if link is not None else "",
            "date": parse_date(node.findtext(f"{ATOM}published") or node.findtext(f"{ATOM}updated")),
        })
    return [i for i in items if i["title"]]

# --------------------------------------------------------------------------- 3. extract

VERBS = ["raises", "raised", "secures", "secured", "lands", "landed", "closes", "closed",
         "bags", "bagged", "nabs", "snags", "scores", "banks", "grabs", "gets", "receives",
         "attracts", "collects", "picks up", "pulls in", "announces", "completes",
         "capta", "captou", "levanta", "levantou", "recebe", "recebeu", "anuncia"]
VERB_RE = re.compile(r"\s(?:" + "|".join(re.escape(v) for v in VERBS) + r")\b", re.I)

NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?"
UNIT = r"billion|bilhões|bilhão|bn|bi|b|million|milhões|milhão|mln|mn|mi|m|thousand|mil|k"
MONEY_SYMBOL_FIRST = re.compile(
    rf"(?P<cur>US\$|CA\$|C\$|A\$|R\$|\$|€|£|₹)\s?(?P<num>{NUM})\s?(?P<unit>{UNIT})?(?!\w)", re.I)
MONEY_WORD_AFTER = re.compile(
    rf"(?<![\w$€£])(?P<num>{NUM})\s?(?P<unit>{UNIT})\s(?P<cur>euros?|eur|dollars?|usd|pounds?|gbp|reais|brl)\b", re.I)
VALUATION_AFTER = re.compile(r"^\s*(valuation|valued|market cap)", re.I)
VALUATION_BEFORE = re.compile(r"(valued at|valuation of|at an?)\s*$", re.I)

ROUND_RE = re.compile(
    r"\b(pre-?seed|pre-?s[ée]ries?\s+[a-h]|s[ée]ries?\s+[a-h]|seed|"
    r"growth(?:\s+|-)(?:round|equity|funding|stage)|bridge|extension)\b", re.I)

ROUNDUP_RE = re.compile(r"\b(recap|roundup|round-up|weekly|biggest|top \d+|this week|week's|"
                        r"mapped|to watch|newsletter|resumo|ranking)\b", re.I)
# "fund" as a noun means a VC fund; "to fund X" is a startup spending its round.
FUND_RE = re.compile(r"(?<!to )\b(funds?|fundo|fundos)\b|\b(vc firm|venture firm|limited partners|lps)\b", re.I)
INVESTOR_SUBJECT_RE = re.compile(r"\b(ventures|capital|partners|vc|backer|investors?|fund|"
                                 r"accelerator|aceleradora|gestora)\b", re.I)

PREFIX_RE = re.compile(r"^(exclusive|exclusivo|breaking|scoop|funding|update)\s*:\s*", re.I)
POSSESSIVE_RE = re.compile(r"(?:'s|s')\s+")
# Subject that IS a person ("Nubank Alum raises $14M for Palomma") -> company comes after "for".
PERSON_SUBJECT_RE = re.compile(r"\b(alum|alums|alumni|founders?|co-?founders?|ceo|cto|veteran|team)\s*$", re.I)
FOR_RE = re.compile(r"\b[Ff]or\s+([A-Z][^,]*?)(?=\s+[Tt]o\s|\s+[Aa]s\s|\s+[Ii]n\s|,|$)")

DESCRIPTORS = set("""
startup start-up startups scaleup scale-up spinout spin-out spinoff spin-off unicorn company firm
platform maker developer provider operator app marketplace ai saas fintech healthtech insurtech
proptech edtech legaltech regtech climatetech cleantech agtech agritech biotech medtech deeptech
defencetech defensetech foodtech govtech martech adtech spacetech robotics cybersecurity
alum alums founder founders cofounder co-founder ceo stealth backer
empresa plataforma
""".split())
DEMONYMS = set("""
american british english scottish irish welsh french german dutch belgian swiss austrian italian
spanish portuguese polish czech danish swedish norwegian finnish estonian latvian lithuanian
greek romanian bulgarian hungarian ukrainian israeli indian chinese japanese korean singaporean
australian canadian mexican brazilian argentine argentinian chilean colombian peruvian african
nigerian kenyan egyptian european nordic
brasileira brasileiro paulista paulistana mineira mineiro gaúcha gaúcho carioca catarinense paranaense
""".split())


def tail_name(phrase: str) -> str:
    """Company = the last words of the subject, after stripping descriptors.
    'Leeds AI spinout adsilico' -> 'adsilico'; "London's Model ML" -> 'Model ML'."""
    phrase = phrase.split(",")[0]
    phrase = POSSESSIVE_RE.split(phrase)[-1]
    picked: list[str] = []
    for tok in reversed(phrase.split()):
        bare = tok.strip("\"'()[]").lower()
        is_descriptor = (bare in DESCRIPTORS or bare in DEMONYMS
                         or re.search(r"-(based|backed|led|founded|born|focused)$", bare))
        if is_descriptor and picked:
            break
        picked.append(tok)
        if len(picked) == 4:
            break
    return " ".join(reversed(picked)).strip("\"'()[]:,. ")


def leading_name(title: str) -> str:
    """Fallback for headlines without a funding verb: 'Corma hits $1B ...' -> 'Corma'."""
    picked = []
    for tok in title.split():
        if not (tok[0].isupper() or tok[0].isdigit()) or len(picked) == 3:
            break
        picked.append(tok)
    return " ".join(picked).strip("\"'()[]:,. ")


def to_float(num: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", num):   # 1,500,000
        return float(num.replace(",", ""))
    return float(num.replace(",", "."))                        # 2.66 or 1,5 (pt-BR)


def unit_to_millions(unit: str | None) -> float:
    u = (unit or "").lower()
    if u in ("billion", "bilhões", "bilhão", "bn", "bi", "b"):
        return 1000.0
    if u in ("million", "milhões", "milhão", "mln", "mn", "mi", "m"):
        return 1.0
    if u in ("thousand", "mil", "k"):
        return 0.001
    return 1e-6                                                 # bare number of units


def currency_code(cur: str, dollar_ccy: str) -> str:
    c = cur.lower()
    if c in ("$",):
        return dollar_ccy
    if c in ("us$", "usd") or c.startswith("dollar"):
        return "USD"
    if c in ("€", "eur") or c.startswith("euro"):
        return "EUR"
    if c in ("£", "gbp") or c.startswith("pound"):
        return "GBP"
    if c in ("r$", "brl", "reais"):
        return "BRL"
    if c in ("ca$", "c$"):
        return "CAD"
    if c == "a$":
        return "AUD"
    return "INR"


def format_amount(value_m: float, ccy: str) -> str:
    sym = SYMBOL[ccy]
    if value_m >= 1000:
        return f"{sym}{value_m / 1000:.3g}B"
    if value_m >= 1:
        return f"{sym}{value_m:.3g}M"
    return f"{sym}{value_m * 1000:.3g}K"


def pick_money(text: str, start: int, dollar_ccy: str) -> dict | None:
    matches = sorted(list(MONEY_SYMBOL_FIRST.finditer(text, start)) + list(MONEY_WORD_AFTER.finditer(text, start)),
                     key=lambda m: m.start())
    for m in matches:
        if VALUATION_AFTER.match(text[m.end():]) or VALUATION_BEFORE.search(text[max(0, m.start() - 14):m.start()]):
            continue                                            # "$1B valuation" is not the round
        value_m = to_float(m.group("num")) * unit_to_millions(m.group("unit"))
        ccy = currency_code(m.group("cur"), dollar_ccy)
        return {"amount": format_amount(value_m, ccy), "amount_usd_m": round(value_m * USD_PER[ccy], 2)}
    return None


def normalize_round(raw: str) -> str:
    r = raw.lower().replace("é", "e")
    if r.startswith("pre") and "seed" in r:
        return "Pre-seed"
    m = re.match(r"pre-?series?\s+([a-h])", r)
    if m:
        return f"Pre-Series {m.group(1).upper()}"
    m = re.match(r"series?\s+([a-h])", r)
    if m:
        return f"Series {m.group(1).upper()}"
    if r == "seed":
        return "Seed"
    if r.startswith("growth"):
        return "Growth"
    return "Bridge" if r.startswith("bridge") else "Extension"


def extract(item: dict, dollar_ccy: str = "USD") -> dict | None:
    title = item["title"]
    if ROUNDUP_RE.search(title) or FUND_RE.search(title):
        return None
    t = PREFIX_RE.sub("", title)
    verb = VERB_RE.search(t)
    rnd = ROUND_RE.search(t)
    money = pick_money(t, verb.end() if verb else 0, dollar_ccy)

    if verb:
        if not (money or rnd):
            return None
        subject = t[:verb.start()].strip()
        if not subject or INVESTOR_SUBJECT_RE.search(subject):
            return None
        company = ""
        if PERSON_SUBJECT_RE.search(subject):
            m = FOR_RE.search(t, verb.end())
            if m:
                company = tail_name(m.group(1))
        company = company or tail_name(subject)
    else:
        if not (money and rnd):
            return None
        company = leading_name(t)

    if not company:
        return None
    return {
        "company": company,
        "round": normalize_round(rnd.group(1)) if rnd else "",
        "amount": money["amount"] if money else "",
        "amount_usd_m": money["amount_usd_m"] if money else None,
        "source": item["source"],
        "title": title,
        "link": item["link"],
    }

# --------------------------------------------------------------------------- 4. filter


def stage_rank(label: str) -> float | None:
    if label.startswith("Series "):
        return 2 + "ABCDEFGH".index(label[-1])
    if label.startswith("Pre-Series "):
        return 1.5 + "ABCDEFGH".index(label[-1])
    return {"Pre-seed": 0, "Seed": 1, "Growth": 9}.get(label)   # Bridge/Extension/"" -> unknown


def is_post_seed(rec: dict, min_usd_m: float, include_seed: bool) -> bool:
    rank = stage_rank(rec["round"])
    if rank is not None:
        return rank >= (1 if include_seed else 2)
    return rec["amount_usd_m"] is not None and rec["amount_usd_m"] >= min_usd_m

# --------------------------------------------------------------------------- 5. dedupe

CSV_FIELDS = ["found_on", "company", "round", "amount", "amount_usd_m", "post_seed", "source", "title", "link"]


def company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_seen(csv_path: Path, today: dt.date, window_days: int = 90) -> set[str]:
    if not csv_path.exists():
        return set()
    cutoff = today - dt.timedelta(days=window_days)
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {company_key(row["company"]) for row in csv.DictReader(f)
                if dt.date.fromisoformat(row["found_on"]) >= cutoff}


def merge_batch(recs: list[dict]) -> dict[str, dict]:
    """Same round covered by several outlets -> one row, all sources listed."""
    merged: dict[str, dict] = {}
    for r in recs:
        k = company_key(r["company"])
        if k not in merged:
            merged[k] = dict(r)
            continue
        m = merged[k]
        if r["source"] not in m["source"].split(", "):
            m["source"] += ", " + r["source"]
        for field in ("round", "amount", "amount_usd_m"):
            if not m[field] and r[field]:
                m[field] = r[field]
    return merged

# --------------------------------------------------------------------------- 6. write


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def linkedin_search(company: str) -> str:
    return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode({"keywords": f"{company} founder"})


def render_markdown(today: dt.date, post: list[dict], other: list[dict], errors: list[str],
                    min_usd_m: float, include_seed: bool) -> str:
    rule = "Seed en adelante" if include_seed else "Series A en adelante"
    lines = [f"# Rondas post-seed — {today.isoformat()}", "",
             f"{len(post)} empresas nuevas. Criterio: {rule}, o ronda sin fase indicada de al menos ~US${min_usd_m:g}M.", ""]
    if post:
        lines += ["| Empresa | Ronda | Cantidad | Fuente | Fundadores |", "|---|---|---|---|---|"]
        for r in post:
            lines.append(f"| [{md_escape(r['company'])}]({r['link']}) | {r['round'] or '¿?'} | {r['amount'] or '¿?'} "
                         f"| {md_escape(r['source'])} | [LinkedIn]({linkedin_search(r['company'])}) |")
    else:
        lines.append("_Hoy no hay rondas nuevas que cumplan el criterio._")
    if other:
        lines += ["", f"<details><summary>Otras rondas detectadas (seed o sin datos suficientes): {len(other)}</summary>", ""]
        lines += [f"- **{md_escape(r['company'])}** — {r['round'] or '¿?'} — {r['amount'] or '¿?'} — "
                  f"[{md_escape(r['title'])}]({r['link']})" for r in other]
        lines += ["", "</details>"]
    if errors:
        lines += ["", "**Feeds que fallaron hoy:**", ""] + [f"- {e}" for e in errors]
    return "\n".join(lines) + "\n"


def append_csv(csv_path: Path, today: dt.date, rows: list[tuple[dict, bool]]) -> None:
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        for r, post in rows:
            w.writerow({**{k: r.get(k, "") for k in CSV_FIELDS}, "found_on": today.isoformat(),
                        "post_seed": "yes" if post else "no",
                        "amount_usd_m": "" if r["amount_usd_m"] is None else r["amount_usd_m"]})

# --------------------------------------------------------------------------- main


def run(out_dir: Path, today: dt.date, days: int = 2, min_usd_m: float = 8.0, include_seed: bool = False,
        feeds: dict[str, str] = FEEDS, fetcher=fetch, dry_run: bool = False) -> dict:
    items, errors = [], []
    for source, url in feeds.items():
        try:
            items += parse_feed(fetcher(url), source)
        except Exception as e:                               # one broken feed must not kill the run
            errors.append(f"{source}: {type(e).__name__}: {e}")

    cutoff = today - dt.timedelta(days=days)
    recs = []
    for it in items:
        if it["date"] and it["date"] < cutoff:
            continue
        r = extract(it, SOURCE_DOLLAR.get(it["source"], "USD"))
        if r:
            recs.append(r)

    csv_path = out_dir / "data" / "companies.csv"
    seen = load_seen(csv_path, today)
    post, other = [], []
    for k, r in merge_batch(recs).items():
        if k in seen:
            continue
        (post if is_post_seed(r, min_usd_m, include_seed) else other).append(r)
    post.sort(key=lambda r: -(r["amount_usd_m"] or 0))

    md = render_markdown(today, post, other, errors, min_usd_m, include_seed)
    if not dry_run:
        (out_dir / "daily").mkdir(parents=True, exist_ok=True)
        (out_dir / "data").mkdir(parents=True, exist_ok=True)
        (out_dir / "daily" / f"{today.isoformat()}.md").write_text(md, encoding="utf-8")
        append_csv(csv_path, today, [(r, True) for r in post] + [(r, False) for r in other])
    return {"items": len(items), "post": post, "other": other, "errors": errors, "markdown": md}


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily list of startups that just raised post-seed rounds.")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent, help="project folder (default: next to this script)")
    ap.add_argument("--days", type=int, default=2, help="ignore articles older than N days (default 2)")
    ap.add_argument("--min-usd", type=float, default=8.0, help="threshold in US$ millions for rounds with no stage label (default 8)")
    ap.add_argument("--include-seed", action="store_true", help="also count Seed / Pre-Series A as post-seed")
    ap.add_argument("--dry-run", action="store_true", help="print the list, write nothing")
    args = ap.parse_args()

    today = dt.datetime.now(dt.timezone.utc).date()
    res = run(args.out, today, args.days, args.min_usd, args.include_seed, dry_run=args.dry_run)
    print(res["markdown"] if args.dry_run else
          f"{res['items']} headlines read, {len(res['post'])} post-seed, {len(res['other'])} other, {len(res['errors'])} feed errors")
    for e in res["errors"]:
        print("  feed error:", e, file=sys.stderr)
    return 1 if len(res["errors"]) == len(FEEDS) else 0   # red X in GitHub only if every feed failed


if __name__ == "__main__":
    sys.exit(main())
