#!/usr/bin/env python3
"""TM topic researcher — seeds Airtable Social Queue with real Taiwan culture topics.

Modeled on the SP2 researcher pattern (WLH = skyfield, OA = PubMed). For
TaiwanMerch the editorially-rigorous sources are:
  1. Reddit r/taiwan + r/Taipei + r/asianeats top weekly threads (live signal
     from people actually in Taiwan or traveling there)
  2. A curated cultural-calendar table of known Taiwan festivals + events with
     literal dates and venues (always-correct ground truth that gives the
     researcher something to seed even when Reddit is quiet)

Topics are framed through TM's brand voice (per brand.json):
  * Always name the place (Taipei, Magong, Hualien, Tainan) — never "Taiwan"
    abstract.
  * Always name the food / temple / market if invoked.
  * No orientalism cliches. Energetic without being touristy. Modern Taiwan
    with affection.
  * No product mentions in script body (universal SP2 warm-hug doctrine).

Usage:
    tm_topic_researcher.py [--dryrun] [--max-topics 2] [--reddit-only] [--calendar-only]

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
TM_AIRTABLE_BASE = "apphzFSaDDSX8FFMx"
TM_AIRTABLE_TABLE = "tblyQ7FeT2OTMUEPX"
TM_BRAND_DIR = Path(__file__).resolve().parent.parent
LEDGER = Path(os.environ.get("SP2_HEARTBEAT_DIR", str(Path(__file__).resolve().parent.parent.parent / "heartbeat"))) / "tm_topic_researcher_ledger.md"

VERIFIED_FACTS_OPEN = "[VERIFIED_FACTS_JSON_START]"
VERIFIED_FACTS_CLOSE = "[VERIFIED_FACTS_JSON_END]"

REDDIT_SUBREDDITS = ["taiwan", "Taipei", "asianeats"]
REDDIT_USER_AGENT = "tm-topic-researcher/1.0 (by /u/taiwanmerch)"

# Curated cultural-calendar anchors — known Taiwan festivals + events with
# real annual dates. These are "always-correct" topics the researcher can fall
# back to when Reddit doesn't surface anything strong this week. Dates are
# windows (often lunar-calendar dependent) — researcher emits a topic when the
# event is within ±21 days of "today."
#
# Format: each entry has a stable id, English + Mandarin names, location
# (specific place per TM doctrine), and a function returning (start_iso, end_iso)
# for a given year. Lunar dates are precomputed for the next 2 years.
CULTURAL_CALENDAR = [
    {
        "id": "penghu_fireworks_2026",
        "name": "Penghu International Marine Fireworks Festival 2026",
        "city": "Magong",
        "island_group": "Penghu",
        "start_iso": "2026-05-04",
        "end_iso": "2026-08-25",
        "venue": "Guanyinting Recreation Area",
        "ip_partner": "Dragon Ball Z",
        "notes": "First-ever Penghu × anime collab. 33 evenings, 9 PM start, fireworks + drone light shows.",
        "anti_cliches": ["no 'exotic East'", "use the venue name", "9 PM start matters"],
    },
    {
        "id": "dragon_boat_2026",
        "name": "Dragon Boat Festival 2026",
        "city": "Lukang (largest race) + Taipei (Bitan)",
        "start_iso": "2026-06-19",
        "end_iso": "2026-06-19",
        "venue": "Lukang waterway / Bitan",
        "ip_partner": None,
        "notes": "Duanwu Festival, 5th day of 5th lunar month. Boat races, zongzi rice dumplings, hanging mugwort.",
        "anti_cliches": ["zongzi not 'tamale'", "Lukang specifically"],
    },
    {
        "id": "ghost_month_2026",
        "name": "Ghost Month / Pudu 2026",
        "city": "Keelung + Tainan + Penghu",
        "start_iso": "2026-08-13",
        "end_iso": "2026-09-11",
        "venue": "Keelung Zhongyuan Festival main parade",
        "ip_partner": None,
        "notes": "7th lunar month. Hungry ghosts walk among the living; offerings, lanterns released on water in Keelung, no weddings, no new houses moved into.",
        "anti_cliches": ["never 'spooky'", "always Keelung specifically"],
    },
    {
        "id": "mid_autumn_2026",
        "name": "Mid-Autumn Festival 2026",
        "city": "everywhere — Sun Moon Lake + Tamsui boardwalk + every alley with a grill",
        "start_iso": "2026-09-25",
        "end_iso": "2026-09-25",
        "venue": "Sun Moon Lake moon-viewing + rooftop barbecue everywhere",
        "ip_partner": None,
        "notes": "15th day of 8th lunar month. Mooncakes + pomelos + barbecue everywhere (Taiwan-specific: BBQ became the dominant national way to celebrate, not just mooncakes).",
        "anti_cliches": ["the BBQ thing is uniquely Taiwan, name it"],
    },
    {
        "id": "pingxi_lantern_2027",
        "name": "Pingxi Sky Lantern Festival 2027",
        "city": "Pingxi District (Shifen)",
        "start_iso": "2027-02-26",
        "end_iso": "2027-02-26",
        "venue": "Pingxi Junior High School ground / Shifen Old Street",
        "ip_partner": None,
        "notes": "Sky lanterns released on Lantern Festival (15th day of lunar new year). National Geographic listed as a top global festival.",
        "anti_cliches": ["Pingxi specifically, not just 'lanterns in Taiwan'"],
    },
    {
        "id": "yanshui_beehive_2027",
        "name": "Yanshui Beehive Fireworks 2027",
        "city": "Yanshui (Tainan)",
        "start_iso": "2027-02-26",
        "end_iso": "2027-02-26",
        "venue": "Wumiao Temple Yanshui",
        "ip_partner": None,
        "notes": "Beehive fireworks where rockets are fired AT the crowd. Originated to ward off cholera. One of the world's most dangerous festivals.",
        "anti_cliches": ["the danger is real and specific — don't soften"],
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


# ---------------------------------------------------------------------- Reddit

def reddit_top_threads(subreddit: str, time_window: str = "week", limit: int = 10) -> list[dict]:
    """Return top threads from a subreddit, filtered for editorial fit."""
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
            "url": d.get("url"),
            "flair": d.get("link_flair_text") or "",
            "created_iso": datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc).strftime("%Y-%m-%d"),
        })
    return out


# Extract Taiwan place names from Reddit titles + selftext so we can satisfy
# the "always name the place" anti-pattern rule.
TAIWAN_PLACES = [
    "Taipei", "Kaohsiung", "Tainan", "Taichung", "Hualien", "Taitung", "Chiayi",
    "Keelung", "Hsinchu", "Yilan", "Pingtung", "Miaoli", "Nantou",
    "Jiufen", "Shifen", "Pingxi", "Tamsui", "Beitou", "Wulai",
    "Sun Moon Lake", "Taroko", "Kenting", "Alishan", "Yushan", "Yangmingshan",
    "Penghu", "Magong", "Kinmen", "Lanyu", "Green Island", "Mazu",
    "Lukang", "Yanshui", "Sanxia", "Jiaoxi", "Hengchun",
    "Shilin", "Raohe", "Ningxia", "Shida", "Liaoning", "Longshan",
    "Ximending", "Da'an", "Xinyi", "Beitou",
]
TAIWAN_FOODS = [
    "beef noodle", "xiaolongbao", "bubble tea", "boba", "stinky tofu",
    "oyster omelette", "lu rou fan", "pineapple cake", "douhua", "shaved ice",
    "scallion pancake", "danbing", "gua bao", "popcorn chicken",
    "mango shaved ice", "fried chicken steak", "milk tea", "sausage",
    "intestine soup", "fish ball", "dumpling", "rice noodle",
    "mochi", "mooncake", "zongzi", "pearl milk",
]


def reddit_score_topic(thread: dict) -> int:
    """Editorial score for a Reddit thread. Higher = better TM fit."""
    score = 0
    text = (thread["title"] + " " + thread.get("selftext", "")).lower()
    places_hit = sum(1 for p in TAIWAN_PLACES if p.lower() in text)
    foods_hit = sum(1 for f in TAIWAN_FOODS if f in text)
    score += places_hit * 25  # named place is the strongest signal
    score += foods_hit * 15
    score += min(thread["score"] // 50, 8) * 5  # reddit score capped contribution
    score += min(thread["num_comments"] // 25, 6) * 3
    # Penalize political / news-only threads (TM is culture/lifestyle)
    if any(t in text for t in ["election", "politics", "china threat", "war", "missile", "ccp"]):
        score -= 50
    # Penalize meta / off-topic
    if any(t in text for t in ["this sub", "moderator", "downvote", "removed"]):
        score -= 30
    return score


# ---------------------------------------------------------------------- Cultural calendar

def upcoming_calendar_events(window_days: int = 21) -> list[dict]:
    """Return calendar events within ±window_days of today, scored by proximity + cultural weight."""
    today = datetime.now(timezone.utc).date()
    out = []
    for ev in CULTURAL_CALENDAR:
        try:
            start = datetime.fromisoformat(ev["start_iso"]).date()
            end = datetime.fromisoformat(ev["end_iso"]).date()
        except Exception:
            continue
        if today > end + timedelta(days=14):
            continue  # already past, with grace
        days_to_start = (start - today).days
        days_to_end = (end - today).days
        # Active right now, or within window
        if -14 <= days_to_start <= window_days or -14 <= days_to_end <= window_days:
            ev_copy = dict(ev)
            ev_copy["days_to_start"] = days_to_start
            ev_copy["days_to_end"] = days_to_end
            ev_copy["is_active"] = start <= today <= end
            ev_copy["score"] = (200 if ev_copy["is_active"] else 100) + max(0, 30 - abs(days_to_start))
            out.append(ev_copy)
    return sorted(out, key=lambda e: -e["score"])


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


def frame_as_tm_topic(source_kind: str, source_data: dict, brand_cfg: dict) -> dict:
    """Ask Claude to write a TM-shaped topic + angle + key points from a calendar event or Reddit thread."""
    source_summary = json.dumps(source_data, indent=2, sort_keys=True)
    prompt = f"""You are writing a TaiwanMerch (TM) SP2 video topic.

TM SELLS Taiwan-themed merch (tees, hoodies, prints, pins, mugs) with cultural authority.
The SCRIPT NEVER mentions products (no tee/hoodie/wear/buy/drop references) — universal
SP2 doctrine: warm hug for the Taiwan-curious ICP, brand sells via outro + bio.

BRAND VOICE: {brand_cfg['brand_voice']}
ANTI-PATTERNS: {'; '.join(brand_cfg['anti_patterns'])}

TM ABSOLUTE RULES:
- ALWAYS name the specific place: Magong, Pingxi, Lukang, Shilin, Hualien, Taipei district. NEVER "Taiwan" abstract.
- ALWAYS name the food / temple / market / venue if invoked.
- NO orientalism cliches ("exotic", "mysterious East", "land of contrasts").
- NO "vibes" as a noun.
- ENERGY level: night-market neon + mountain stillness + Taipei MRT punctuality. Modern Taiwan with affection.

SOURCE ({source_kind}):
{source_summary}

Produce four fields using the literal place names, dates, and concrete details
from the source. No paraphrasing of numbers or proper nouns.

Return ONLY valid JSON, no preamble, no fences, this exact schema:
{{
  "topic": "<one-line headline, under 70 chars, anchored on the concrete place + event + date>",
  "angle": "<2-4 sentences. The specific moment, the specific place, the specific food/festival/scene. Modern affection, no tourist-brochure tone.>",
  "key_points": ["<bullet 1: literal date + place>", "<bullet 2: a specific detail (food, performer, custom)>", "<bullet 3: practical or emotional payoff for the viewer, NO product mention>"],
  "visual_notes": "<2-3 sentences. Imagery direction: Studio Ghibli watercolor anime style per TM DESIGN. Specific landmarks, specific lights, specific food. No products in frame.>"
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
        "source": f"TaiwanMerch curated cultural-calendar — {ev['id']}",
        "source_run_iso_utc": run_iso_utc,
        "subject": ev["name"],
        "city": ev["city"],
        "venue": ev.get("venue"),
        "island_group": ev.get("island_group"),
        "start_iso": ev["start_iso"],
        "end_iso": ev["end_iso"],
        "ip_partner": ev.get("ip_partner"),
        "is_active_today": ev.get("is_active"),
        "days_to_start": ev.get("days_to_start"),
        "days_to_end": ev.get("days_to_end"),
        "notes": ev.get("notes"),
        "today_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def build_verified_facts_reddit(thread: dict, run_iso_utc: str) -> dict:
    text = (thread["title"] + " " + thread.get("selftext", ""))
    text_lower = text.lower()
    places = sorted(set(p for p in TAIWAN_PLACES if p.lower() in text_lower))
    foods = sorted(set(f for f in TAIWAN_FOODS if f in text_lower))
    return {
        "source": f"Reddit r/{thread['subreddit']} top weekly — score {thread['score']}, {thread['num_comments']} comments",
        "source_url": thread["permalink"],
        "source_run_iso_utc": run_iso_utc,
        "subject": thread["title"],
        "subreddit": thread["subreddit"],
        "post_score": thread["score"],
        "post_comments": thread["num_comments"],
        "post_created_iso": thread.get("created_iso"),
        "post_flair": thread.get("flair"),
        "places_mentioned": places,
        "foods_mentioned": foods,
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
        f"BRAND RULE (hard): SCRIPT TELLS THE STORY, NEVER SELLS THE MERCH. Zero tee/hoodie/merch/wear/buy references. "
        f"ALWAYS name specific places (Magong, Pingxi, Lukang, Shilin, Hualien). Beat 7 is a poetic close, not a sales line.\n"
        f"\n"
        f"{VERIFIED_FACTS_OPEN}\n{facts_json}\n{VERIFIED_FACTS_CLOSE}"
    )


def post_airtable_row(env: dict, topic_payload: dict, source_summary: str, verified_facts: dict, dryrun: bool) -> dict:
    url = f"https://api.airtable.com/v0/{TM_AIRTABLE_BASE}/{urllib.parse.quote(TM_AIRTABLE_TABLE)}"
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
    ap.add_argument("--dryrun", action="store_true", help="Don't write to Airtable")
    ap.add_argument("--max-topics", type=int, default=2)
    ap.add_argument("--reddit-only", action="store_true")
    ap.add_argument("--calendar-only", action="store_true")
    ap.add_argument("--window-days", type=int, default=21, help="Calendar event window")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    if not env.get("AIRTABLE_API_KEY"):
        print(f"ERROR: missing AIRTABLE_API_KEY at {ENV_PATH}", file=sys.stderr)
        sys.exit(1)

    brand_cfg = json.loads((TM_BRAND_DIR / "brand.json").read_text())
    run_iso_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates: list[tuple[int, str, dict]] = []  # (score, source_kind, source_data)

    if not args.reddit_only:
        print(f"[1a/4] cultural calendar — events within ±{args.window_days} days")
        cal_events = upcoming_calendar_events(args.window_days)
        for ev in cal_events:
            tag = "ACTIVE" if ev["is_active"] else f"+{ev['days_to_start']}d"
            print(f"    [{ev['score']:3d}] {tag:6s} {ev['name']} — {ev['city']}")
            candidates.append((ev["score"], "calendar", ev))

    if not args.calendar_only:
        print(f"[1b/4] reddit — top weekly from {REDDIT_SUBREDDITS}")
        for sub in REDDIT_SUBREDDITS:
            threads = reddit_top_threads(sub, time_window="week", limit=8)
            for t in threads:
                s = reddit_score_topic(t)
                if s < 20:
                    continue
                print(f"    [{s:3d}] r/{sub:9s} [{t['score']:4d}/{t['num_comments']:3d}c] {t['title'][:75]}")
                candidates.append((s, "reddit", t))
            time.sleep(0.4)

    candidates.sort(key=lambda c: -c[0])
    print(f"[2/4] {len(candidates)} candidates total; selecting top {args.max_topics}")
    top = candidates[: args.max_topics]

    print(f"[3/4] framing each as TM topic via claude")
    framed = []
    for i, (score, kind, data) in enumerate(top, 1):
        try:
            if kind == "calendar":
                facts = build_verified_facts_calendar(data, run_iso_utc)
                source_summary = f"TaiwanMerch curated cultural calendar — {data['id']} ({data['name']})"
            else:
                facts = build_verified_facts_reddit(data, run_iso_utc)
                source_summary = f"Reddit r/{data['subreddit']} top weekly — score {data['score']}"
            payload = frame_as_tm_topic(kind, data, brand_cfg)
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
        f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} tm_topic_researcher "
        f"candidates={len(candidates)} written={sum(1 for r in results if r and 'id' in r)} "
        f"dryrun={args.dryrun}"
    )
    print(summary)
    if not args.dryrun:
        ledger_append(summary)


if __name__ == "__main__":
    main()
