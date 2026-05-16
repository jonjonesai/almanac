# Almanac — Dev Log

> **WHY ALMANAC EXISTS (the founding doctrine):** Almanac was built *for*, *via*, and *with* the **warm-hug doctrine**. Every architectural choice — the 7-beat per-beat contract, the `verified_facts` injection, the universal "no products in script body" rule, the per-brand researchers that pull from real-world structured spines (ephemerides / PubMed / album anniversaries / cultural calendars) — exists to serve one rule: **warm hug for the ICP, never the pitch.** The brand sells itself via outro card + bio. The script's only job is to be a story, a fact, a moment of value the viewer actually wants. Almanac is the doctrine made executable. Strip away the karaoke captions, the AI music, the FLUX illustrations, the WhisperX timing — and the doctrine remains. The doctrine is the founding act. Everything else serves it. — Jon, locked 2026-05-15, named Almanac 2026-05-16

---

# SP2 Video Skill — Session Handoff
**Last updated:** 2026-05-16 morning (after overnight 2026-05-15 hardening session)
**Cost spent:** $2.31 across 6 WLH smoke tests
**Mission going forward:** "make this video skill so bullet proof and astonishing that it's remarkable to anyone." — Jon

---

## Where we are right now

SP2 pipeline (HyperFrames + GSAP + ElevenLabs + WhisperX + MusicGen + Cloudinary + Metricool) is production-ready. Validated end-to-end on WLH across 6 smoke tests. Doctrine locked. Layout hardened.

**Brand status:**
| Brand | Dispatcher | Notes |
|---|---|---|
| organicaromas | LIVE (Mon/Wed/Fri 15:00 Taipei) | First brand validated, ships freely |
| customcreative | LIVE (Tue/Thu/Sat 15:00) | Hip-hop merch, Darius voice |
| utamaspice | LIVE (Sun/Thu 15:00) | Bali artisan, Mia voice |
| taiwanmerch | scaffolded, `enabled: false` | awaiting smoke-test QA |
| **welovehoroscope** | **scaffolded, `enabled: false`** | **Jon to flip when ready — first auto-fire would be Wed/Sun 19:00 Taipei** |

---

## What was fixed in the 2026-05-15 overnight session

Five real bugs, doctrine pivot, layout overhaul. All shipped to `sp2_pipeline.py` + WLH's `scripts/build-karaoke-html.py`.

| # | Bug | Fix | Affects |
|---|---|---|---|
| 1 | Beat boundary drift — uniform `total_words//7` misallocated last word of each beat | Walk cooked words using each beat["text"]'s word count | ALL brands (shared) |
| 2 | Hyphen-aware boundaries — WhisperX splits "twenty-five" → 2 tokens, naive split shifts boundary by +1 per hyphen | `_cooked_word_count` adds hyphen count to base split | ALL brands (shared) |
| 3 | Title card too short (0.7s left only ~0.06s of static hold) | `TITLE_DURATION = 2.0` | ALL brands (shared) |
| 4 | Caption layout — left-aligned, accent words in Cinzel created mid-phrase wraps | `text-align: center`, font 58 → 64px, accent uses body font (Inter) with italic + uppercase + letter-spacing instead of Cinzel | **WLH only** — needs rolling to others |
| 5 | Fade timing — fade-in 0.5s but pre-roll 0.3s → first word at 60% scene opacity; fade-out at `next - 0.35` → last word ended mid-fade | Pre-roll 0.3 → 0.6, post-roll 0.4 → 0.7, fade-out anchored to `sc.start + sc.duration - 0.5` instead of next.start | Pre/post-roll: ALL (shared). Fade-out anchor: **WLH only** — needs rolling. |

**Plus**: TTS typography rule added to script-gen prompt (`0 deg` not `0deg`, spell out small numbers).

---

## Doctrine — Jon locked 2026-05-15

**"Warm hug for the ICP, not a pitch."**

- Script body = pure horoscope content. Per-sign guidance ("if you're earth..."), transit mechanics, generational markers, fun cosmic stories
- ZERO product mention anywhere in the script — not body, not close, not beat 7
- Brand presence: outro card + brand-tag overlay + post caption + IG bio + visual identity. The script's only job is to be valuable content
- Beat 7 = poetic close (e.g., *"Build phase. Body anchored. Don't start anything new."*), never *"Mars in Taurus 2026 tee, stamped for..."*

This is currently WLH-specific in `welovehoroscope/brand.json` `anti_patterns[]` + `brand_voice`. CC / OA / UT / TM may want different (their products are more central to brand identity — hip-hop merch IS the cultural artifact, wellness products ARE the wedge).

---

## Outstanding work — in priority order

### 1. Content scaffolding (queued, ~1-2 hours, ~$1)
See `$MEMORY_ROOT/project_sp2_content_scaffolding.md` for full plan.

**Problem:** Claude script-gen receives `topic + angle + notes` as freeform prose, then fills in numbers. Run 6 produced *"six years of fire"* when Mars-in-Aries lasted ~6 weeks. Subtle factual drift.

**Fix:** Three layers:
- (a) `wlh_topic_researcher.py` emits structured `verified_facts: {mars_aries_duration_weeks: 6, synodic_days: 780, ingress_date: "2026-05-19", ...}` with source citations (skyfield JPL DE421)
- (b) `gen_script_plan` in `sp2_pipeline.py` injects `verified_facts` into Claude's prompt and requires literal reference
- (c) Per-beat content type contract: beat 1 = hook+ONE-verified-fact, beat 6 = per-sign guidance, etc.

Roll to other brand researchers after WLH proof.

### 2. Roll layout + fade-out fix to OA / CC / UT / TM (~30 min, 4 × $0.33 smoke tests)
Bugs #4 and #5 above are still WLH-only. Other brands have:
- Caption left-aligned with Cinzel accent (mid-phrase wraps)
- Fade-out at `next - 0.35` (last word fades mid-display)

Each brand's `scripts/build-karaoke-html.py` needs the same edits I applied to WLH. They DIFFER from each other (per-brand customizations), so surgical patches, not file copies. Smoke-test each before declaring done.

### 3. Audio ducking (~30 min, smoke test)
Voice currently at `data-volume="1.0"`, music at `0.18` — flat mix. Real broadcast audio uses sidechain compression so music drops 6-9 dB when voice speaks. HyperFrames audio is `<audio>` tags — can be ducked via Web Audio API or pre-rendered by ffmpeg with sidechain before Cloudinary upload.

### 4. Thumbnail frame (~20 min, no smoke test needed)
Meta and TikTok pull the first frame of the mp4 as the preview tile. Currently this is whatever scene-1 looks like at t=0 (which is now the title card mid-entrance). A *deliberately composed* first frame (title at full opacity, no animation) would dramatically improve click-through. Can be a separate `thumbnail.jpg` captured at t=1.0s of the rendered video, or a re-arranged timeline so frame 0 is the visual peak of the title card.

### 5. WLH go-live (one line)
Jon flips `enabled: false → true` in `<dispatcher>/jobs/sp2-video-welovehoroscope.yml`. First auto-fire would be the next Wed/Sun 19:00 Taipei.

### Backlog (not for today)
- Migrate SP2 dispatcher from second-brain box → jonops-vps host
- Magic stack: Freepik video b-roll, beat-sync to MusicGen drums, marker sweeps on emphasis words, shader transitions

---

## How to validate after any change

```bash
cd $REPO_ROOT
python3 sp2_pipeline.py --brand welovehoroscope --dryrun
```

- Cost: ~$0.33 per run (ElevenLabs $0.20 + WhisperX $0.005 + MusicGen $0.05 + FLUX/Freepik $0.07 + Claude $0.01)
- ~3-5 min runtime
- Outputs mp4 to `welovehoroscope/renders/run-<ts>/` AND uploads to Cloudinary
- DRYRUN sentinel at `$HEARTBEAT_DIR/DRYRUN` forces dryrun even without flag
- 7-point acceptance criteria documented in `$SKILL_ROOT/SKILL.md` under "Acceptance criteria"

To extract frames for inspection:
```bash
cd $REPO_ROOT/<brand>/renders/run-<latest>
ffmpeg -i *.mp4 -vf "select='eq(n,300)+eq(n,600)+eq(n,900)'" -fps_mode vfr -q:v 2 /tmp/frame_%d.jpg -y
# then Read /tmp/frame_N.jpg in Claude
```

---

## File map

```
$REPO_ROOT/
├── sp2_pipeline.py                    # orchestrator (10-step pipeline)
├── HANDOFF.md                         # this file
├── <brand>/
│   ├── brand.json                     # voice, colors, fonts, anti_patterns, brand_voice doctrine
│   ├── DESIGN.md                      # visual identity rules
│   ├── scripts/
│   │   ├── build-karaoke-html.py      # per-brand HTML builder
│   │   ├── today_sky.py               # (WLH only) skyfield ephemeris
│   │   └── wlh_topic_researcher.py    # (WLH only) astrology topic gen
│   ├── assets/                        # populated per fire
│   └── renders/run-<ts>/              # populated per fire

<dispatcher>/jobs/
└── sp2-video-<brand>.yml              # cron schedule + enabled gate

$SKILL_ROOT/
├── SKILL.md                           # canonical skill doc with onboarding section
└── templates/
    ├── brand.template.json
    ├── DESIGN.template.md
    └── dispatcher.template.yml

$MEMORY_ROOT/
├── project_sp2_hyperframes_pipeline.md
├── project_sp2_content_scaffolding.md  # next-session plan
└── ...
```

---

## Strategic context (so you know why we're here)

Jon declared 2026-05-15 the **10-year strategic arc** for his Second Brain → digital marketing operations platform for own brands + paid clients. Three pillars:

1. **WP/Kadence framework** (MKB-evolved, multi-element workflow controller)
2. **POD + MEGA** (engine + retail + brand portfolio + services — 4 revenue paths)
3. **JonOps productized** (small-business automation blueprint, sellable)

SP2 video pipeline is part of Pillar 2 + Pillar 3 — the marketing element that produces social content for every brand. **Bulletproof + astonishing** = the goal for today's session, per Jon's morning kick-off.

Brand portfolio context (don't confuse a deactivated JonOps container with a deactivated business — Villa Amrita business is Jon's 14-year crown jewel even though its container isn't migrated):
- jonjones.ai (identity hub)
- MEGA (SaaS + retail + dogfood)
- OrganicAromas + UtamaSpice (legacy revenue)
- Custom Creative (album-art tees + Wu-Tang lights + Custom Ridge wallets)
- BroSharks + cutemerch.love + TaiwanMerch + WeLoveHoroscope (newer brands)
- Villa Amrita (Bali hospitality, "Home of No-Work November")

---

## Today's immediate ask from Jon

> "make this video skill so bullet proof and astonishing that its remarkable ot anyone"

Recommended start: **#1 content scaffolding + #2 fade-out roll to other brands**, bundled. Content rigor + cross-brand consistency = the highest-leverage moves toward "remarkable to anyone."
