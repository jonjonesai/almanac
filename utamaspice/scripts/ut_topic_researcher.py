#!/usr/bin/env python3
"""UT topic researcher — seeds Airtable Social Queue with real Bali craft + culture topics.

UT (Utama Spice) sells artisan soap, balm, oil, incense from Bali. The brand
voice is plant-rooted and story-driven: name the maker, the village, the ritual.
Topics come from two layered sources:
  1. A curated Bali cultural-calendar (Galungan, Nyepi, Saraswati, harvest
     ceremonies, Ubud festivals) — these recur annually on Pawukon-cycle
     dates that are precomputed.
  2. Reddit r/Bali + r/Indonesia + r/handmadeskincare top weekly threads —
     live signal about what travelers / locals / makers are actually
     discussing.

Doctrine: NO product mention in script body (universal SP2 rule). NO medical
or healing claims (per UT brand.json anti-patterns). NO greenwashing language.
Always name the plant + the village + the maker.

Usage:
    ut_topic_researcher.py [--dryrun] [--max-topics 2] [--reddit-only] [--calendar-only]
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
UT_AIRTABLE_BASE = "appCsFjgzzGelMmpY"
UT_AIRTABLE_TABLE = "tblUKzuTGBa3i8ovJ"
UT_BRAND_DIR = Path(__file__).resolve().parent.parent
LEDGER = Path(os.environ.get("SP2_HEARTBEAT_DIR", str(Path(__file__).resolve().parent.parent.parent / "heartbeat"))) / "ut_topic_researcher_ledger.md"

VERIFIED_FACTS_OPEN = "[VERIFIED_FACTS_JSON_START]"
VERIFIED_FACTS_CLOSE = "[VERIFIED_FACTS_JSON_END]"

REDDIT_SUBREDDITS = ["Bali", "indonesia", "handmadeskincare", "skincareaddiction"]
REDDIT_USER_AGENT = "ut-topic-researcher/1.0 (by /u/utamaspice)"

# Curated Balinese / Indonesian cultural calendar. Pawukon-cycle dates (Galungan,
# Kuningan, Saraswati) recur every 210 days; researcher checks against today.
# Other festivals are Gregorian-fixed and recur annually.
CULTURAL_CALENDAR = [
    {
        "id": "galungan_2026_aug",
        "name": "Galungan",
        "village": "everywhere in Bali — penjor on every house",
        "region": "Bali (whole island)",
        "start_iso": "2026-08-19",
        "end_iso": "2026-08-29",
        "is_pawukon": True,
        "notes": "Galungan celebrates the victory of dharma over adharma. 10-day window of penjor bamboo poles arching over every street, offerings at family temples. Kuningan closes the 10-day window.",
        "anchor_terms": ["penjor", "dharma", "ancestor"],
    },
    {
        "id": "saraswati_2026_may",
        "name": "Saraswati Day",
        "village": "Balinese schools + libraries everywhere",
        "region": "Bali (whole island)",
        "start_iso": "2026-05-23",
        "end_iso": "2026-05-23",
        "is_pawukon": True,
        "notes": "Holiday for the goddess of knowledge, learning, art. Books and lontar palm-leaf manuscripts get offerings. Schools blessed. No reading or writing the day itself.",
        "anchor_terms": ["lontar", "Saraswati", "Banyu Pinaruh"],
    },
    {
        "id": "nyepi_2027",
        "name": "Nyepi (Balinese New Year)",
        "village": "all of Bali — every village silent",
        "region": "Bali (whole island)",
        "start_iso": "2027-03-08",
        "end_iso": "2027-03-08",
        "is_pawukon": False,
        "notes": "Day of Silence. Entire island shuts down for 24 hours — no lights, no fires, no traffic, no flights into Denpasar. Preceded the night before by Ngrupuk parade of ogoh-ogoh demon effigies, burned at the village crossroads.",
        "anchor_terms": ["ogoh-ogoh", "Ngrupuk", "Caka", "Tilem"],
    },
    {
        "id": "bali_arts_festival_2026",
        "name": "Bali Arts Festival (Pesta Kesenian Bali)",
        "village": "Denpasar (Werdhi Budaya Art Center)",
        "region": "Denpasar, Bali",
        "start_iso": "2026-06-14",
        "end_iso": "2026-07-12",
        "is_pawukon": False,
        "notes": "Month-long festival of Balinese performing arts at the Werdhi Budaya Art Center. Gamelan competitions, legong dance, wayang shadow puppet, kecak. Run by the Bali provincial government.",
        "anchor_terms": ["gamelan", "legong", "wayang", "kecak", "Werdhi Budaya"],
    },
    {
        "id": "ubud_writers_2026",
        "name": "Ubud Writers & Readers Festival",
        "village": "Ubud (multiple venues — Indus, Murni's, Pondok Pekak)",
        "region": "Ubud, Bali",
        "start_iso": "2026-10-21",
        "end_iso": "2026-10-25",
        "is_pawukon": False,
        "notes": "Asia's largest literary festival. Indonesian + international writers gather across Ubud venues. Founded 2004 by Janet DeNeefe after Bali bombings to reconnect Ubud to the world.",
        "anchor_terms": ["Ubud", "Janet DeNeefe", "Indus", "Murni's"],
    },
    {
        "id": "bali_spirit_2026",
        "name": "BaliSpirit Festival",
        "village": "Ubud (Yoga Barn + outer venues)",
        "region": "Ubud, Bali",
        "start_iso": "2026-04-30",
        "end_iso": "2026-05-04",
        "is_pawukon": False,
        "notes": "Yoga + music + dance gathering at the Yoga Barn and outer venues in Ubud. Started 2008. Workshops in vinyasa, kundalini, gong baths, ecstatic dance, sound healing.",
        "anchor_terms": ["Yoga Barn", "kundalini", "kecak", "ecstatic dance"],
    },
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


# ---------------------------------------------------------------------- Calendar

def upcoming_calendar_events(window_days: int = 21) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    out = []
    for ev in CULTURAL_CALENDAR:
        try:
            start = datetime.fromisoformat(ev["start_iso"]).date()
            end = datetime.fromisoformat(ev["end_iso"]).date()
        except Exception:
            continue
        if today > end + timedelta(days=14):
            continue
        days_to_start = (start - today).days
        days_to_end = (end - today).days
        if -14 <= days_to_start <= window_days or -14 <= days_to_end <= window_days:
            ev_copy = dict(ev)
            ev_copy["days_to_start"] = days_to_start
            ev_copy["days_to_end"] = days_to_end
            ev_copy["is_active"] = start <= today <= end
            ev_copy["score"] = (200 if ev_copy["is_active"] else 100) + max(0, 30 - abs(days_to_start))
            out.append(ev_copy)
    return sorted(out, key=lambda e: -e["score"])


# ---------------------------------------------------------------------- Reddit

# Entities that earn a Bali / Indonesia / artisan-craft topic priority score.
BALI_ENTITIES = [
    # Places
    "Ubud", "Denpasar", "Canggu", "Seminyak", "Sanur", "Uluwatu", "Amed",
    "Munduk", "Lovina", "Sidemen", "Tegallalang", "Tirta Empul",
    "Nusa Penida", "Nusa Lembongan", "Nusa Ceningan", "Gilis", "Gili Trawangan",
    "Java", "Yogyakarta", "Bandung", "Lombok",
    # Cultural anchors
    "Galungan", "Kuningan", "Nyepi", "Saraswati", "Pawukon", "lontar",
    "ogoh-ogoh", "Ngrupuk", "gamelan", "legong", "wayang", "kecak",
    "Tirta", "Pura", "Banjar", "Subak", "penjor", "canang",
    # Plant + craft + body-product terms
    "kaffir lime", "lemongrass", "frangipani", "jasmine", "vetiver",
    "patchouli", "cloves", "turmeric", "ginger", "candlenut", "coconut",
    "ylang ylang", "ylang-ylang", "sandalwood", "agarwood",
    "balm", "lulur", "boreh", "incense", "dukun", "jamu",
    # Makers / villages
    "Tenganan", "Sidemen", "Munduk", "Ubud artisan", "Sukawati",
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
        if d.get("score", 0) < 50:
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
    entities_hit = sum(1 for e in BALI_ENTITIES if e.lower() in text)
    score += entities_hit * 22
    score += min(thread["score"] // 50, 8) * 5
    score += min(thread["num_comments"] // 30, 5) * 3
    # Penalize political / news-only / scam-warning threads (UT is artisan/craft/story)
    if any(t in text for t in ["election", "politics", "scam", "tourist trap", "ripoff", "violation"]):
        score -= 60
    if any(t in text for t in ["this sub", "moderator", "downvote"]):
        score -= 30
    # Bonus when artisan / craft / plant terms present
    if any(t in text for t in ["handmade", "artisan", "craft", "village", "maker", "ritual"]):
        score += 25
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


def frame_as_ut_topic(source_kind: str, source_data: dict, brand_cfg: dict) -> dict:
    source_summary = json.dumps(source_data, indent=2, sort_keys=True)
    prompt = f"""You are writing a Utama Spice (UT) SP2 video topic.

UT SELLS artisan soap, balm, oil, incense from Bali. The SCRIPT NEVER mentions
the products (no soap/balm/oil/buy references) — universal SP2 doctrine: warm
hug for the Bali-curious + craft-loving ICP, brand sells via outro + bio.

BRAND VOICE: {brand_cfg['brand_voice']}
ANTI-PATTERNS: {'; '.join(brand_cfg['anti_patterns'])}

UT ABSOLUTE RULES:
- ALWAYS name the village, the plant, the maker if invoked. "kaffir lime from Sidemen" not "natural ingredients."
- NO medical or healing claims ("treats", "cures", "balances chakras").
- NO greenwashing language alone — name the plant, the source.
- Slow language, no rush. Sensory before instructional.
- Plant-rooted, story-driven. The ritual the product is part of, not the product itself.

SOURCE ({source_kind}):
{source_summary}

Produce four fields. Use literal place names + plant names + ritual terms from
the source. Slow, sensory, specific. No tourist-brochure tone.

Return ONLY valid JSON, no preamble, no fences, this exact schema:
{{
  "topic": "<one-line headline, under 70 chars, anchored on the specific place/plant/moment>",
  "angle": "<2-4 sentences. The story of the village/plant/ritual. Specific names. Slow language, sensory imagery.>",
  "key_points": ["<bullet 1: literal place + date>", "<bullet 2: specific plant or craft detail>", "<bullet 3: emotional / sensory payoff, NO product mention>"],
  "visual_notes": "<2-3 sentences. Imagery: tropical golden hour, hand-painted temple offerings, lush rice terraces, frangipani petals, dim lantern-lit village. Warm orange + brown + cream palette. No product imagery.>"
}}"""
    raw = call_claude(prompt)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def build_verified_facts_calendar(ev: dict, run_iso_utc: str) -> dict:
    return {
        "source": f"UtamaSpice curated Bali cultural-calendar — {ev['id']}",
        "source_run_iso_utc": run_iso_utc,
        "subject": ev["name"],
        "village": ev.get("village"),
        "region": ev.get("region"),
        "start_iso": ev["start_iso"],
        "end_iso": ev["end_iso"],
        "is_pawukon_cycle": ev.get("is_pawukon"),
        "is_active_today": ev.get("is_active"),
        "days_to_start": ev.get("days_to_start"),
        "days_to_end": ev.get("days_to_end"),
        "anchor_terms": ev.get("anchor_terms", []),
        "notes": ev.get("notes"),
        "today_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def build_verified_facts_reddit(thread: dict, run_iso_utc: str) -> dict:
    text_lower = (thread["title"] + " " + thread.get("selftext", "")).lower()
    entities = sorted(set(e for e in BALI_ENTITIES if e.lower() in text_lower))
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
        f"BRAND RULE (hard): SCRIPT TELLS THE BALI STORY, NEVER SELLS THE PRODUCT. "
        f"Zero soap/balm/oil/incense/wear/buy references. ALWAYS name the village + plant + maker. "
        f"NO medical or healing claims. Beat 7 is a poetic close, not a sales line.\n"
        f"\n"
        f"{VERIFIED_FACTS_OPEN}\n{facts_json}\n{VERIFIED_FACTS_CLOSE}"
    )


def post_airtable_row(env: dict, topic_payload: dict, source_summary: str, verified_facts: dict, dryrun: bool) -> dict:
    url = f"https://api.airtable.com/v0/{UT_AIRTABLE_BASE}/{urllib.parse.quote(UT_AIRTABLE_TABLE)}"
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

    brand_cfg = json.loads((UT_BRAND_DIR / "brand.json").read_text())
    run_iso_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates: list[tuple[int, str, dict]] = []

    if not args.reddit_only:
        print(f"[1a/4] Bali cultural calendar — events within ±{args.window_days} days")
        cal = upcoming_calendar_events(args.window_days)
        for ev in cal:
            tag = "ACTIVE" if ev["is_active"] else f"+{ev['days_to_start']}d"
            print(f"    [{ev['score']:3d}] {tag:6s} {ev['name']} — {ev['village'][:60]}")
            candidates.append((ev["score"], "calendar", ev))

    if not args.calendar_only:
        print(f"[1b/4] reddit — top weekly from {REDDIT_SUBREDDITS}")
        for sub in REDDIT_SUBREDDITS:
            threads = reddit_top_threads(sub, "week", 8)
            for t in threads:
                s = reddit_score_topic(t)
                if s < 25:
                    continue
                print(f"    [{s:3d}] r/{sub:18s} [{t['score']:4d}/{t['num_comments']:3d}c] {t['title'][:65]}")
                candidates.append((s, "reddit", t))
            time.sleep(0.4)

    candidates.sort(key=lambda c: -c[0])
    print(f"[2/4] {len(candidates)} candidates; selecting top {args.max_topics}")
    top = candidates[: args.max_topics]

    print(f"[3/4] framing each as UT topic via claude")
    framed = []
    for i, (score, kind, data) in enumerate(top, 1):
        try:
            if kind == "calendar":
                facts = build_verified_facts_calendar(data, run_iso_utc)
                source_summary = f"UtamaSpice curated Bali cultural calendar — {data['id']} ({data['name']})"
            else:
                facts = build_verified_facts_reddit(data, run_iso_utc)
                source_summary = f"Reddit r/{data['subreddit']} top weekly — score {data['score']}"
            payload = frame_as_ut_topic(kind, data, brand_cfg)
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
        f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ut_topic_researcher "
        f"candidates={len(candidates)} written={sum(1 for r in results if r and 'id' in r)} "
        f"dryrun={args.dryrun}"
    )
    print(summary)
    if not args.dryrun:
        ledger_append(summary)


if __name__ == "__main__":
    main()
