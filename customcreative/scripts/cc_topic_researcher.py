#!/usr/bin/env python3
"""CC topic researcher — seeds Airtable Social Queue with real hip-hop topics.

Sources (in priority order):
  1. Classic-album ANNIVERSARY calendar — every classic hip-hop album has a
     verifiable release date. On May 16, 2026, Illmatic is 32 years 28 days old
     (released April 19, 1994). Anniversary milestones (20th, 25th, 30th, 33rd,
     40th, 50th) get auto-surfaced when within ±21 days of the milestone date.
     Pure ground truth, never stale.
  2. Rock & Roll Hall of Fame inductions + Grammy-recognized milestones.
  3. Reddit r/hiphopheads + r/wutang + r/streetwear top weekly threads.

Topics are framed through CC's brand voice (per brand.json):
  * Real records, real eras, real producers, real gear.
  * Street-level, no posturing. Energy with reverence.
  * Aimed at people who actually know.
  * No corporate hip-hop clichés. Name the era + the record + the producer.
  * No products in script (universal SP2 warm-hug doctrine).

Usage:
    cc_topic_researcher.py [--dryrun] [--max-topics 2] [--reddit-only] [--calendar-only]

Requires:
    python3 (with required deps) (claude --print)
    <repo-root>/.env (or `$SP2_ENV_PATH`) (AIRTABLE_API_KEY)
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENV_PATH = Path(os.environ.get("SP2_ENV_PATH", str(Path(__file__).resolve().parent.parent.parent / ".env")))
CC_AIRTABLE_BASE = "appbM0PPRQbwi8g5a"
CC_AIRTABLE_TABLE = "tblGmPRpbbHo177el"
CC_BRAND_DIR = Path(__file__).resolve().parent.parent
LEDGER = Path(os.environ.get("SP2_HEARTBEAT_DIR", str(Path(__file__).resolve().parent.parent.parent / "heartbeat"))) / "cc_topic_researcher_ledger.md"

VERIFIED_FACTS_OPEN = "[VERIFIED_FACTS_JSON_START]"
VERIFIED_FACTS_CLOSE = "[VERIFIED_FACTS_JSON_END]"

REDDIT_SUBREDDITS = ["hiphopheads", "WuTangClan", "streetwear"]
REDDIT_USER_AGENT = "cc-topic-researcher/1.0 (by /u/customcreative)"

# Milestone anniversaries that earn topic priority. Other anniversaries (e.g.
# year 17) are still tracked but scored lower.
MILESTONE_YEARS = {5, 10, 15, 20, 25, 30, 33, 35, 40, 45, 50}

# Curated classic-album spine. Each entry is a verifiable release. The
# researcher computes (today - release_date) and auto-surfaces milestone hits.
# Only canon-level entries that align with CC's brand voice: real records,
# real eras, real producers, real cultural weight.
#
# Adding to this list is the v2 growth path — every new entry expands the
# topic surface forever (release dates don't change).
ALBUM_CANON = [
    # 1980s
    {"artist": "Public Enemy", "album": "It Takes a Nation of Millions to Hold Us Back", "release_iso": "1988-06-28", "producers": ["The Bomb Squad"], "label": "Def Jam"},
    {"artist": "Eric B. & Rakim", "album": "Paid in Full", "release_iso": "1987-07-07", "producers": ["Eric B.", "Rakim"], "label": "4th & B'way"},
    {"artist": "N.W.A", "album": "Straight Outta Compton", "release_iso": "1988-08-09", "producers": ["Dr. Dre", "DJ Yella"], "label": "Ruthless"},
    {"artist": "De La Soul", "album": "3 Feet High and Rising", "release_iso": "1989-03-03", "producers": ["Prince Paul"], "label": "Tommy Boy"},
    # 1990s
    {"artist": "A Tribe Called Quest", "album": "The Low End Theory", "release_iso": "1991-09-24", "producers": ["Q-Tip", "A Tribe Called Quest"], "label": "Jive"},
    {"artist": "Dr. Dre", "album": "The Chronic", "release_iso": "1992-12-15", "producers": ["Dr. Dre"], "label": "Death Row / Interscope"},
    {"artist": "Wu-Tang Clan", "album": "Enter the Wu-Tang (36 Chambers)", "release_iso": "1993-11-09", "producers": ["RZA"], "label": "Loud / RCA"},
    {"artist": "Nas", "album": "Illmatic", "release_iso": "1994-04-19", "producers": ["DJ Premier", "Pete Rock", "Q-Tip", "Large Professor", "L.E.S."], "label": "Columbia"},
    {"artist": "The Notorious B.I.G.", "album": "Ready to Die", "release_iso": "1994-09-13", "producers": ["DJ Premier", "Easy Mo Bee", "Lord Finesse", "Sean 'Puffy' Combs"], "label": "Bad Boy"},
    {"artist": "Mobb Deep", "album": "The Infamous", "release_iso": "1995-04-25", "producers": ["Havoc", "Q-Tip"], "label": "Loud / RCA"},
    {"artist": "Raekwon", "album": "Only Built 4 Cuban Linx...", "release_iso": "1995-08-01", "producers": ["RZA"], "label": "Loud / RCA"},
    {"artist": "GZA", "album": "Liquid Swords", "release_iso": "1995-11-07", "producers": ["RZA"], "label": "Geffen"},
    {"artist": "2Pac", "album": "All Eyez on Me", "release_iso": "1996-02-13", "producers": ["Dr. Dre", "Daz Dillinger", "DJ Quik", "Johnny J"], "label": "Death Row"},
    {"artist": "Jay-Z", "album": "Reasonable Doubt", "release_iso": "1996-06-25", "producers": ["DJ Premier", "Ski Beatz", "Clark Kent"], "label": "Roc-A-Fella"},
    {"artist": "OutKast", "album": "ATLiens", "release_iso": "1996-08-27", "producers": ["Organized Noize", "OutKast"], "label": "LaFace"},
    {"artist": "The Roots", "album": "Things Fall Apart", "release_iso": "1999-02-23", "producers": ["The Roots", "J Dilla", "Scott Storch"], "label": "MCA"},
    # 2000s
    {"artist": "OutKast", "album": "Stankonia", "release_iso": "2000-10-31", "producers": ["Organized Noize", "OutKast"], "label": "LaFace"},
    {"artist": "Jay-Z", "album": "The Blueprint", "release_iso": "2001-09-11", "producers": ["Kanye West", "Just Blaze", "Bink"], "label": "Roc-A-Fella"},
    {"artist": "Madvillain", "album": "Madvillainy", "release_iso": "2004-03-23", "producers": ["Madlib"], "label": "Stones Throw"},
    {"artist": "Kanye West", "album": "The College Dropout", "release_iso": "2004-02-10", "producers": ["Kanye West"], "label": "Roc-A-Fella"},
    {"artist": "Clipse", "album": "Hell Hath No Fury", "release_iso": "2006-11-28", "producers": ["The Neptunes"], "label": "Re-Up / Jive"},
    {"artist": "Rick Ross", "album": "Port of Miami", "release_iso": "2006-08-08", "producers": ["The Runners", "J.R. Rotem"], "label": "Slip-n-Slide / Def Jam"},
    {"artist": "Lupe Fiasco", "album": "Food & Liquor", "release_iso": "2006-09-19", "producers": ["The Neptunes", "Mike Shinoda", "Soundtrakk"], "label": "1st & 15th / Atlantic"},
    # 2010s
    {"artist": "Kanye West", "album": "My Beautiful Dark Twisted Fantasy", "release_iso": "2010-11-22", "producers": ["Kanye West", "Mike Dean", "RZA", "No I.D."], "label": "Roc-A-Fella / Def Jam"},
    {"artist": "Kendrick Lamar", "album": "good kid, m.A.A.d city", "release_iso": "2012-10-22", "producers": ["Pharrell", "Dr. Dre", "Hit-Boy", "Sounwave"], "label": "TDE / Aftermath / Interscope"},
    {"artist": "Run the Jewels", "album": "Run the Jewels", "release_iso": "2013-06-26", "producers": ["El-P"], "label": "Fool's Gold"},
    {"artist": "Kendrick Lamar", "album": "To Pimp a Butterfly", "release_iso": "2015-03-15", "producers": ["Sounwave", "Flying Lotus", "Terrace Martin", "Knxwledge"], "label": "TDE / Aftermath / Interscope"},
]


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


# ---------------------------------------------------------------------- Anniversary calendar

def album_anniversaries(window_days: int = 21) -> list[dict]:
    """Compute upcoming album anniversaries within ±window_days of today."""
    today = datetime.now(timezone.utc).date()
    out = []
    for entry in ALBUM_CANON:
        try:
            release = datetime.fromisoformat(entry["release_iso"]).date()
        except Exception:
            continue
        # Find this year's anniversary date
        this_year_anniv = release.replace(year=today.year)
        # Also next year's if we're past this year's
        candidates = [this_year_anniv, this_year_anniv.replace(year=today.year + 1)]
        for anniv in candidates:
            days_until = (anniv - today).days
            if -window_days <= days_until <= window_days:
                age = anniv.year - release.year
                is_milestone = age in MILESTONE_YEARS
                score = (200 if is_milestone else 80) + max(0, 30 - abs(days_until))
                out.append({
                    **entry,
                    "anniversary_iso": anniv.strftime("%Y-%m-%d"),
                    "age_years": age,
                    "days_until": days_until,
                    "is_milestone": is_milestone,
                    "score": score,
                })
                break  # don't double-count same album in both this_year/next_year window
    return sorted(out, key=lambda e: -e["score"])


# ---------------------------------------------------------------------- Reddit

# Extract hip-hop entities for Reddit scoring
HIPHOP_ENTITIES = [
    # MCs
    "Nas", "Jay-Z", "Biggie", "Notorious B.I.G.", "2Pac", "Tupac", "Andre 3000",
    "Kendrick Lamar", "J. Cole", "Kanye", "Eminem", "Dr. Dre", "Snoop Dogg",
    "Method Man", "Raekwon", "GZA", "RZA", "Ghostface", "ODB", "Cappadonna",
    "Madlib", "MF DOOM", "MF Doom", "Big Pun", "Black Thought", "Q-Tip",
    "Common", "Mos Def", "Talib Kweli", "Pharoahe Monch", "Rakim",
    "Clipse", "Pusha T", "Rick Ross", "Lupe Fiasco", "Lil Wayne",
    # Producers
    "DJ Premier", "Pete Rock", "J Dilla", "The Neptunes", "Pharrell",
    "Just Blaze", "9th Wonder", "Madlib", "Alchemist", "Havoc", "El-P",
    "Sounwave", "Hit-Boy", "No I.D.", "Mike Dean", "Knxwledge",
    # Crews / labels
    "Wu-Tang", "Wu Tang", "Roc-A-Fella", "Death Row", "Bad Boy", "Def Jam",
    "TDE", "Stones Throw", "Loud Records",
    # Albums
    "Illmatic", "Reasonable Doubt", "Ready to Die", "All Eyez on Me",
    "Stankonia", "ATLiens", "Liquid Swords", "Only Built 4 Cuban Linx",
    "Things Fall Apart", "Madvillainy", "Blueprint",
    "good kid, m.A.A.d city", "To Pimp a Butterfly", "DAMN",
    # Cities / scenes
    "Brooklyn", "Bronx", "Staten Island", "Compton", "Atlanta", "Houston",
    "Detroit", "Chicago", "Philly", "Memphis", "Bay Area",
]


def reddit_top_threads(subreddit: str, time_window: str = "week", limit: int = 10) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/top.json?t={time_window}&limit={limit}"
    try:
        data = http_json(url)
    except Exception as exc:
        print(f"  reddit {subreddit} error: {exc}", file=sys.stderr)
        return []
    out = []
    for item in (data or {}).get("data", {}).get("children", []):
        d = item.get("data", {})
        if d.get("score", 0) < 100:
            continue
        if d.get("over_18") or d.get("stickied"):
            continue
        out.append({
            "subreddit": subreddit,
            "title": d.get("title", "").strip(),
            "permalink": "https://www.reddit.com" + d.get("permalink", ""),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "selftext": (d.get("selftext") or "").strip()[:1200],
            "flair": d.get("link_flair_text") or "",
        })
    return out


def reddit_score_topic(thread: dict) -> int:
    score = 0
    text = (thread["title"] + " " + thread.get("selftext", "")).lower()
    entities_hit = sum(1 for e in HIPHOP_ENTITIES if e.lower() in text)
    score += entities_hit * 22
    score += min(thread["score"] // 100, 10) * 5
    score += min(thread["num_comments"] // 50, 6) * 3
    # Penalize political / meta
    if any(t in text for t in ["election", "politics", "moderator", "this sub", "ban", "removed"]):
        score -= 60
    # Bonus for anniversary-style threads
    if any(t in text for t in ["anniversary", "years ago", "today in", "x years"]):
        score += 30
    return score


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


def frame_as_cc_topic(source_kind: str, source_data: dict, brand_cfg: dict) -> dict:
    """Ask Claude to write a CC-shaped topic + angle + key points."""
    source_summary = json.dumps(source_data, indent=2, sort_keys=True)
    prompt = f"""You are writing a Custom Creative (CC) SP2 video topic.

CC SELLS hip-hop merch (album-art tees, Wu-Tang lights, Custom Ridge wallets) with
cultural authority. The SCRIPT NEVER mentions products — universal SP2 doctrine:
warm hug for hip-hop heads, brand sells via outro + bio.

BRAND VOICE: {brand_cfg['brand_voice']}
ANTI-PATTERNS: {'; '.join(brand_cfg['anti_patterns'])}

CC ABSOLUTE RULES:
- ALWAYS name the era, the record, the producer. Never generic "rap" references.
- Real records, real producers, real gear. Specifics over vibes.
- Street-level energy with reverence. No corporate-rap clichés.
- Aimed at people who actually know — heads, not newcomers.
- Bonus: hip-hop history is verifiable. Quote release dates literally.

SOURCE ({source_kind}):
{source_summary}

Produce four fields. Use the literal values (dates, names, labels, producers)
from the source. No paraphrasing of facts.

Return ONLY valid JSON, no preamble, no fences, this exact schema:
{{
  "topic": "<one-line headline, under 70 chars, anchored on the strongest fact + era>",
  "angle": "<2-4 sentences. The specific record/moment, who made it, why it lands. Name the producers + label + city. Energy with reverence.>",
  "key_points": ["<bullet 1: literal date / age / value>", "<bullet 2: specific producer or musical detail>", "<bullet 3: cultural payoff or takeaway, NO product mention>"],
  "visual_notes": "<2-3 sentences. Imagery direction: vintage hip-hop aesthetic, polaroid grain, gold + black palette, era-specific markers (boombox 80s, four-track 90s, Polaroid 00s). No products in frame.>"
}}"""
    raw = call_claude(prompt)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def build_verified_facts_anniversary(entry: dict, run_iso_utc: str) -> dict:
    return {
        "source": f"CustomCreative classic-album anniversary calendar — {entry['artist']} / {entry['album']}",
        "source_run_iso_utc": run_iso_utc,
        "subject": f"{entry['artist']} — {entry['album']} ({entry['age_years']}th anniversary)",
        "artist": entry["artist"],
        "album_title": entry["album"],
        "release_iso": entry["release_iso"],
        "anniversary_iso": entry["anniversary_iso"],
        "anniversary_age_years": entry["age_years"],
        "is_milestone_anniversary": entry["is_milestone"],
        "days_until_or_since": entry["days_until"],
        "producers": entry.get("producers", []),
        "label": entry.get("label"),
        "today_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def build_verified_facts_reddit(thread: dict, run_iso_utc: str) -> dict:
    text = (thread["title"] + " " + thread.get("selftext", ""))
    text_lower = text.lower()
    entities = sorted(set(e for e in HIPHOP_ENTITIES if e.lower() in text_lower))
    return {
        "source": f"Reddit r/{thread['subreddit']} top weekly — score {thread['score']}, {thread['num_comments']} comments",
        "source_url": thread["permalink"],
        "source_run_iso_utc": run_iso_utc,
        "subject": thread["title"],
        "subreddit": thread["subreddit"],
        "post_score": thread["score"],
        "post_comments": thread["num_comments"],
        "post_flair": thread.get("flair"),
        "entities_mentioned": entities,
        "selftext_excerpt": thread.get("selftext", "")[:600],
        "today_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def build_research_brief(payload: dict, source_summary_line: str, verified_facts: dict) -> str:
    key_points = payload.get("key_points") or []
    kp_block = "\n".join(f"- {kp}" for kp in key_points)
    facts_json = json.dumps(verified_facts, indent=2, sort_keys=True)
    return (
        f"SOURCE: {source_summary_line}\n"
        f"\n"
        f"ANGLE: {payload['angle']}\n"
        f"\n"
        f"KEY POINTS:\n{kp_block}\n"
        f"\n"
        f"PLATFORM FIT: TikTok Reel + Instagram Reel (SP2 vertical video, 30s).\n"
        f"\n"
        f"VISUAL NOTES: {payload.get('visual_notes', '')}\n"
        f"\n"
        f"BRAND RULE (hard): SCRIPT TELLS THE HIP-HOP STORY, NEVER SELLS THE MERCH. "
        f"Zero tee/hoodie/wear/buy/drop references. Always name the era, the record, the producer. "
        f"Beat 7 is a poetic close honoring the legacy, not a sales line.\n"
        f"\n"
        f"{VERIFIED_FACTS_OPEN}\n{facts_json}\n{VERIFIED_FACTS_CLOSE}"
    )


def post_airtable_row(env: dict, topic_payload: dict, source_summary: str, verified_facts: dict, dryrun: bool) -> dict:
    url = f"https://api.airtable.com/v0/{CC_AIRTABLE_BASE}/{urllib.parse.quote(CC_AIRTABLE_TABLE)}"
    fields = {
        "Status": "Queued",
        "Topic": topic_payload["topic"],
        "Research Brief": build_research_brief(topic_payload, source_summary, verified_facts),
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--max-topics", type=int, default=2)
    ap.add_argument("--reddit-only", action="store_true")
    ap.add_argument("--calendar-only", action="store_true")
    ap.add_argument("--window-days", type=int, default=21)
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    if not env.get("AIRTABLE_API_KEY"):
        print(f"ERROR: missing AIRTABLE_API_KEY at {ENV_PATH}", file=sys.stderr)
        sys.exit(1)

    brand_cfg = json.loads((CC_BRAND_DIR / "brand.json").read_text())
    run_iso_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates: list[tuple[int, str, dict]] = []

    if not args.reddit_only:
        print(f"[1a/4] album anniversaries within ±{args.window_days} days")
        anniv = album_anniversaries(args.window_days)
        for entry in anniv:
            tag = "🎖" if entry["is_milestone"] else " "
            print(f"    [{entry['score']:3d}] {tag} {entry['anniversary_iso']} {entry['artist']} — {entry['album']} ({entry['age_years']} yrs)")
            candidates.append((entry["score"], "anniversary", entry))

    if not args.calendar_only:
        print(f"[1b/4] reddit — top weekly from {REDDIT_SUBREDDITS}")
        for sub in REDDIT_SUBREDDITS:
            threads = reddit_top_threads(sub, "week", 8)
            for t in threads:
                s = reddit_score_topic(t)
                if s < 30:
                    continue
                print(f"    [{s:3d}] r/{sub:15s} [{t['score']:4d}/{t['num_comments']:3d}c] {t['title'][:70]}")
                candidates.append((s, "reddit", t))
            time.sleep(0.4)

    candidates.sort(key=lambda c: -c[0])
    print(f"[2/4] {len(candidates)} candidates; selecting top {args.max_topics}")
    top = candidates[: args.max_topics]

    print(f"[3/4] framing each as CC topic via claude")
    framed = []
    for i, (score, kind, data) in enumerate(top, 1):
        try:
            if kind == "anniversary":
                facts = build_verified_facts_anniversary(data, run_iso_utc)
                source_summary = f"CustomCreative classic-album anniversary calendar — {data['artist']} / {data['album']} turns {data['age_years']}"
            else:
                facts = build_verified_facts_reddit(data, run_iso_utc)
                source_summary = f"Reddit r/{data['subreddit']} top weekly — score {data['score']}"
            payload = frame_as_cc_topic(kind, data, brand_cfg)
            framed.append((kind, data, payload, facts, source_summary))
            print(f"  [{i}/{len(top)}] {payload['topic'][:80]}")
        except Exception as exc:
            print(f"  [{i}/{len(top)}] FAILED: {exc}")

    print(f"[4/4] writing to Airtable (dryrun={args.dryrun})")
    results = []
    for kind, data, payload, facts, source_summary in framed:
        try:
            res = post_airtable_row(env, payload, source_summary, facts, args.dryrun)
            rec_id = (res or {}).get("id") if not args.dryrun else "(dryrun)"
            results.append(res)
            print(f"  ✓ {payload['topic'][:60]} → {rec_id}")
        except Exception as exc:
            print(f"  ✗ {payload['topic'][:60]} — {exc}")

    summary = (
        f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} cc_topic_researcher "
        f"candidates={len(candidates)} written={sum(1 for r in results if r and 'id' in r)} "
        f"dryrun={args.dryrun}"
    )
    print(summary)
    if not args.dryrun:
        ledger_append(summary)


if __name__ == "__main__":
    main()
