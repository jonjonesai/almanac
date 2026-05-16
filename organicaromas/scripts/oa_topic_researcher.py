#!/usr/bin/env python3
"""OA topic researcher — seeds Airtable Social Queue with real essential-oil research.

Modeled on `welovehoroscope/scripts/wlh_topic_researcher.py`. Where WLH pulls
from skyfield ephemeris (structured astronomical data), OA pulls from PubMed
NCBI E-utilities (structured biomedical research) + Reddit r/aromatherapy
trending threads (structured community signal).

Output: a small number of high-rigor topics per week, each anchored on a real
published study or a high-engagement Reddit discussion, with `verified_facts`
embedded so `sp2_pipeline.gen_script_plan` can quote literal values (study
counts, participant numbers, effect sizes, compound names) without drift.

Usage:
    oa_topic_researcher.py [--dryrun] [--max-topics 2] [--reddit] [--no-pubmed]

Requires:
    python3 (with required deps) (claude --print available)
    <repo-root>/.env (or `$SP2_ENV_PATH`) (AIRTABLE_API_KEY, optional NCBI_API_KEY)

Brand-rule alignment with OA (per brand.json):
    * No medical claims — preserve study-language exactly ("improved sleep
      quality"), never paraphrase to "treats" / "cures."
    * Sensory + scientific language; calm contemplation; precise.
    * Script tells the story, never sells the product (warm-hug doctrine,
      universal SP2 rule locked 2026-05-15).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENV_PATH = Path(os.environ.get("SP2_ENV_PATH", str(Path(__file__).resolve().parent.parent.parent / ".env")))
OA_AIRTABLE_BASE = "appDz92uv0ZnWNcdd"
OA_AIRTABLE_TABLE = "tblni5LeNkLXBLaPz"
OA_BRAND_DIR = Path(__file__).resolve().parent.parent
LEDGER = Path(os.environ.get("SP2_HEARTBEAT_DIR", str(Path(__file__).resolve().parent.parent.parent / "heartbeat"))) / "oa_topic_researcher_ledger.md"

VERIFIED_FACTS_OPEN = "[VERIFIED_FACTS_JSON_START]"
VERIFIED_FACTS_CLOSE = "[VERIFIED_FACTS_JSON_END]"

# Study-type rarity / editorial weight. Higher = better fit for "warm hug science"
# content. Meta-analyses pool many trials so they're the strongest single-study
# claim. Recent RCTs are second. Reviews are third.
STUDY_TYPE_WEIGHT = {
    "meta-analysis": 100,
    "systematic review": 95,
    "randomized controlled trial": 80,
    "double-blind": 75,
    "clinical trial": 60,
    "review": 50,
    "pilot study": 40,
}

# Core PubMed query — last 18 months, essential-oil-or-aromatherapy crossed with
# sleep/anxiety/stress, study-design filtered. The compound list mirrors what's
# editorially-on-brand for OA's wellness-meets-neuroscience voice.
PUBMED_QUERY_TEMPLATE = (
    '(("aromatherapy"[MeSH] OR "essential oil"[Title/Abstract] OR '
    '"linalool"[Title/Abstract] OR "lavender"[Title/Abstract] OR '
    '"sandalwood"[Title/Abstract] OR "frankincense"[Title/Abstract] OR '
    '"bergamot"[Title/Abstract] OR "ylang"[Title/Abstract]) AND '
    '("sleep"[Title/Abstract] OR "insomnia"[Title/Abstract] OR '
    '"anxiety"[Title/Abstract] OR "stress"[Title/Abstract] OR '
    '"depression"[Title/Abstract] OR "cognition"[Title/Abstract])) AND '
    '("randomized controlled trial"[Publication Type] OR '
    '"meta-analysis"[Publication Type] OR '
    '"systematic review"[Publication Type]) AND '
    '("{start}"[PDAT] : "{end}"[PDAT])'
)

REDDIT_SUBREDDITS = ["aromatherapy", "essentialoils"]
REDDIT_USER_AGENT = "oa-topic-researcher/1.0 (by /u/organicaromas)"


def load_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def http_json(url: str, method="GET", headers=None, body=None, timeout=30):
    if isinstance(body, str):
        body = body.encode()
    h = {"User-Agent": REDDIT_USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def http_text(url: str, timeout=30) -> str:
    h = {"User-Agent": REDDIT_USER_AGENT}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------- PubMed

def pubmed_search(months_back: int = 18, max_results: int = 20, api_key: str | None = None) -> list[str]:
    """Return PMIDs of relevant recent essential-oil studies."""
    end = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    start = (datetime.now(timezone.utc) - timedelta(days=30 * months_back)).strftime("%Y/%m/%d")
    term = PUBMED_QUERY_TEMPLATE.format(start=start, end=end)
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": max_results,
        "sort": "pub_date",
        "retmode": "json",
    }
    if api_key:
        params["api_key"] = api_key
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{urllib.parse.urlencode(params)}"
    data = http_json(url)
    return (data or {}).get("esearchresult", {}).get("idlist", [])


def pubmed_fetch_abstracts(pmids: list[str], api_key: str | None = None) -> dict[str, dict]:
    """Fetch full abstracts (XML) for PMIDs. Returns {pmid: {title, abstract, year, journal, study_types, ...}}."""
    if not pmids:
        return {}
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    if api_key:
        params["api_key"] = api_key
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{urllib.parse.urlencode(params)}"
    xml = http_text(url, timeout=60)
    root = ET.fromstring(xml)
    out = {}
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text.strip()
        title_el = article.find(".//ArticleTitle")
        abstract_parts = []
        for ab in article.findall(".//Abstract/AbstractText"):
            label = ab.get("Label", "")
            text = "".join(ab.itertext()).strip()
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
        journal_el = article.find(".//Journal/Title")
        year_el = article.find(".//JournalIssue/PubDate/Year")
        if year_el is None:
            year_el = article.find(".//ArticleDate/Year")
        pub_types = [pt.text.strip() for pt in article.findall(".//PublicationType") if pt.text]
        out[pmid] = {
            "pmid": pmid,
            "title": "".join(title_el.itertext()).strip() if title_el is not None else "",
            "abstract": "\n\n".join(abstract_parts),
            "journal": journal_el.text.strip() if journal_el is not None and journal_el.text else "",
            "year": int(year_el.text.strip()) if year_el is not None and year_el.text else None,
            "publication_types": pub_types,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return out


# Numeric extractors — pull literal values out of abstract prose so verified_facts
# carries data Claude can quote, not paraphrase.
_N_PARTICIPANT_RX = re.compile(r"(\d{2,5})\s*(?:adult|participant|patient|subject|individual|woman|women|men|elderly)", re.I)
_RCT_COUNT_RX = re.compile(r"(\d{1,3})\s*(?:randomized controlled trials?|RCTs?|clinical trials?|studies)\b", re.I)
_COMPOUND_RX = re.compile(r"\b(linalool|limonene|linalyl acetate|santalol|citronellol|geraniol|eugenol|alpha-pinene|terpinen-4-ol)\b", re.I)
_OIL_RX = re.compile(r"\b(lavender|sandalwood|rose|chamomile|bergamot|ylang.ylang|peppermint|eucalyptus|valerian|sweet orange|frankincense|jasmine)\b", re.I)
_MD_RX = re.compile(r"\bMD\s*[-−]?\s*([\d.]+)", re.I)


def extract_numeric_facts(abstract: str) -> dict:
    """Best-effort extraction of structured facts from an abstract."""
    facts: dict = {}
    n_matches = [int(m.group(1)) for m in _N_PARTICIPANT_RX.finditer(abstract)]
    if n_matches:
        facts["participant_counts_mentioned"] = sorted(set(n_matches), reverse=True)[:3]
    rct_matches = [int(m.group(1)) for m in _RCT_COUNT_RX.finditer(abstract)]
    if rct_matches:
        facts["trial_counts_mentioned"] = sorted(set(rct_matches), reverse=True)[:3]
    compounds = sorted(set(m.group(1).lower() for m in _COMPOUND_RX.finditer(abstract)))
    if compounds:
        facts["compounds_mentioned"] = compounds
    oils = sorted(set(m.group(1).lower() for m in _OIL_RX.finditer(abstract)))
    if oils:
        facts["oils_mentioned"] = oils
    mds = [float(m.group(1)) for m in _MD_RX.finditer(abstract)]
    if mds:
        facts["mean_differences_mentioned"] = mds[:6]
    return facts


def study_score(study: dict) -> int:
    """Rank a study for editorial selection. Higher = more on-brand for OA warm-hug science."""
    score = 0
    for pt in study.get("publication_types", []):
        score += STUDY_TYPE_WEIGHT.get(pt.lower(), 0)
    # Recency bonus
    year = study.get("year") or 0
    if year >= datetime.now(timezone.utc).year - 1:
        score += 30
    elif year >= datetime.now(timezone.utc).year - 2:
        score += 15
    # Bonus when extractable structured facts exist
    facts = study.get("extracted_facts", {})
    if facts.get("participant_counts_mentioned"):
        score += 20
    if facts.get("trial_counts_mentioned"):
        score += 25  # meta-analyses cite trial counts
    if facts.get("compounds_mentioned"):
        score += 10
    return score


# ---------------------------------------------------------------------- Reddit

def reddit_top_threads(subreddit: str, time_window: str = "week", limit: int = 10) -> list[dict]:
    """Return top threads from a subreddit in the time window."""
    url = f"https://www.reddit.com/r/{subreddit}/top.json?t={time_window}&limit={limit}"
    try:
        data = http_json(url)
    except Exception:
        return []
    out = []
    for item in (data or {}).get("data", {}).get("children", []):
        d = item.get("data", {})
        if d.get("score", 0) < 30:
            continue
        if d.get("over_18") or d.get("stickied"):
            continue
        out.append({
            "subreddit": subreddit,
            "title": d.get("title", "").strip(),
            "permalink": "https://www.reddit.com" + d.get("permalink", ""),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "selftext": (d.get("selftext") or "").strip()[:800],
            "url": d.get("url"),
        })
    return out


# ---------------------------------------------------------------------- Claude

def call_claude(prompt: str, timeout: int = 240) -> str:
    proc = subprocess.run(
        ["claude", "--print"],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude --print exit {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout.strip()


def frame_study_as_oa_topic(study: dict, brand_cfg: dict) -> dict:
    """Ask Claude to write a topic + angle + key points + visual notes for a PubMed study."""
    abstract = study.get("abstract", "") or "(no abstract available)"
    facts_summary = json.dumps(study.get("extracted_facts", {}), indent=2)
    prompt = f"""You are writing an Organic Aromas (OA) SP2 video topic.

OA SELLS PREMIUM WELLNESS DIFFUSERS + ESSENTIAL OILS via a Nebulizing Diffuser format.
The SCRIPT NEVER mentions products (no diffuser/nebulizer/bottle/oil-SKU references)
— locked SP2 doctrine: warm hug for the wellness-curious ICP, brand sells via outro
and bio. Anti-pattern: NO medical claims ("treats", "cures", "prevents") — preserve
study-language exactly ("improved sleep quality"). Tone: calm contemplation with
scientific precision; sensory language welcome; trust by data.

BRAND VOICE: {brand_cfg['brand_voice']}
ANTI-PATTERNS: {'; '.join(brand_cfg['anti_patterns'])}

PUBMED STUDY TO FRAME (real, recently published):
  PMID: {study['pmid']}
  Title: {study['title']}
  Journal: {study['journal']} ({study.get('year')})
  Publication types: {', '.join(study.get('publication_types', []))}
  URL: {study['url']}

ABSTRACT:
{abstract}

EXTRACTED STRUCTURED FACTS (use these literally — do NOT paraphrase numbers):
{facts_summary}

Produce four fields. Plain text, no markdown, no quotes, no emojis. Use the
literal numerical values from the extracted facts where applicable.

Return ONLY valid JSON, no preamble, no fences, this exact schema:
{{
  "topic": "<one-line headline, under 70 chars, anchored on the strongest concrete value>",
  "angle": "<2-4 sentences. Story-shape: what the study asked, the literal data, the mechanism, why it matters to a wellness-curious adult. Avoid 'treats' / 'cures' / 'heals' — preserve study language.>",
  "key_points": ["<bullet 1: literal value from extracted facts>", "<bullet 2: mechanism / compound / pathway>", "<bullet 3: practical takeaway, NO product mention>"],
  "visual_notes": "<2-3 sentences. Imagery direction for FLUX/Freepik: science-meets-nature aesthetic (microscope on a flower bud, molecule stylings, hands, brain, light through leaves). NO diffuser/bottle/product imagery. Calm, premium, contemplative.>"
}}"""
    raw = call_claude(prompt)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def build_verified_facts(study: dict, run_iso_utc: str) -> dict:
    """Compose verified_facts from a PubMed study record."""
    facts = {
        "source": f"PubMed PMID {study['pmid']} via NCBI E-utilities — {study['journal']} ({study.get('year')})",
        "source_url": study["url"],
        "source_pmid": study["pmid"],
        "source_run_iso_utc": run_iso_utc,
        "title": study["title"],
        "journal": study["journal"],
        "publication_year": study.get("year"),
        "publication_types": study.get("publication_types", []),
    }
    extracted = study.get("extracted_facts", {})
    if extracted.get("participant_counts_mentioned"):
        facts["participant_counts"] = extracted["participant_counts_mentioned"]
        facts["largest_n"] = extracted["participant_counts_mentioned"][0]
    if extracted.get("trial_counts_mentioned"):
        facts["trial_counts"] = extracted["trial_counts_mentioned"]
        facts["rct_count"] = extracted["trial_counts_mentioned"][0]
    if extracted.get("compounds_mentioned"):
        facts["compounds_named_in_abstract"] = extracted["compounds_mentioned"]
    if extracted.get("oils_mentioned"):
        facts["oils_named_in_abstract"] = extracted["oils_mentioned"]
    if extracted.get("mean_differences_mentioned"):
        facts["effect_sizes_md"] = extracted["mean_differences_mentioned"]
    return facts


def build_research_brief(payload: dict, study: dict, verified_facts: dict) -> str:
    """Compose the Research Brief text block (Airtable field) — same shape as WLH."""
    key_points = payload.get("key_points") or []
    kp_block = "\n".join(f"- {kp}" for kp in key_points)
    facts_json = json.dumps(verified_facts, indent=2, sort_keys=True)
    return (
        f"SOURCE: PubMed PMID {study['pmid']} — {study['journal']} ({study.get('year')})\n"
        f"URL: {study['url']}\n"
        f"\n"
        f"ANGLE: {payload['angle']}\n"
        f"\n"
        f"KEY POINTS:\n{kp_block}\n"
        f"\n"
        f"STUDY TITLE: {study['title']}\n"
        f"\n"
        f"PLATFORM FIT: Instagram Reel (SP2 vertical video, 30s).\n"
        f"\n"
        f"VISUAL NOTES: {payload.get('visual_notes', '')}\n"
        f"\n"
        f"BRAND RULE (hard): SCRIPT TELLS THE SCIENCE STORY, NEVER SELLS THE PRODUCT. "
        f"Zero diffuser / nebulizer / oil-bottle references. Beat 7 is a poetic insight, "
        f"not a sales line. NO medical claims — preserve study language ('improved sleep "
        f"quality'), never paraphrase to 'treats' / 'cures'.\n"
        f"\n"
        f"{VERIFIED_FACTS_OPEN}\n{facts_json}\n{VERIFIED_FACTS_CLOSE}"
    )


def post_airtable_row(env: dict, topic_payload: dict, study: dict, verified_facts: dict, dryrun: bool) -> dict:
    """Write one Status=Queued row to OA Social Queue."""
    url = f"https://api.airtable.com/v0/{OA_AIRTABLE_BASE}/{urllib.parse.quote(OA_AIRTABLE_TABLE)}"
    fields = {
        "Status": "Queued",
        "Topic": topic_payload["topic"],
        "Research Brief": build_research_brief(topic_payload, study, verified_facts),
        "Date Added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    body = json.dumps({"fields": fields})
    if dryrun:
        return {"dryrun": True, "would_create": fields}
    return http_json(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {env['AIRTABLE_API_KEY']}",
            "Content-Type": "application/json",
        },
        body=body,
    )


def ledger_append(line: str) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(line.rstrip() + "\n")


def dedup_studies(studies: list[dict], max_n: int) -> list[dict]:
    """Pick top N studies, prefer compound diversity (don't write 3 lavender topics)."""
    out = []
    seen_oils = set()
    for s in studies:
        oils = set((s.get("extracted_facts") or {}).get("oils_mentioned", []))
        if oils & seen_oils and len(out) > 0:
            continue
        seen_oils |= oils
        out.append(s)
        if len(out) >= max_n:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true", help="Don't write to Airtable, print only")
    ap.add_argument("--max-topics", type=int, default=2, help="Max topics to write per run")
    ap.add_argument("--months-back", type=int, default=18, help="PubMed search window")
    ap.add_argument("--max-search", type=int, default=20, help="Max PubMed PMIDs to fetch")
    ap.add_argument("--reddit", action="store_true", help="Also pull r/aromatherapy + r/essentialoils trending threads")
    ap.add_argument("--no-pubmed", action="store_true", help="Skip PubMed (debug / Reddit-only)")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    if not env.get("AIRTABLE_API_KEY"):
        print(f"ERROR: missing AIRTABLE_API_KEY at {ENV_PATH}", file=sys.stderr)
        sys.exit(1)

    brand_cfg = json.loads((OA_BRAND_DIR / "brand.json").read_text())
    ncbi_api_key = env.get("NCBI_API_KEY")  # optional

    run_iso_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    studies: list[dict] = []

    if not args.no_pubmed:
        print(f"[1/4] PubMed search — last {args.months_back} months, max {args.max_search} results")
        pmids = pubmed_search(months_back=args.months_back, max_results=args.max_search, api_key=ncbi_api_key)
        print(f"  {len(pmids)} PMIDs found")
        if pmids:
            time.sleep(0.4)  # NCBI politeness
            studies_by_pmid = pubmed_fetch_abstracts(pmids, api_key=ncbi_api_key)
            for pmid, s in studies_by_pmid.items():
                s["extracted_facts"] = extract_numeric_facts(s.get("abstract", ""))
                s["score"] = study_score(s)
                studies.append(s)
            studies.sort(key=lambda s: -s["score"])
            print(f"  top 5 by score:")
            for s in studies[:5]:
                print(f"    [{s['score']:3d}] {s['journal']} {s.get('year')} — {s['title'][:80]}")

    if args.reddit:
        print(f"[1b/4] Reddit — top weekly threads from {REDDIT_SUBREDDITS}")
        # v1: log Reddit signal alongside but don't seed Airtable from it (PubMed has higher rigor).
        # Future: blend in based on score + engagement when an angle isn't covered by recent research.
        for sub in REDDIT_SUBREDDITS:
            threads = reddit_top_threads(sub, time_window="week", limit=5)
            for t in threads:
                print(f"    r/{sub} [{t['score']:4d}/{t['num_comments']:3d}c] {t['title'][:80]}")
            time.sleep(0.3)

    print(f"[2/4] selecting top {args.max_topics} (compound dedup)")
    top_studies = dedup_studies(studies, args.max_topics)
    print(f"  {len(top_studies)} studies selected")

    print(f"[3/4] framing each as OA topic via claude")
    framed = []
    for i, s in enumerate(top_studies, 1):
        try:
            facts = build_verified_facts(s, run_iso_utc)
            payload = frame_study_as_oa_topic(s, brand_cfg)
            framed.append((s, payload, facts))
            print(f"  [{i}/{len(top_studies)}] {payload['topic'][:80]}")
        except Exception as exc:
            print(f"  [{i}/{len(top_studies)}] FAILED: {exc}")

    print(f"[4/4] writing to Airtable Social Queue (dryrun={args.dryrun})")
    results = []
    for s, payload, facts in framed:
        try:
            res = post_airtable_row(env, payload, s, facts, args.dryrun)
            rec_id = (res or {}).get("id") if not args.dryrun else "(dryrun)"
            results.append(res)
            print(f"  ✓ {payload['topic'][:60]} → {rec_id}")
        except Exception as exc:
            print(f"  ✗ {payload['topic'][:60]} — {exc}")

    summary = (
        f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} oa_topic_researcher "
        f"studies_seen={len(studies)} written={sum(1 for r in results if r and 'id' in r)} "
        f"dryrun={args.dryrun}"
    )
    print(summary)
    if not args.dryrun:
        ledger_append(summary)


if __name__ == "__main__":
    main()
