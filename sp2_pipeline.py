#!/usr/bin/env python3
"""SP2 video pipeline orchestrator.

Runs the locked HyperFrames pipeline for a single brand: topic → 7-beat script →
per-beat media → ElevenLabs VO → WhisperX word timestamps → MusicGen → render →
Cloudinary upload → Metricool schedule.

Usage:
    sp2_pipeline.py --brand organicaromas [--script-only] [--dryrun]

Flags:
    --brand        Brand slug, must match a project dir under this script's parent.
    --script-only  Stop after writing the script + media-query plan JSON; no API calls.
    --dryrun       Run every step except the final Metricool schedule (logs intended post).
                   Also honored by the DRYRUN sentinel file at $HEARTBEAT_DIR/DRYRUN.

Reads global creds from $SP2_ENV_PATH (or sibling .env, or ~/second-brain-production/.env as last fallback).
Reads brand config from <project_dir>/brand.json.
Outputs render mp4 to <project_dir>/renders/, ledger entry to $HEARTBEAT_DIR/ledger.md.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import urllib.request
import urllib.parse
import urllib.error

# -------------------------------------------------------------------- constants
def _find_env_path() -> Path:
    # Preference order: SP2_ENV_PATH override → sibling .env (jonops-vps layout) → second-brain fallback
    if os.environ.get("SP2_ENV_PATH"):
        return Path(os.environ["SP2_ENV_PATH"])
    sibling = Path(__file__).resolve().parent / ".env"
    if sibling.exists():
        return sibling
    return Path(Path.home() / "second-brain-production/.env")


SECOND_BRAIN_ENV = _find_env_path()


def _find_heartbeat_dir() -> Path:
    # Canonical second-brain location takes precedence; jonops-vps falls back to sibling.
    canonical = Path(os.environ.get("SP2_HEARTBEAT_DIR", str(Path.home() / "SecondBrain/Memory/heartbeat")))
    if canonical.exists():
        return canonical
    return Path(__file__).resolve().parent / "heartbeat"


HEARTBEAT_DIR = _find_heartbeat_dir()
DRYRUN_SENTINEL = HEARTBEAT_DIR / "DRYRUN"
LEDGER = HEARTBEAT_DIR / "ledger.md"
ROOT = Path(__file__).resolve().parent

WHISPERX_MODEL = "victor-upmeet/whisperx"
WHISPERX_MODEL_VERSION = (
    "84d2ad2d6194fe98a17d2b60bef1c7f910c46b2f6fd38996ca457afd9c8abfcb"
)
MUSICGEN_MODEL_VERSION = (
    "671ac645ce5e552cc63a54a2bbff63fcf798043055d2dac5fc9e36a837eedcfb"
)
USER_AGENT = "sp2-pipeline/1.0 (python urllib)"


# -------------------------------------------------------------------- env utils
def load_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def require(env: dict, *keys: str) -> dict:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        die(f"missing required env: {missing}")
    return env


def die(msg: str, code: int = 1) -> None:
    print(f"sp2_pipeline ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# -------------------------------------------------------------------- http
def http_json(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: bytes | str | None = None,
    timeout: int = 60,
) -> Any:
    if isinstance(body, str):
        body = body.encode("utf-8")
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def http_download(url: str, dest: Path, timeout: int = 60) -> Path:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        dest.write_bytes(r.read())
    return dest


# -------------------------------------------------------------------- topic
# Markers a brand's topic researcher (currently wlh_topic_researcher.py) embeds
# into the Research Brief / angle text to carry ephemeris-grounded facts forward
# to script generation. The block between markers is JSON; the markers themselves
# are stripped from the angle text before it reaches Claude.
_VERIFIED_FACTS_OPEN = "[VERIFIED_FACTS_JSON_START]"
_VERIFIED_FACTS_CLOSE = "[VERIFIED_FACTS_JSON_END]"


def _extract_verified_facts(text: str) -> tuple[str, dict]:
    """Split verified_facts JSON out of text. Returns (cleaned_text, facts_dict).
    Missing markers → ({}, original_text). Malformed JSON between markers is
    silently dropped so a corrupted brief never blocks a fire.
    """
    if _VERIFIED_FACTS_OPEN not in text or _VERIFIED_FACTS_CLOSE not in text:
        return text, {}
    open_at = text.index(_VERIFIED_FACTS_OPEN)
    close_at = text.index(_VERIFIED_FACTS_CLOSE, open_at)
    payload = text[open_at + len(_VERIFIED_FACTS_OPEN):close_at].strip()
    cleaned = (text[:open_at] + text[close_at + len(_VERIFIED_FACTS_CLOSE):]).rstrip()
    try:
        facts = json.loads(payload)
    except json.JSONDecodeError:
        facts = {}
    return cleaned, facts


def pick_topic(brand_cfg: dict, env: dict) -> dict:
    """Read next ready row from the brand's social queue (Airtable for OA)."""
    sq = brand_cfg["social_queue"]
    if sq["type"] != "airtable":
        die(f"unsupported social_queue type: {sq['type']}")
    base = sq["base_id"]
    table = sq["table_id"]
    api_key = env.get("AIRTABLE_API_KEY") or die("missing AIRTABLE_API_KEY")
    formula = f"AND({{{sq['status_field']}}}='{sq['ready_value']}')"
    url = (
        f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}"
        f"?maxRecords=5&filterByFormula={urllib.parse.quote(formula)}"
    )
    sort_field = sq.get("sort_field")
    if sort_field:
        sort_direction = sq.get("sort_direction", "asc")
        url += (
            f"&sort%5B0%5D%5Bfield%5D={urllib.parse.quote(sort_field)}"
            f"&sort%5B0%5D%5Bdirection%5D={urllib.parse.quote(sort_direction)}"
        )
    data = http_json(url, headers={"Authorization": f"Bearer {api_key}"})
    records = data.get("records") or []
    if not records:
        die("social queue empty — no Ready topics for SP2")
    rec = records[0]
    fields = rec["fields"]
    angle_raw = fields.get(sq.get("angle_field", ""), "").strip()
    notes_raw = fields.get(sq.get("notes_field", ""), "").strip()
    # Verified facts may be embedded as a delimited JSON block in either field
    # by the brand's topic researcher (WLH currently does this). Strip them out
    # of the text fields so the prompt stays clean; carry as structured data.
    angle_clean, facts_from_angle = _extract_verified_facts(angle_raw)
    notes_clean, facts_from_notes = _extract_verified_facts(notes_raw)
    return {
        "id": rec["id"],
        "topic": fields.get(sq["topic_field"], "").strip(),
        "angle": angle_clean,
        "notes": notes_clean,
        "verified_facts": facts_from_angle or facts_from_notes or {},
    }


def _patch_topic_status(brand_cfg: dict, env: dict, record_id: str, status_value: str) -> None:
    sq = brand_cfg["social_queue"]
    base = sq["base_id"]
    table = sq["table_id"]
    api_key = env["AIRTABLE_API_KEY"]
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}/{record_id}"
    body = json.dumps({"fields": {sq["status_field"]: status_value}})
    http_json(
        url,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        body=body,
    )


def mark_topic_in_progress(brand_cfg: dict, env: dict, record_id: str) -> None:
    value = brand_cfg["social_queue"].get("in_progress_value", "In Progress")
    _patch_topic_status(brand_cfg, env, record_id, value)


def mark_topic_used(brand_cfg: dict, env: dict, record_id: str) -> None:
    value = brand_cfg["social_queue"].get("used_value", "Used")
    _patch_topic_status(brand_cfg, env, record_id, value)


def mark_topic_revert_to_queued(brand_cfg: dict, env: dict, record_id: str) -> None:
    value = brand_cfg["social_queue"]["ready_value"]
    _patch_topic_status(brand_cfg, env, record_id, value)


# -------------------------------------------------------------------- claude
def call_claude(prompt: str, timeout: int = 240) -> str:
    """Invoke the local claude CLI (--print mode). Returns trimmed stdout."""
    proc = subprocess.run(
        ["claude", "--print"],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        die(f"claude --print exit {proc.returncode}: {proc.stderr[:400]}")
    return proc.stdout.strip()


def _format_verified_facts_block(facts: dict) -> str:
    """Render verified_facts as a compact, prompt-shaped block. Returns "" if no facts."""
    if not facts:
        return ""
    lines = ["VERIFIED FACTS (literal ground truth — quote these EXACT values, do not paraphrase numbers, do not invent dates, do not round):"]
    lines.append(f"  Source: {facts.get('source', 'unknown')}")
    if facts.get("today_iso"):
        lines.append(f"  Today (UTC): {facts['today_iso']} — Sun in {facts.get('today_sun_sign', '?')}, Moon in {facts.get('today_moon_sign', '?')} ({facts.get('today_moon_phase', '?')}, {facts.get('today_illumination_pct', 0)}% illuminated)")
    if facts.get("today_retrogrades"):
        lines.append(f"  Currently retrograde: {', '.join(facts['today_retrogrades'])}")
    ev = facts.get("event") or {}
    if ev:
        ev_bits = [f"type={ev.get('type')}"]
        for k in ("planet", "from_sign", "to_sign", "sign", "phase", "direction", "iso_date", "day_of_week", "days_until", "degree_in_sign_today"):
            if k in ev:
                ev_bits.append(f"{k}={ev[k]}")
        lines.append("  Event: " + ", ".join(ev_bits))
    cur = facts.get("current_window") or {}
    if cur:
        lines.append(
            f"  Current window: {cur['planet']} in {cur['sign']} from {cur['start_iso']} to {cur['end_iso']} — "
            f"{cur['duration_days']} days = {cur['duration_weeks']} weeks ≈ {cur['duration_months_approx']} months"
        )
    pri = facts.get("prior_window") or {}
    if pri:
        lines.append(
            f"  Prior occurrence: {pri['planet']} was last in {pri['sign']} from {pri['start_iso']} to {pri['end_iso']} (year {pri['year']})"
        )
    pos = facts.get("today_planet_positions") or {}
    if pos:
        lines.append("  Full sky today:")
        for planet, p in pos.items():
            lines.append(f"    {planet}: {p['sign']} {p['degree_in_sign']}deg{' Rx' if p.get('retrograde') else ''}")
    return "\n".join(lines)


# Per-beat content type contract (default — brand-agnostic story/value/info/fun-fact
# arc that works for any topic-led brand: horoscope, hip-hop history, wellness science,
# artisan craft, marine biology, whatever). Universal doctrine: warm hug for the ICP,
# never a pitch. Brands MAY override via brand.json key "beat_contract" if they want
# different beat semantics (e.g. a specific genre-shaped structure).
_DEFAULT_BEAT_CONTRACT = [
    "Beat 1 (hook): pose the surprising fact, news, event, or story with ONE concrete specific quoted literally from VERIFIED FACTS or the topic brief (a date, number, name, place, or proper noun). Make the viewer stop scrolling.",
    "Beat 2 (tension): name who this is for or what's at stake. Concrete and specific, not vague vibes. The reason the audience should care.",
    "Beat 3 (reveal): the mechanism, origin, context, or HOW/WHY behind the hook. Cite a literal value, date, or proper noun if available.",
    "Beat 4 (reinforce): history or context layer — when did this start, where does it come from, what came before? Quote a year, place, person, or work literally. If you don't have the verified data, frame in approximate terms ('decades ago', 'last generation') rather than inventing.",
    "Beat 5 (surprise): a non-obvious angle, deeper layer, hidden connection, or theoretical/traditional framing. Something the audience didn't already know.",
    "Beat 6 (insight): practical takeaway, micro-action, or per-segment guidance for the viewer — what to know, watch for, do, or feel. Specific not generic.",
    "Beat 7 (stinger): poetic close, lock-in feeling, callback to the hook, or aphorism. NO product mention. NO sales language. NO 'wear', 'buy', 'shop', 'drop', 'get yours', 'tee', 'hoodie', 'merch'. The brand sells itself via the outro card and the bio — the script's only job is to be worth following.",
]


def gen_script_plan(brand_cfg: dict, topic: dict) -> dict:
    """Ask Claude for a 7-beat script + accent words + Freepik queries."""
    verified = topic.get("verified_facts") or {}
    facts_block = _format_verified_facts_block(verified)
    beat_contract = brand_cfg.get("beat_contract") or _DEFAULT_BEAT_CONTRACT
    contract_block = "\n".join(f"- {row}" for row in beat_contract)

    prompt = f"""You are writing a vertical 9:16 social-media video script for {brand_cfg['display_name']}.

BRAND VOICE: {brand_cfg['brand_voice']}
ANTI-PATTERNS: {'; '.join(brand_cfg['anti_patterns'])}

TOPIC FROM SOCIAL QUEUE:
  topic: {topic['topic']}
  angle: {topic['angle']}
  notes: {topic['notes']}

{facts_block}

PER-BEAT CONTENT CONTRACT (each beat MUST do its job; do not collapse two beats into one):
{contract_block}

FACT DISCIPLINE:
- Any numerical value (degrees, days, weeks, months, years, dates, percentages) MUST be either (a) a literal value from VERIFIED FACTS above, or (b) omitted entirely. NEVER paraphrase a number ("six weeks" when facts say 6.7 weeks → say "six weeks" or "about seven weeks", not "six years").
- Any historical reference MUST be either (a) the prior_window year from VERIFIED FACTS quoted exactly, or (b) framed in approximate terms that cannot be wrong ("the last generation", "decades ago"). NEVER invent a year.
- If a fact is not in VERIFIED FACTS, you may use it ONLY if it's a structural truth of astrology (rulerships, houses, aspect angles) — not numerical claims about real-world dates or durations.

Produce a 7-beat script following the arc above. Total spoken length: 60-80 words across all 7 beats combined. Plain prose, no markdown.

TYPOGRAPHY RULES (aural — these are spoken by ElevenLabs TTS):
- ALWAYS put a space between numbers and units: "0 deg" not "0deg", "25 deg" not "25deg", "22 months" not "22months". Same goes for the topic_title field — no squished compound tokens.
- Spell out small numbers in body text ("zero degrees", "twenty-two"). Use digits only where they punch.
- LARGE NUMBERS RULE — any 4-digit-or-more number in the spoken body MUST be spelled out, otherwise ElevenLabs reads it as a year. Examples:
    * "2072 adults" → "two thousand seventy-two adults" (otherwise read as "twenty seventy-two")
    * "6014 patients" → "six thousand fourteen patients" (otherwise "sixty fourteen")
    * "1,200 sessions" → "twelve hundred sessions" or "one thousand two hundred sessions"
  EXCEPTION: actual years stay as digits — 1993, 2025, 2026 read correctly. If in doubt whether a 4-digit value is a year, write it explicit ("the year 2025" or "in 1993") OR spell out the number.
- The topic_title field is read VISUALLY (display font on title card), so digits there are fine — only the beat text body is constrained.

For EACH beat, also produce:
- "accent": a list of word indices WITHIN THE BEAT (0-indexed) to highlight in italic serif. Pick 1-3 emphasis words per beat.
- "freepik_query": 4-8 search words for 9:16 vertical premium imagery that matches the beat's emotional/visual tone. Prefer LIFESTYLE/atmospheric imagery; NEVER product-shot imagery (no t-shirts, no merch, no labels).

Return ONLY valid JSON, no markdown fences, no preamble. Schema:
{{
  "topic_title": "<short headline, < 60 chars>",
  "beats": [
    {{"text": "<beat 1 sentence(s)>", "accent": [<word indices>], "freepik_query": "<search terms>"}},
    ... seven of these ...
  ]
}}"""
    raw = call_claude(prompt)
    # strip markdown fences if Claude added them despite the instruction
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"claude returned non-JSON script plan: {e}\n---\n{raw[:800]}")
    if len(plan.get("beats", [])) != 7:
        die(f"expected 7 beats, got {len(plan.get('beats', []))}")
    return plan


# -------------------------------------------------------------------- freepik
def freepik_fetch(query: str, env: dict, out_path: Path) -> Path:
    """Search Freepik, download top vertical premium image to out_path."""
    api_key = env.get("FREEPIK_API_KEY") or die("missing FREEPIK_API_KEY")
    qs = urllib.parse.urlencode(
        {
            "term": query,
            "limit": 5,
            "filters[orientation][vertical]": 1,
            "filters[content_type][photo]": 1,
        }
    )
    url = f"https://api.freepik.com/v1/resources?{qs}"
    data = http_json(url, headers={"x-freepik-api-key": api_key})
    items = data.get("data") or []
    if not items:
        die(f"freepik: no results for '{query}'")
    pick = next((i for i in items if i.get("licenses", [{}])[0].get("type") == "premium"), items[0])
    image_url = pick["image"]["source"]["url"]
    http_download(image_url, out_path)
    return out_path


def freepik_fetch_video(
    query: str,
    env: dict,
    out_path: Path,
    min_duration: float = 3.0,
    max_duration: float = 8.0,
) -> Path:
    """Search Freepik /v1/videos, consume 1 credit to download a 9:16 clean mp4,
    then normalize via ffmpeg so headless Chromium can decode it during render.

    Raw Freepik downloads are studio-master quality (often 100MB+ for 5s) which
    chokes the HyperFrames render pipeline. ffmpeg trims to <= max_duration, scales
    to 1080x1920, re-encodes H.264 at ~4Mbps, drops audio. Result is ~5MB per clip.

    Picks the first 9:16 1080p result whose duration covers min_duration.
    Raises on no usable result; caller may catch + fall back to freepik_fetch (image).
    """
    api_key = env.get("FREEPIK_API_KEY") or die("missing FREEPIK_API_KEY")
    qs = urllib.parse.urlencode({"term": query, "limit": 10})
    url = f"https://api.freepik.com/v1/videos?{qs}"
    data = http_json(url, headers={"x-freepik-api-key": api_key})
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"freepik videos: no results for '{query}'")

    def dur_sec(s: str) -> float:
        try:
            parts = [int(x) for x in s.split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        except Exception:
            return 0.0

    def score(item: dict) -> int:
        s = 0
        if item.get("aspect_ratio") == "9:16":
            s += 100
        if item.get("quality") == "1080p":
            s += 20
        if item.get("item_subtype") == "footage":
            s += 10
        if not item.get("is_ai_generated"):
            s += 5
        if dur_sec(item.get("duration", "00:00:00")) >= min_duration:
            s += 30
        return s

    items_sorted = sorted(items, key=score, reverse=True)
    pick = items_sorted[0]
    if pick.get("aspect_ratio") != "9:16":
        raise RuntimeError(f"freepik videos: no 9:16 result for '{query}'")

    dl = http_json(
        f"https://api.freepik.com/v1/videos/{pick['id']}/download",
        headers={"x-freepik-api-key": api_key},
    )
    signed_url = dl.get("data", {}).get("url")
    if not signed_url:
        raise RuntimeError(f"freepik videos: download endpoint returned no url for {pick['id']}")

    raw_path = out_path.with_suffix(".raw.mp4")
    http_download(signed_url, raw_path, timeout=180)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(raw_path),
        "-t", str(max_duration),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v", "libx264", "-preset", "fast", "-b:v", "4M",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg normalize failed: {proc.stderr[-400:]}")
    raw_path.unlink(missing_ok=True)
    return out_path


# -------------------------------------------------------------------- eleven
def elevenlabs_tts(text: str, voice_id: str, env: dict, out_path: Path) -> Path:
    api_key = env.get("ELEVENLABS_API_KEY") or env.get("ELEVENLABS_API") or die("missing ELEVENLABS_API_KEY")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps(
        {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.3},
        }
    )
    req = urllib.request.Request(
        url,
        data=body.encode(),
        method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        out_path.write_bytes(r.read())
    return out_path


# -------------------------------------------------------------------- replicate
def replicate_run(model: str, version: str | None, input_payload: dict, env: dict) -> dict:
    token = env.get("REPLICATE_API_TOKEN") or die("missing REPLICATE_API_TOKEN")
    if version:
        url = "https://api.replicate.com/v1/predictions"
        body = json.dumps({"version": version, "input": input_payload})
    else:
        # latest stable: model-based endpoint
        url = f"https://api.replicate.com/v1/models/{model}/predictions"
        body = json.dumps({"input": input_payload})
    pred = http_json(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body=body,
    )
    pred_url = pred["urls"]["get"]
    # poll
    for _ in range(180):
        time.sleep(2)
        pred = http_json(pred_url, headers={"Authorization": f"Bearer {token}"})
        if pred["status"] in ("succeeded", "failed", "canceled"):
            break
    if pred["status"] != "succeeded":
        die(f"replicate {model} status={pred['status']}: {pred.get('error')}")
    return pred["output"]


def reconcile_words_with_script(script_text: str, wx_words: list[dict]) -> list[dict]:
    """Return a script-truthful word list with timings.

    WhisperX's wav2vec forced-alignment drops words it can't pin (commonly digits,
    years, single-character tokens, and some hyphenated proper nouns). It also
    occasionally substitutes (e.g. "rode" → "wrote"). The script we sent to TTS
    is the ground truth for what was actually spoken and what the caption must
    say. We use WhisperX only for *timing*; we keep the script's word list and
    interpolate timings for any word WhisperX dropped.
    """
    import re

    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t.lower())

    # Tokenize the script on whitespace AND hyphens — WhisperX returns hyphenated
    # proper nouns as separate tokens (e.g. "Run-DMC" → "Run", "DMC").
    raw_tokens = [t for t in re.split(r"[\s\-]+", script_text) if t.strip()]
    script_tokens = [{"display": t.strip(), "key": norm(t)} for t in raw_tokens if norm(t)]

    wx_keys = [norm(w["w"]) for w in wx_words]

    # Greedy alignment: for each script token, search forward up to a window of 6
    # in WhisperX for a matching key. WhisperX is mostly in-order; the window
    # absorbs skips and minor substitutions.
    matched_idx = [-1] * len(script_tokens)
    wx_i = 0
    WINDOW = 6
    for si, st in enumerate(script_tokens):
        found = -1
        for k in range(wx_i, min(wx_i + WINDOW, len(wx_keys))):
            if wx_keys[k] and wx_keys[k] == st["key"]:
                found = k
                break
        if found >= 0:
            matched_idx[si] = found
            wx_i = found + 1

    result = []
    for si, st in enumerate(script_tokens):
        if matched_idx[si] >= 0:
            w = wx_words[matched_idx[si]]
            result.append({"w": st["display"], "start": float(w["start"]), "end": float(w["end"])})
        else:
            result.append({"w": st["display"], "start": None, "end": None})

    n = len(result)
    # Fill unmatched runs by linear interpolation between matched neighbors.
    i = 0
    while i < n:
        if result[i]["start"] is None:
            run_start = i
            while i < n and result[i]["start"] is None:
                i += 1
            run_end = i - 1
            prev_end = result[run_start - 1]["end"] if run_start > 0 else 0.0
            next_start = result[run_end + 1]["start"] if run_end + 1 < n else (prev_end + 0.5 * (run_end - run_start + 1))
            span = max(0.15, next_start - prev_end)
            slot = span / (run_end - run_start + 2)
            for k, j in enumerate(range(run_start, run_end + 1)):
                s = prev_end + slot * (k + 1)
                e = prev_end + slot * (k + 2) - 0.01
                result[j]["start"] = s
                result[j]["end"] = e
        else:
            i += 1

    return [{"w": r["w"], "start": round(r["start"], 3), "end": round(r["end"], 3)} for r in result]


def replicate_flux(prompt: str, env: dict, out_path: Path) -> Path:
    """Generate a single 9:16 image via Replicate FLUX-schnell. ~$0.003 per image."""
    out = replicate_run(
        "black-forest-labs/flux-schnell",
        None,
        {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "output_format": "jpg",
            "output_quality": 88,
            "num_outputs": 1,
            "num_inference_steps": 4,
            "go_fast": True,
            "disable_safety_checker": False,
        },
        env,
    )
    if isinstance(out, list):
        out = out[0]
    http_download(out, out_path, timeout=90)
    return out_path


def whisperx_words(voice_path: Path, env: dict) -> list[dict]:
    """Transcribe voice via Replicate WhisperX. Uses data-URI (mp3 is small)."""
    token = env["REPLICATE_API_TOKEN"]
    # Audio mp3 from ElevenLabs for ~28s narration is ~700KB. Data URI is reliable;
    # Replicate's files API requires proper multipart which urllib doesn't do well.
    b64 = base64.b64encode(voice_path.read_bytes()).decode()
    audio_url = f"data:audio/mp3;base64,{b64}"
    out = replicate_run(
        WHISPERX_MODEL,
        WHISPERX_MODEL_VERSION,
        {"audio_file": audio_url, "align_output": True, "diarization": False},
        env,
    )
    segments = out.get("segments") or out
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            if "start" in w and "end" in w and w.get("word", "").strip():
                words.append(
                    {"w": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])}
                )
    return words


def detect_beats(wav_path: Path, min_bpm: float = 60.0, max_bpm: float = 160.0) -> list[float]:
    """Detect beat timestamps in an audio file using stdlib + ffmpeg.

    Pipeline: ffmpeg → mono 16kHz int16 PCM → 20ms RMS energy envelope → smoothing
    → adaptive-threshold local-maxima peak picking with minimum gap derived from max_bpm.

    Good enough for kick-drum / strong-onset music (which is what CC's percussive
    MusicGen prompt produces). For ambient/no-percussion music, returns very few
    or zero beats — which is the right behavior (no snap to nothing).
    """
    import struct

    target_sr = 16000
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
        "-ac", "1", "-ar", str(target_sr), "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True, timeout=60)
    n_samples = len(proc.stdout) // 2
    samples = struct.unpack(f"<{n_samples}h", proc.stdout)

    win_ms = 20
    win_size = int(target_sr * win_ms / 1000)
    energies = []
    for i in range(0, n_samples - win_size, win_size):
        e = sum(abs(s) for s in samples[i:i + win_size]) / win_size
        energies.append(e)
    if not energies:
        return []

    # 3-point moving average smoothing
    smoothed = []
    for i in range(len(energies)):
        lo, hi = max(0, i - 1), min(len(energies), i + 2)
        smoothed.append(sum(energies[lo:hi]) / (hi - lo))

    median = sorted(smoothed)[len(smoothed) // 2]
    threshold = median * 1.5
    min_gap_windows = max(1, int((60.0 / max_bpm) * 1000 / win_ms))

    beats: list[float] = []
    last_idx = -min_gap_windows
    for i in range(1, len(smoothed) - 1):
        v = smoothed[i]
        if v > threshold and v >= smoothed[i - 1] and v >= smoothed[i + 1]:
            if i - last_idx >= min_gap_windows:
                beats.append(i * win_ms / 1000.0)
                last_idx = i
    return beats


def snap_transitions_to_beats(
    scene_timings: list[dict],
    beats: list[float],
    window: float = 0.4,
    locked_first_start: float = 0.0,
) -> tuple[list[dict], int]:
    """Snap each scene transition (= start of next scene) to nearest beat within ±window.

    Adjusts both the previous scene's duration AND the next scene's start so the
    timeline stays contiguous. Keeps the very first scene start anchored.
    Returns (new_timings, n_snapped).
    """
    if not beats or len(scene_timings) < 2:
        return scene_timings, 0

    out = [dict(s) for s in scene_timings]
    out[0]["start"] = locked_first_start
    n_snapped = 0
    for i in range(1, len(out)):
        original_transition = out[i - 1]["start"] + out[i - 1]["duration"]
        nearest = min(beats, key=lambda b: abs(b - original_transition))
        if abs(nearest - original_transition) <= window and nearest > out[i - 1]["start"] + 0.5:
            new_transition = nearest
            n_snapped += 1
        else:
            new_transition = original_transition
        out[i - 1]["duration"] = round(new_transition - out[i - 1]["start"], 3)
        scene_end_target = out[i]["start"] + out[i]["duration"]
        out[i]["start"] = round(new_transition, 3)
        out[i]["duration"] = round(max(0.5, scene_end_target - new_transition), 3)
    return out, n_snapped


def musicgen(prompt: str, duration: int, env: dict, out_path: Path) -> Path:
    """Run MusicGen and download the wav."""
    out = replicate_run(
        "meta/musicgen",
        MUSICGEN_MODEL_VERSION,
        {
            "prompt": prompt,
            "duration": duration,
            "model_version": "stereo-melody-large",
            "output_format": "wav",
            "normalization_strategy": "peak",
        },
        env,
    )
    if isinstance(out, list):
        out = out[0]
    http_download(out, out_path)
    return out_path


# -------------------------------------------------------------------- cloudinary
def cloudinary_upload(path: Path, folder: str, env: dict) -> str:
    cn = env.get("CLOUDINARY_CLOUD_NAME") or die("missing CLOUDINARY_CLOUD_NAME")
    key = env.get("CLOUDINARY_API_KEY") or die("missing CLOUDINARY_API_KEY")
    secret = env.get("CLOUDINARY_API_SECRET") or die("missing CLOUDINARY_API_SECRET")
    import hashlib

    ts = str(int(time.time()))
    public_id = f"sp2_{ts}"
    # signature: alphabetical params (excluding api_key, file, signature) + secret, SHA1
    to_sign = f"folder={folder}&public_id={public_id}&timestamp={ts}{secret}"
    sig = hashlib.sha1(to_sign.encode()).hexdigest()
    # multipart upload
    boundary = "----cld" + ts
    fields = {
        "api_key": key,
        "timestamp": ts,
        "public_id": public_id,
        "folder": folder,
        "signature": sig,
    }
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    )
    parts.append(b"Content-Type: video/mp4\r\n\r\n")
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = f"https://api.cloudinary.com/v1_1/{cn}/video/upload"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    return resp["secure_url"]


# -------------------------------------------------------------------- metricool
def metricool_schedule(
    brand_cfg: dict,
    env: dict,
    video_url: str,
    caption_video: str,
    caption_still: str,
    hero_still_url: str,
    when_iso: str,
    dryrun: bool,
) -> dict:
    """Schedule video on the brand's enabled video networks via Metricool. One POST per network.

    Networks honored from brand_cfg["metricool_networks"] (default = ["tiktok", "instagram_reel"]
    for back-compat with brands wired before this field existed). Brands without TikTok connected
    (e.g. welovehoroscope) declare networks = ["instagram_reel"] to skip the TT POST.

    Proven shape lifted from JonOps OA social-tiktok / social-instagram skills 2026-05-11.
    """
    networks = brand_cfg.get("metricool_networks") or ["tiktok", "instagram_reel"]
    plan = {
        "video_lane": {"platforms": networks, "media": video_url, "caption": caption_video, "datetime": when_iso, "timezone": "Asia/Makassar"},
        "still_lane_skipped_for_v1": True,
        "still_lane_planned": {"hero_still": hero_still_url, "caption": caption_still},
    }
    if dryrun:
        return {"dryrun": True, "would_post": plan}

    token = env.get("METRICOOL_API_TOKEN") or die("missing METRICOOL_API_TOKEN")
    blog_id = brand_cfg["metricool_blog_id"]
    results = {}

    if "tiktok" not in networks:
        results["tiktok"] = {"skipped": "not in brand metricool_networks"}
    # ---- TikTok ----
    tt_url = f"https://app.metricool.com/api/v2/scheduler/posts?userToken={token}&blogId={blog_id}"
    tt_body = json.dumps({
        "text": caption_video,
        "providers": [{"network": "tiktok"}],
        "publicationDate": {"dateTime": when_iso, "timezone": "Asia/Makassar"},
        "draft": False,
        "autoPublish": True,
        "media": [video_url],
        "videoCoverMilliseconds": 350,
        "tiktokData": {
            "privacyOption": "PUBLIC_TO_EVERYONE",
            "disableComment": False,
            "disableDuet": False,
            "disableStitch": False,
            "commercialContentOwnBrand": False,
            "commercialContentThirdParty": False,
            "isAigc": False,
        },
    })
    if "tiktok" in networks:
        try:
            results["tiktok"] = http_json(
                tt_url, method="POST",
                headers={"X-Mc-Auth": token, "Content-Type": "application/json"},
                body=tt_body,
            )
        except urllib.error.HTTPError as e:
            results["tiktok"] = {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")[:500]}

    # ---- Instagram Reel ----
    ig_url = f"https://app.metricool.com/api/v2/scheduler/posts?blogId={blog_id}"
    ig_body = json.dumps({
        "text": caption_video,
        "providers": [{"network": "instagram"}],
        "publicationDate": {"dateTime": when_iso, "timezone": "Asia/Makassar"},
        "draft": False,
        "autoPublish": True,
        "media": [video_url],
        "videoCoverMilliseconds": 350,
        "instagramData": {"type": "REEL"},
    })
    if "instagram_reel" in networks:
        try:
            results["instagram_reel"] = http_json(
                ig_url, method="POST",
                headers={"X-Mc-Auth": token, "Content-Type": "application/json"},
                body=ig_body,
            )
        except urllib.error.HTTPError as e:
            results["instagram_reel"] = {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")[:500]}
    else:
        results["instagram_reel"] = {"skipped": "not in brand metricool_networks"}

    return {"dryrun": False, "scheduled": results, "scheduled_time": when_iso, "timezone": "Asia/Makassar"}


# -------------------------------------------------------------------- ledger
def ledger_append(line: str) -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(line.rstrip() + "\n")


# -------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--script-only", action="store_true")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--topic-override", help="Skip Airtable queue read; use this topic literally")
    ap.add_argument("--angle-override", default="")
    ap.add_argument("--notes-override", default="")
    args = ap.parse_args()

    if DRYRUN_SENTINEL.exists():
        print(f"DRYRUN sentinel present at {DRYRUN_SENTINEL} — forcing --dryrun")
        args.dryrun = True

    project_dir = ROOT / args.brand
    if not project_dir.is_dir():
        die(f"no project dir for brand '{args.brand}' at {project_dir}")
    brand_cfg = json.loads((project_dir / "brand.json").read_text())
    env = load_env(SECOND_BRAIN_ENV)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = project_dir / "renders" / f"run-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    assets = project_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    print(f"[1/10] pick topic from social queue")
    if args.topic_override:
        # Override path: still parse verified_facts out of angle/notes if the user
        # pasted a Research Brief that contains the delimited JSON block.
        angle_clean, facts_from_angle = _extract_verified_facts(args.angle_override or "")
        notes_clean, facts_from_notes = _extract_verified_facts(args.notes_override or "")
        topic = {
            "id": "override",
            "topic": args.topic_override,
            "angle": angle_clean,
            "notes": notes_clean,
            "verified_facts": facts_from_angle or facts_from_notes or {},
        }
        print(f"  topic: {topic['topic'][:80]} (override, no Airtable mutation)")
        if topic["verified_facts"]:
            print(f"  verified_facts: {len(topic['verified_facts'])} fields extracted from override")
    else:
        topic = pick_topic(brand_cfg, env)
        print(f"  topic: {topic['topic'][:80]} (rec {topic['id']})")

    is_real_consume = not args.dryrun and not args.script_only and topic["id"] != "override"
    if is_real_consume:
        mark_topic_in_progress(brand_cfg, env, topic["id"])

    try:
        print(f"[2/10] generate 7-beat script via claude")
        plan = gen_script_plan(brand_cfg, topic)
        plan_path = run_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2))
        print(f"  topic_title: {plan['topic_title']}")

        if args.script_only:
            print(f"--script-only: stopping after plan at {plan_path}")
            return

        media_style = brand_cfg.get("media_style", "photo")  # "photo" (Freepik) | "illustration" (FLUX-schnell)
        print(f"[3/10] fetch 7 media assets (mode={media_style})")
        if media_style == "illustration":
            style_suffix = brand_cfg.get("illustration_style_suffix", "vertical 9:16 cinematic illustration, atmospheric lighting, hand-painted")
            for i, beat in enumerate(plan["beats"], start=1):
                flux_out = assets / f"flux_beat{i}.jpg"
                prompt = f"{beat['freepik_query']}, {style_suffix}"
                replicate_flux(prompt, env, flux_out)
                beat["bg"] = flux_out.name
                beat["bg_type"] = "image"
                print(f"  beat {i}: {beat['freepik_query'][:60]} → {flux_out.name} [flux]")
        else:
            use_video = bool(brand_cfg.get("prefer_video_clips", True))
            for i, beat in enumerate(plan["beats"], start=1):
                video_out = assets / f"freepik_beat{i}.mp4"
                image_out = assets / f"freepik_beat{i}.jpg"
                beat["bg_type"] = "image"
                if use_video:
                    try:
                        freepik_fetch_video(beat["freepik_query"], env, video_out)
                        beat["bg"] = video_out.name
                        beat["bg_type"] = "video"
                        print(f"  beat {i}: {beat['freepik_query'][:50]} → {video_out.name} [video]")
                        continue
                    except Exception as e:
                        print(f"  beat {i}: video fetch failed ({e}); falling back to image")
                freepik_fetch(beat["freepik_query"], env, image_out)
                beat["bg"] = image_out.name
                print(f"  beat {i}: {beat['freepik_query'][:50]} → {image_out.name} [image]")

        full_script = " ".join(b["text"] for b in plan["beats"])

        print(f"[4/10] elevenlabs voiceover")
        voice_path = assets / "voice.mp3"
        elevenlabs_tts(full_script, brand_cfg["voice_id"], env, voice_path)

        print(f"[5/10] replicate whisperx word timestamps")
        wx_raw = whisperx_words(voice_path, env)
        words = reconcile_words_with_script(full_script, wx_raw)
        (assets / "words.json").write_text(json.dumps(words, indent=2))
        (assets / "words_whisperx_raw.json").write_text(json.dumps(wx_raw, indent=2))
        interp = sum(1 for i, w in enumerate(words) if i < len(wx_raw) and w["w"].lower() != wx_raw[i]["w"].lower())
        print(f"  {len(words)} script-truthful words (whisperx returned {len(wx_raw)}, reconciled)")

        TITLE_DURATION = 2.0  # branded title-card scene at t=[0, TITLE_DURATION]. 0.7s left only ~0.06s of static hold after enter/exit fades — title unreadable. 2.0s gives ~1.4s of clean readable hold.
        audio_duration = (words[-1]["end"] + 0.4 if words else 28.0) + TITLE_DURATION
        video_duration = audio_duration + 3.0  # outro card

        print(f"[6/10] replicate musicgen ({int(video_duration)}s)")
        music_path = assets / "music.wav"
        default_music_prompt = f"calm ambient wellness instrumental, {brand_cfg['display_name']} brand, soft pads, no vocals, no percussion sharpness"
        music_prompt = brand_cfg.get("music_prompt", default_music_prompt)
        musicgen(music_prompt, int(video_duration), env, music_path)

        print(f"[7/10] build hyperframes composition")
        # Map words → beats by SCRIPT-NATURAL boundaries.
        # NB: WhisperX/reconciliation splits hyphenated compound words into separate
        # tokens ("twenty-five" → "twenty"+"five"). Naive script.split() counts them
        # as 1 word each and the boundary drifts +1 per hyphen, leaking the last word
        # of each beat into the next scene. Count alphanumeric-bounded hyphens as
        # extra tokens to match the reconciliation split.
        import re as _re
        def _cooked_word_count(text: str) -> int:
            base = len(text.split())
            hyphen_compounds = len(_re.findall(r"\w-\w", text))
            return base + hyphen_compounds

        total_words = len(words)
        script_beats = plan["beats"]
        beats_config = []
        running = 0
        for i, beat in enumerate(script_beats):
            beat_word_count = _cooked_word_count(beat["text"])
            lo = running
            if i == len(script_beats) - 1:
                # Last beat absorbs any rounding drift from reconciliation.
                hi = total_words
            else:
                hi = min(running + beat_word_count, total_words)
            running = hi
            beats_config.append(
                {
                    "scene": f"scene-{i+1}",
                    "lo": lo,
                    "hi": hi,
                    "accent": [a for a in beat.get("accent", []) if a < (hi - lo)],
                    "bg": beat["bg"],
                    "bg_type": beat.get("bg_type", "image"),
                }
            )
        # scene timings from word boundaries with generous pre/post-roll so the
        # 0.5s fade-in completes BEFORE the first word fires (was 0.3s pre-roll →
        # first word landed at ~60% scene opacity, the "rushed transition" feel)
        # and the 0.5s fade-out doesn't start UNTIL after the last word ends.
        scene_timings = []
        prev_end = 0.0
        for bc in beats_config:
            start = max(0.0, (words[bc["lo"]]["start"] if bc["lo"] < total_words else prev_end) - 0.6)
            end = (words[bc["hi"] - 1]["end"] if bc["hi"] - 1 < total_words else prev_end) + 0.7
            scene_timings.append({"start": round(start, 2), "duration": round(end - start, 2)})
            prev_end = end
        scene_timings.append({"start": round(prev_end + 0.2, 2), "duration": 3.0})
        scene_timings = [
            {"start": round(s["start"] + TITLE_DURATION, 2), "duration": s["duration"]} for s in scene_timings
        ]

        bs_cfg = brand_cfg.get("beat_sync") or {}
        if bs_cfg.get("enabled"):
            try:
                beats = detect_beats(music_path)
                (assets / "beats.json").write_text(json.dumps(beats, indent=2))
                window = float(bs_cfg.get("snap_window_sec", 0.4))
                # Anchor first scene AFTER the title card; otherwise beat-sync
                # snaps scene-1 to t=0 and overlays the title (bleed-through bug
                # observed on CC/TM 2026-05-16).
                scene_timings, n_snapped = snap_transitions_to_beats(scene_timings, beats, window=window, locked_first_start=TITLE_DURATION)
                print(f"  beat-sync: {len(beats)} beats detected, {n_snapped}/{len(scene_timings)-1} transitions snapped (±{window}s)")
            except Exception as e:
                print(f"  beat-sync: skipped ({e})")

        build_cfg = {
            "brand": args.brand,
            "project_dir": str(project_dir),
            "words_path": "assets/words.json",
            "voice_path": "assets/voice.mp3",
            "music_path": "assets/music.wav",
            "duration": round(video_duration, 1),
            "brand_identity": brand_cfg,
            "beats": beats_config,
            "scene_timings": scene_timings,
            "title_card": {
                "topic_title": plan["topic_title"],
                "duration": TITLE_DURATION,
            },
            "audio_offset": TITLE_DURATION,
        }
        build_cfg_path = run_dir / "build_config.json"
        build_cfg_path.write_text(json.dumps(build_cfg, indent=2))
        subprocess.run(
            ["python3", str(project_dir / "scripts" / "build-karaoke-html.py"), str(build_cfg_path)],
            check=True,
        )

        print(f"[8/10] hyperframes render → mp4")
        render_proc = subprocess.run(
            ["npx", "hyperframes", "render"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        if render_proc.returncode != 0:
            die(f"hyperframes render failed: {render_proc.stderr[-800:]}")
        # find newest mp4 in renders/
        mp4s = sorted(
            (project_dir / "renders").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not mp4s:
            die("render produced no mp4")
        mp4 = mp4s[0]
        final_mp4 = run_dir / mp4.name
        mp4.replace(final_mp4)
        print(f"  rendered: {final_mp4}")

        print(f"[9/10] cloudinary upload")
        video_url = cloudinary_upload(final_mp4, brand_cfg["cloudinary_folder"], env)
        print(f"  {video_url}")

        print(f"[10/10] metricool schedule (dryrun={args.dryrun})")
        # Hero still: use beat-1's actual bg filename (works for both photo and illustration modes).
        # Skip the upload entirely if the brand only fires video lane (no still-lane networks).
        networks = brand_cfg.get("metricool_networks") or ["tiktok", "instagram_reel"]
        still_lane_networks = {"facebook", "pinterest", "bluesky", "threads", "twitter", "gbp"}
        needs_hero_still = bool(still_lane_networks.intersection(networks))
        hero_still_name = plan["beats"][0].get("bg") if plan.get("beats") else None
        if not args.dryrun and needs_hero_still and hero_still_name:
            hero_still = assets / hero_still_name
            hero_still_url = cloudinary_upload_image(hero_still, brand_cfg["cloudinary_folder"], env)
        else:
            hero_still_url = "(skipped — video-only brand or dryrun)"
        # Schedule for next 09:00 Asia/Makassar (morning prime). When run on Mon/Wed/Fri 15:00 WITA cron,
        # this puts the post out next morning 09:00 WITA — ~18 hours of Metricool buffer.
        now_wita = datetime.now(ZoneInfo("Asia/Makassar"))
        if now_wita.hour >= 8:
            target = (now_wita + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            target = now_wita.replace(hour=9, minute=0, second=0, microsecond=0)
        when_iso = target.strftime("%Y-%m-%dT%H:%M:%S")
        handle = brand_cfg.get("social_handle") or f"@{brand_cfg['brand']}"
        caption_video = f"{plan['topic_title']}\n\nFollow {handle} for more."
        caption_still = caption_video
        result = metricool_schedule(
            brand_cfg, env, video_url, caption_video, caption_still, hero_still_url, when_iso, args.dryrun
        )
        (run_dir / "metricool_result.json").write_text(json.dumps(result, indent=2))

        ledger_line = (
            f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} sp2_pipeline "
            f"brand={args.brand} rec={topic['id']} topic=\"{plan['topic_title'][:60]}\" "
            f"video={video_url} dryrun={args.dryrun}"
        )
        ledger_append(ledger_line)

        if is_real_consume:
            mark_topic_used(brand_cfg, env, topic["id"])
        print(f"DONE → {run_dir}")
    except BaseException:
        if is_real_consume:
            try:
                mark_topic_revert_to_queued(brand_cfg, env, topic["id"])
                print(f"  topic {topic['id']} reverted to Queued after pipeline failure")
            except Exception as revert_err:
                print(f"  WARNING: failed to revert topic to Queued: {revert_err}")
        raise


def cloudinary_upload_image(path: Path, folder: str, env: dict) -> str:
    """Same as cloudinary_upload but for image."""
    cn = env["CLOUDINARY_CLOUD_NAME"]
    key = env["CLOUDINARY_API_KEY"]
    secret = env["CLOUDINARY_API_SECRET"]
    import hashlib

    ts = str(int(time.time()))
    public_id = f"sp2_hero_{ts}"
    to_sign = f"folder={folder}&public_id={public_id}&timestamp={ts}{secret}"
    sig = hashlib.sha1(to_sign.encode()).hexdigest()
    boundary = "----cld" + ts
    fields = {"api_key": key, "timestamp": ts, "public_id": public_id, "folder": folder, "signature": sig}
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode())
    parts.append(b"Content-Type: image/jpeg\r\n\r\n")
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = f"https://api.cloudinary.com/v1_1/{cn}/image/upload"
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["secure_url"]


if __name__ == "__main__":
    main()
