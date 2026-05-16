#!/usr/bin/env python3
"""WLH topic researcher — seeds Airtable Social Queue weekly from live sky.

Runs Mon 06:00 Asia/Taipei via the secondbrain-dispatcher. For each fire:
  1. Pull today's sky + scan next 14 days via skyfield + DE421 ephemeris.
  2. Identify the 2-3 most editorially-worthy events for the upcoming week:
       - Fresh outer-planet ingress (within ±14 days of crossing a sign boundary)
       - Retrograde station (planet's speed sign flips)
       - Next New Moon or Full Moon in window
       - Active retrograde of a slow/medium planet (Mercury Rx is universal)
  3. Ask Claude to frame each as a WLH-shaped topic + angle + notes
     anchored on real degrees + wearable-merch close (per
     feedback_wlh_topics_from_sky.md).
  4. POST each as a Status=Ready row to Airtable Social Queue.

Output: 2-3 new Ready rows per week. WLH dispatcher (Wed/Sun 19:00 Taipei)
then pulls from queue with zero Jon input.

Usage:
    wlh_topic_researcher.py [--dryrun] [--start YYYY-MM-DD] [--window-days 14] [--max-topics 3]

Requires:
    python3 (with required deps) (has skyfield + DE421)
    <repo-root>/.env (or `$SP2_ENV_PATH`) (AIRTABLE_API_KEY, ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from today_sky import compute_sky, SIGNS, PLANET_SCENTS, ecliptic_lon_deg, PLANETS  # noqa: E402

# skyfield is loaded lazily inside the helpers that need it so non-WLH usage
# of this module doesn't pay the import cost.

# Delimiters the pipeline parses to extract verified_facts back out of the
# Research Brief text. Keep these stable — sp2_pipeline.py greps for them.
VERIFIED_FACTS_OPEN = "[VERIFIED_FACTS_JSON_START]"
VERIFIED_FACTS_CLOSE = "[VERIFIED_FACTS_JSON_END]"

# Granularity for scanning the ephemeris backward to find a planet's prior
# window in a given sign. Smaller step = more skyfield evaluations = slower
# but never skips a fast-moving sign window. 15 days is safe for everything
# except Mercury, which occasionally clips a sign in ~14 days when retrograde.
_BACK_STEP_DAYS_BY_PLANET = {
    "Sun": 5,
    "Mercury": 5,
    "Venus": 7,
    "Mars": 10,
    "Jupiter": 30,
    "Saturn": 60,
    "Uranus": 90,
    "Neptune": 90,
    "Pluto": 180,
}

# Bound on how far back we'll scan when looking for the prior occurrence of
# a planet in a sign. One synodic-ish cycle plus margin.
_MAX_YEARS_BACK_BY_PLANET = {
    "Sun": 2,
    "Mercury": 2,
    "Venus": 2,
    "Mars": 3,
    "Jupiter": 14,
    "Saturn": 32,
    "Uranus": 90,
    "Neptune": 170,
    "Pluto": 260,
}

# Reverse-lookup of skyfield ephemeris key by display name.
_PLANET_EPHEM_KEY = {display: key for key, display in PLANETS}

ENV_PATH = Path(os.environ.get("SP2_ENV_PATH", str(Path(__file__).resolve().parent.parent.parent / ".env")))
WLH_AIRTABLE_BASE = "appCgtZBsLNtRWzRV"
WLH_AIRTABLE_TABLE = "tblxRSXH1Tlzyfxvh"
WLH_BRAND_DIR = Path(__file__).resolve().parent.parent
LEDGER = Path(os.environ.get("SP2_HEARTBEAT_DIR", str(Path(__file__).resolve().parent.parent.parent / "heartbeat"))) / "wlh_topic_researcher_ledger.md"

# Cultural/editorial weight per planet ingress, anchored on cycle length.
# Outer planets = rarer = bigger generational marker = more WLH-worthy.
INGRESS_RARITY = {
    "Pluto":    100,  # 248-yr cycle, ~20yr in sign
    "Neptune":   95,  # 165-yr cycle, ~14yr in sign
    "Uranus":    90,  # 84-yr cycle, ~7yr in sign
    "Saturn":    70,  # 29-yr cycle, ~2.5yr in sign
    "Jupiter":   55,  # 12-yr cycle, ~1yr in sign
    "Mars":      35,  # 2-yr cycle, ~6wk-8mo in sign
    "Sun":       25,  # 1-yr cycle, 30-day in sign — birthday season hook
    "Venus":     22,  # ~1-month in sign average
    "Mercury":   20,  # ~3wk in sign
}

# Retrograde station weight — moments when a planet pivots are huge in astrology
# culture (especially Mercury Rx).
RETRO_STATION_RARITY = {
    "Mercury": 60,
    "Venus":   55,
    "Mars":    50,
    "Jupiter": 40,
    "Saturn":  45,
    "Uranus":  50,
    "Neptune": 50,
    "Pluto":   60,
}

MOON_PHASE_RARITY = {
    "New Moon":  35,
    "Full Moon": 40,
}


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
    h = {"User-Agent": "wlh-topic-researcher/1.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def scan_window(start_dt: datetime, days: int) -> list[dict]:
    """Return per-day sky snapshots for [start, start+days]."""
    snapshots = []
    for d in range(days + 1):
        dt = start_dt + timedelta(days=d)
        sky = compute_sky(dt.strftime("%Y-%m-%dT%H:%M:%S"))
        sky["_day_offset"] = d
        sky["_iso"] = dt.strftime("%Y-%m-%d")
        snapshots.append(sky)
    return snapshots


def detect_events(snapshots: list[dict]) -> list[dict]:
    """Walk snapshots and emit dated events with rarity scores."""
    if len(snapshots) < 2:
        return []
    events = []
    n = len(snapshots)

    by_planet_today = {p["planet"]: p for p in snapshots[0]["planets"]}

    # 1. INGRESSES — detect when a planet's sign changes between two days
    for i in range(n - 1):
        cur = {p["planet"]: p for p in snapshots[i]["planets"]}
        nxt = {p["planet"]: p for p in snapshots[i + 1]["planets"]}
        for name, p_cur in cur.items():
            p_nxt = nxt.get(name)
            if not p_nxt:
                continue
            if p_cur["sign"] != p_nxt["sign"] and name != "Moon":
                events.append({
                    "type": "ingress",
                    "planet": name,
                    "from_sign": p_cur["sign"],
                    "to_sign": p_nxt["sign"],
                    "iso": snapshots[i + 1]["_iso"],
                    "days_until": snapshots[i + 1]["_day_offset"],
                    "rarity": INGRESS_RARITY.get(name, 15),
                })

    # 2. FRESH RECENT INGRESSES — outer planet under 3° in a sign right now is "fresh"
    for name, p in by_planet_today.items():
        if name in ("Sun", "Moon"):
            continue
        if p["degree_in_sign"] < 3.0 and INGRESS_RARITY.get(name, 0) >= 70:
            events.append({
                "type": "fresh_ingress",
                "planet": name,
                "to_sign": p["sign"],
                "degree": p["degree_in_sign"],
                "iso": snapshots[0]["_iso"],
                "days_until": 0,
                "rarity": INGRESS_RARITY.get(name, 50) + int((3.0 - p["degree_in_sign"]) * 10),
            })

    # 3. RETROGRADE STATIONS — speed crosses zero between two snapshots
    for i in range(n - 1):
        for p_cur in snapshots[i]["planets"]:
            name = p_cur["planet"]
            if name in ("Sun", "Moon"):
                continue
            p_nxt = next((p for p in snapshots[i + 1]["planets"] if p["planet"] == name), None)
            if not p_nxt:
                continue
            if (p_cur["speed_deg_per_day"] > 0) != (p_nxt["speed_deg_per_day"] > 0):
                events.append({
                    "type": "retrograde_station",
                    "planet": name,
                    "direction": "Rx" if p_nxt["speed_deg_per_day"] < 0 else "Direct",
                    "iso": snapshots[i + 1]["_iso"],
                    "days_until": snapshots[i + 1]["_day_offset"],
                    "rarity": RETRO_STATION_RARITY.get(name, 30),
                })

    # 4. MOON PHASES — find phase-boundary crossings
    for i in range(n - 1):
        a_cur = snapshots[i]["moon"]["sun_moon_angle_deg"]
        a_nxt = snapshots[i + 1]["moon"]["sun_moon_angle_deg"]
        # Detect crossing of 0 (New Moon) or 180 (Full Moon).
        for target, name in [(0, "New Moon"), (180, "Full Moon")]:
            # Normalize to [-180, 180] for cleaner crossing detection
            def _delta(angle, t):
                return (angle - t + 540) % 360 - 180
            d_cur = _delta(a_cur, target)
            d_nxt = _delta(a_nxt, target)
            if d_cur * d_nxt < 0 and abs(d_cur - d_nxt) < 30:
                events.append({
                    "type": "moon_phase",
                    "phase": name,
                    "sign": snapshots[i + 1]["moon"]["sign"],
                    "iso": snapshots[i + 1]["_iso"],
                    "days_until": snapshots[i + 1]["_day_offset"],
                    "rarity": MOON_PHASE_RARITY.get(name, 20),
                })

    # 5. ACTIVE RETROGRADES — any planet currently in retrograde is a usable angle
    for p in by_planet_today.values():
        if p["retrograde"] and p["planet"] not in ("Sun", "Moon"):
            # Lower priority than stations, but still usable
            events.append({
                "type": "active_retrograde",
                "planet": p["planet"],
                "sign": p["sign"],
                "degree": p["degree_in_sign"],
                "iso": snapshots[0]["_iso"],
                "days_until": 0,
                "rarity": (RETRO_STATION_RARITY.get(p["planet"], 25) // 2),
            })

    events.sort(key=lambda e: -e["rarity"])
    return events


def dedup_events(events: list[dict], max_n: int) -> list[dict]:
    """Pick top N events, but never two from the same planet."""
    out = []
    seen_planets = set()
    for e in events:
        planet = e.get("planet") or e.get("phase")
        if planet in seen_planets:
            continue
        seen_planets.add(planet)
        out.append(e)
        if len(out) >= max_n:
            break
    return out


def _load_skyfield():
    """Lazy skyfield loader. Returns (eph, ts)."""
    from skyfield.api import load  # type: ignore
    ts = load.timescale()
    eph = load("de421.bsp")
    return eph, ts


def _sign_idx_at(eph, ts, planet_ephem_key: str, dt: datetime) -> int:
    """Zodiac sign index (0..11) of planet at dt."""
    lon = ecliptic_lon_deg(eph, ts, planet_ephem_key, ts.from_datetime(dt))
    return int(lon // 30)


def _bisect_sign_change(eph, ts, planet_ephem_key: str, dt_in: datetime, dt_out: datetime,
                        target_sign_idx: int, max_iters: int = 22) -> datetime:
    """Given dt_in (planet IN target_sign) and dt_out (planet OUT of target_sign),
    bisect to within ~1 hour to find the moment of the sign-boundary crossing.
    Returns the FIRST dt at which planet is no longer in target_sign (egress) OR
    the FIRST dt at which planet is in target_sign (ingress), depending on which
    side is "in".
    """
    lo, hi = dt_in, dt_out
    for _ in range(max_iters):
        mid = lo + (hi - lo) / 2
        if _sign_idx_at(eph, ts, planet_ephem_key, mid) == target_sign_idx:
            lo = mid
        else:
            hi = mid
    return hi


def find_current_window(eph, ts, planet_display: str, target_sign: str,
                         anchor_dt: datetime, max_years: float = 3.0) -> dict | None:
    """Find the contiguous date window when planet is in target_sign, anchored at anchor_dt.
    Returns {start_iso, end_iso, duration_days, duration_weeks, duration_months_approx, refined: bool}
    or None if anchor_dt is not actually in target_sign (data integrity check).

    Note: outer planets can retrograde back out of a sign briefly. This returns the
    "anchored contiguous slab"; we set refined=False if we hit the max_years bound,
    indicating the window extends beyond what we scanned.
    """
    ephem_key = _PLANET_EPHEM_KEY.get(planet_display)
    if not ephem_key:
        return None
    target_idx = SIGNS.index(target_sign)
    step_days = _BACK_STEP_DAYS_BY_PLANET.get(planet_display, 15)
    max_back = timedelta(days=int(365 * max_years))

    if _sign_idx_at(eph, ts, ephem_key, anchor_dt) != target_idx:
        return None

    # Walk backward until we leave the sign, then bisect.
    cur = anchor_dt
    bounded_start = False
    while True:
        prev = cur - timedelta(days=step_days)
        if anchor_dt - prev > max_back:
            bounded_start = True
            start_dt = prev
            break
        if _sign_idx_at(eph, ts, ephem_key, prev) != target_idx:
            start_dt = _bisect_sign_change(eph, ts, ephem_key, cur, prev, target_idx)
            break
        cur = prev

    # Walk forward until we leave the sign, then bisect.
    cur = anchor_dt
    bounded_end = False
    while True:
        nxt = cur + timedelta(days=step_days)
        if nxt - anchor_dt > max_back:
            bounded_end = True
            end_dt = nxt
            break
        if _sign_idx_at(eph, ts, ephem_key, nxt) != target_idx:
            end_dt = _bisect_sign_change(eph, ts, ephem_key, cur, nxt, target_idx)
            break
        cur = nxt

    days = max(1, (end_dt - start_dt).days)
    return {
        "start_iso": start_dt.strftime("%Y-%m-%d"),
        "end_iso": end_dt.strftime("%Y-%m-%d"),
        "duration_days": days,
        "duration_weeks": round(days / 7, 1),
        "duration_months_approx": round(days / 30.4375, 1),
        "refined": not (bounded_start or bounded_end),
    }


def find_prior_window(eph, ts, planet_display: str, target_sign: str,
                       before_dt: datetime) -> dict | None:
    """Find the most recent past window when planet was in target_sign, ending before before_dt.
    Returns {start_iso, end_iso, year, duration_days, refined: bool} or None if not found within
    the planet's known cycle.
    """
    ephem_key = _PLANET_EPHEM_KEY.get(planet_display)
    if not ephem_key:
        return None
    target_idx = SIGNS.index(target_sign)
    step_days = _BACK_STEP_DAYS_BY_PLANET.get(planet_display, 15)
    max_years = _MAX_YEARS_BACK_BY_PLANET.get(planet_display, 30)
    max_back = timedelta(days=int(365 * max_years))

    # Walk backward in step_days chunks until we land inside the sign.
    cur = before_dt - timedelta(days=step_days)
    hit = None
    while before_dt - cur < max_back:
        if _sign_idx_at(eph, ts, ephem_key, cur) == target_idx:
            hit = cur
            break
        cur -= timedelta(days=step_days)
    if hit is None:
        return None

    # Expand the window around hit_dt.
    win = find_current_window(eph, ts, planet_display, target_sign, hit, max_years=max_years)
    if not win:
        return None
    return {
        "start_iso": win["start_iso"],
        "end_iso": win["end_iso"],
        "year": int(win["start_iso"][:4]),
        "duration_days": win["duration_days"],
        "duration_weeks": win["duration_weeks"],
        "duration_months_approx": win["duration_months_approx"],
        "refined": win["refined"],
    }


def _day_of_week_iso(iso: str) -> str:
    """ISO date to weekday name."""
    return datetime.fromisoformat(iso).strftime("%A")


def build_verified_facts(event: dict, today_sky_obj: dict, run_iso_utc: str) -> dict:
    """Compose a structured fact dict from the chosen event + today's sky.

    The dict is the canonical ephemeris-grounded truth Claude will be required
    to quote literally. Anything beyond these fields is interpretive layer.
    """
    facts: dict = {
        "source": "skyfield + JPL DE421 ephemeris via today_sky.py",
        "source_run_iso_utc": run_iso_utc,
        "today_iso": today_sky_obj["date_utc"],
        "today_sun_sign": today_sky_obj["sun_sign"],
        "today_moon_sign": today_sky_obj["moon"]["sign"],
        "today_moon_phase": today_sky_obj["moon"]["phase_name"],
        "today_illumination_pct": today_sky_obj["moon"]["illumination_pct"],
        "today_retrogrades": today_sky_obj["retrogrades"],
        "today_planet_positions": {
            p["planet"]: {
                "sign": p["sign"],
                "degree_in_sign": round(p["degree_in_sign"], 2),
                "retrograde": p["retrograde"],
            }
            for p in today_sky_obj["planets"]
        },
        "event": {
            "type": event["type"],
            "days_until": event["days_until"],
            "iso_date": event["iso"],
            "day_of_week": _day_of_week_iso(event["iso"]),
        },
    }

    et = event["type"]
    planet = event.get("planet")
    if planet:
        facts["event"]["planet"] = planet
    if et == "ingress":
        facts["event"].update({
            "from_sign": event["from_sign"],
            "to_sign": event["to_sign"],
        })
    elif et == "fresh_ingress":
        facts["event"].update({
            "to_sign": event["to_sign"],
            "degree_in_sign_today": round(event["degree"], 2),
        })
    elif et == "retrograde_station":
        facts["event"].update({
            "direction": event["direction"],
        })
    elif et == "moon_phase":
        facts["event"].update({
            "phase": event["phase"],
            "sign": event["sign"],
        })
    elif et == "active_retrograde":
        facts["event"].update({
            "sign": event["sign"],
            "degree_in_sign_today": round(event["degree"], 2),
        })

    # Compute current + prior window for events that have a planet + target_sign.
    target_sign = event.get("to_sign") or event.get("sign")
    if planet and target_sign and planet not in ("Moon",):
        try:
            eph, ts = _load_skyfield()
            anchor_dt = datetime.fromisoformat(event["iso"]).replace(tzinfo=timezone.utc)
            # For an ingress event, anchor at iso+1 day so we're inside the new sign.
            if et == "ingress":
                anchor_dt = anchor_dt + timedelta(days=1)
            cur_win = find_current_window(eph, ts, planet, target_sign, anchor_dt)
            if cur_win:
                facts["current_window"] = {
                    "planet": planet,
                    "sign": target_sign,
                    **cur_win,
                }
            # Prior occurrence: start scanning from start of current window.
            scan_before = datetime.fromisoformat(cur_win["start_iso"]).replace(tzinfo=timezone.utc) if cur_win else anchor_dt
            scan_before -= timedelta(days=7)  # don't catch the tail of the current window
            prior = find_prior_window(eph, ts, planet, target_sign, scan_before)
            if prior:
                facts["prior_window"] = {
                    "planet": planet,
                    "sign": target_sign,
                    **prior,
                }
        except Exception as exc:  # pragma: no cover — never block a fire on ephemeris compute
            facts["window_compute_error"] = str(exc)[:200]

    return facts


def describe_event(e: dict) -> str:
    if e["type"] == "ingress":
        return f"{e['planet']} ingresses {e['to_sign']} on {e['iso']} (in {e['days_until']} days). Leaves {e['from_sign']}."
    if e["type"] == "fresh_ingress":
        return f"{e['planet']} just entered {e['to_sign']} ({e['degree']:.2f}° in — fresh ingress)."
    if e["type"] == "retrograde_station":
        return f"{e['planet']} stations {e['direction']} on {e['iso']} (in {e['days_until']} days)."
    if e["type"] == "moon_phase":
        return f"{e['phase']} in {e['sign']} on {e['iso']} (in {e['days_until']} days)."
    if e["type"] == "active_retrograde":
        return f"{e['planet']} retrograde at {e['degree']:.2f}° {e['sign']} (ongoing)."
    return json.dumps(e)


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


def frame_event_as_wlh(event: dict, brand_cfg: dict, today_sky: dict, verified_facts: dict) -> dict:
    """Ask Claude to write a WLH-shaped topic/angle/notes for this event."""
    planet = event.get("planet", "")
    scent_inspiration = ", ".join(PLANET_SCENTS.get(planet, [])[:3])
    fact_block = describe_event(event)

    full_sky_block = "\n".join(
        f"  {p['planet']:8s} {p['sign']:12s} {p['degree_in_sign']:5.2f}deg  Rx={p['retrograde']}"
        for p in today_sky["planets"]
    )

    cur_win = verified_facts.get("current_window") or {}
    prior_win = verified_facts.get("prior_window") or {}
    window_block = ""
    if cur_win:
        window_block += (
            f"\n  CURRENT TRANSIT WINDOW: {cur_win['planet']} in {cur_win['sign']} "
            f"from {cur_win['start_iso']} to {cur_win['end_iso']} "
            f"({cur_win['duration_weeks']} weeks / ~{cur_win['duration_months_approx']} months)."
        )
    if prior_win:
        window_block += (
            f"\n  PRIOR OCCURRENCE: last time {prior_win['planet']} was in {prior_win['sign']} "
            f"was {prior_win['start_iso']} to {prior_win['end_iso']} (year {prior_win['year']})."
        )

    prompt = f"""You are writing a WLH (We Love Horoscope) SP2 video topic.

DOCTRINE (Jon, locked 2026-05-15): the script TELLS THE HOROSCOPE, NEVER SELLS THE MERCH.
WLH's products exist (tees, hoodies, stickers, mugs, prints) but the script content
never references them — not in body, not in close, not in beat 7. The brand sells
itself via outro card + IG bio. The script's only job is to be valuable horoscope
content: practical per-sign guidance, transit mechanics, cosmic stories, generational
markers. Never herbal/aromatherapy framing either (that's OA/UtamaSpice territory).

BRAND VOICE: {brand_cfg['brand_voice']}
ANTI-PATTERNS: {'; '.join(brand_cfg['anti_patterns'])}

THIS WEEK'S TARGET EVENT (real ephemeris data, do not invent):
  {fact_block}
  Event type: {event['type']}
  Days until: {event['days_until']}{window_block}

TODAY'S FULL SKY (for context — do not list all of this, just use to anchor):
{full_sky_block}

DESIGN-INSPIRATION SCENT KEYWORDS for {planet} (HERMETIC ASSOCIATION — use for
visual mood / palette / typography reference ONLY, NEVER as ritual prescription
in script copy): {scent_inspiration}

Produce three fields for the Airtable Social Queue row. Each as plain text,
no markdown, no quotes, no emojis. Anchor on the real numerical data.

Return ONLY valid JSON, no preamble, no fences, this exact schema:
{{
  "topic": "<one-line headline, under 70 chars, ALL the real numerical data>",
  "angle": "<2-4 sentences. Specific degrees, dates, prior occurrences. Mechanics, not woo. Frame as horoscope content (per-sign guidance, transit mechanics, generational shift), never as a product pitch.>",
  "key_points": ["<bullet 1: real degree/date data (literal value)>", "<bullet 2: prior-cycle history with year>", "<bullet 3: per-sign practical guidance or generational angle (NO product)>"],
  "visual_notes": "<visual direction for FLUX illustrations: cosmic palette, glyphs, era references, NO faces, no product imagery. 2-3 sentences.>"
}}"""

    raw = call_claude(prompt)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _rarity_to_priority(rarity: int) -> int:
    """Map detector rarity (0-130) to Airtable Priority field (1-10)."""
    if rarity >= 90:
        return 10
    if rarity >= 60:
        return 9
    if rarity >= 45:
        return 8
    if rarity >= 30:
        return 7
    if rarity >= 20:
        return 6
    return 5


def build_research_brief(payload: dict, event: dict, verified_facts: dict) -> str:
    """Compose the structured Research Brief text block that WLH SQ rows use.

    Embeds a delimited JSON block at the end containing the ephemeris-grounded
    facts. The pipeline (sp2_pipeline.gen_script_plan) parses this block out and
    injects the facts directly into Claude's script-gen prompt with a rule:
    every numerical claim must be quoted literally. Anything beyond these facts
    is the interpretive layer.
    """
    key_points = payload.get("key_points") or []
    kp_block = "\n".join(f"- {kp}" for kp in key_points)
    facts_json = json.dumps(verified_facts, indent=2, sort_keys=True)
    return (
        f"SOURCE: Live Sky (skyfield + JPL DE421 ephemeris via today_sky.py)\n"
        f"\n"
        f"ANGLE: {payload['angle']}\n"
        f"\n"
        f"KEY POINTS:\n{kp_block}\n"
        f"\n"
        f"EVENT DATA (live ephemeris, do not invent): {describe_event(event)}\n"
        f"\n"
        f"PLATFORM FIT: Instagram Reel (SP2 vertical video, 30s, FLUX illustration mode).\n"
        f"\n"
        f"VISUAL NOTES: {payload.get('visual_notes', '')}\n"
        f"\n"
        f"BRAND RULE (hard): SCRIPT TELLS THE HOROSCOPE, NEVER SELLS THE MERCH. "
        f"Zero product mention in any beat (no tee/hoodie/sticker/mug/print, no 'wear this'). "
        f"Beat 7 is a poetic insight, not a sales line. (Jon doctrine 2026-05-15.)\n"
        f"\n"
        f"{VERIFIED_FACTS_OPEN}\n{facts_json}\n{VERIFIED_FACTS_CLOSE}"
    )


def post_airtable_row(env: dict, topic_payload: dict, event: dict, verified_facts: dict, dryrun: bool) -> dict:
    """Write one Status=Queued row to WLH Social Queue.

    Schema (probed 2026-05-13): Status singleSelect = ['Queued','In Progress','Used','Skipped'].
    Source singleSelect = ['Reddit','TikTok','Pinterest','Wikipedia','Google News','Competitor IG','People Also Ask']
    — no 'Live Sky' choice and PAT can't create new ones. Skip Source field; the
    Research Brief itself labels SOURCE: Live Sky / today_sky.py.
    """
    url = f"https://api.airtable.com/v0/{WLH_AIRTABLE_BASE}/{urllib.parse.quote(WLH_AIRTABLE_TABLE)}"
    fields = {
        "Status": "Queued",
        "Topic": topic_payload["topic"],
        "Research Brief": build_research_brief(topic_payload, event, verified_facts),
        "Priority": _rarity_to_priority(event["rarity"]),
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
    ap.add_argument("--dryrun", action="store_true", help="Don't write to Airtable, print only")
    ap.add_argument("--start", default=None, help="ISO date to start scanning from (default = today UTC)")
    ap.add_argument("--window-days", type=int, default=14, help="Days to scan forward")
    ap.add_argument("--max-topics", type=int, default=3, help="Max topics to write")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    for k in ("AIRTABLE_API_KEY",):
        if not env.get(k):
            print(f"ERROR: missing env var {k} at {ENV_PATH}", file=sys.stderr)
            sys.exit(1)

    brand_cfg = json.loads((WLH_BRAND_DIR / "brand.json").read_text())

    if args.start:
        start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    else:
        start_dt = datetime.now(timezone.utc)

    print(f"[1/4] scanning sky {start_dt.strftime('%Y-%m-%d')} → +{args.window_days}d")
    snapshots = scan_window(start_dt, args.window_days)
    print(f"  {len(snapshots)} snapshots computed")

    print(f"[2/4] detecting events")
    events = detect_events(snapshots)
    print(f"  {len(events)} raw events detected (top 6):")
    for e in events[:6]:
        print(f"    [{e['rarity']:3d}] {describe_event(e)}")
    top_events = dedup_events(events, args.max_topics)
    print(f"  {len(top_events)} top events selected after planet dedup")

    print(f"[3/4] framing each as WLH topic via claude (+ ephemeris verified_facts)")
    run_iso_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    framed = []
    for i, e in enumerate(top_events, 1):
        try:
            facts = build_verified_facts(e, snapshots[0], run_iso_utc)
            cur_win = facts.get("current_window") or {}
            if cur_win:
                print(f"  [{i}/{len(top_events)}] window: {cur_win.get('start_iso')} → {cur_win.get('end_iso')} ({cur_win.get('duration_weeks')}w)")
            payload = frame_event_as_wlh(e, brand_cfg, snapshots[0], facts)
            framed.append((e, payload, facts))
            print(f"  [{i}/{len(top_events)}] {payload['topic'][:80]}")
        except Exception as exc:
            print(f"  [{i}/{len(top_events)}] FAILED: {exc}")

    print(f"[4/4] writing to Airtable Social Queue (dryrun={args.dryrun})")
    results = []
    for e, payload, facts in framed:
        try:
            res = post_airtable_row(env, payload, e, facts, args.dryrun)
            results.append({"event": e, "payload": payload, "airtable": res})
            rec_id = (res or {}).get("id") if not args.dryrun else "(dryrun)"
            print(f"  ✓ {payload['topic'][:60]} → {rec_id}")
        except Exception as exc:
            print(f"  ✗ {payload['topic'][:60]} — {exc}")

    summary = (
        f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} wlh_topic_researcher "
        f"start={start_dt.strftime('%Y-%m-%d')} window={args.window_days}d "
        f"events_seen={len(events)} written={sum(1 for r in results if 'id' in (r.get('airtable') or {}))} "
        f"dryrun={args.dryrun}"
    )
    print(summary)
    if not args.dryrun:
        ledger_append(summary)


if __name__ == "__main__":
    main()
