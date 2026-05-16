# We Love Horoscope — SP2 Video Visual Identity

## Style Prompt

Personalized horoscope merch with cosmic cultural authority. Each design captures a real sky event as a wearable generational marker — like collectible Halley's Comet pins or Y2K hoodies, but anchored on actual ephemeris data, not horoscope-app slop. Deep cosmic indigo canvas with antique-gold and amethyst-purple accents. Hand-painted art nouveau astrology illustration backgrounds — constellations, ephemeris glyphs, planetary symbols, sigils. Slow celestial motion (the sky moves, the merch lands). Aimed at someone who knows their rising sign, reads transit charts, and would wear "URANUS IN GEMINI 2026" as a generational artifact.

## Product context (CRITICAL)

WLH is a **personalized horoscope merch brand** — tees, hoodies, stickers, mugs, prints. **Not** scent / aromatherapy / essential oils (those are OrganicAromas + Utama Spice lanes). Every SP2 script must close on a wearable form, never a ritual prescription. The wedge is "wear the marker of an actual celestial event," same energy as concert tour shirts but for the sky.

## Colors

| Hex       | Role                                                                      |
| --------- | ------------------------------------------------------------------------- |
| `#c084fc` | Primary — amethyst purple (highlights, key-word emphasis, planet glyphs)  |
| `#0a0815` | Cosmic indigo — primary canvas, night-sky ground                          |
| `#1a1430` | Deep violet — secondary surface                                           |
| `#fafafa` | Off-white — primary body text on dark surface                             |
| `#d4af37` | Antique gold — sigil accents (rare, only for transformation moments)      |
| `#a78bfa` | Soft violet — lower-third labels, secondary text                          |

Caption-on-image style: white body text + amethyst accent words in italic display serif. All caption text sits on a deep cosmic gradient scrim so it reads cleanly over varied celestial b-roll.

## Typography

- **Display headlines** (60-130px): `Cinzel` weight 400 — engraved Roman serif with zodiac/occult DNA, art nouveau-adjacent
- **Accent emphasis words**: `Cinzel` italic if available, else fall back to display weight with `font-style: italic`
- **Captions / body** (38-56px): `Inter` weight 700 — bold sans for video legibility
- **Lower-third labels** (20-28px): `Inter` weight 500, letter-spacing 0.22em uppercase

## Motion

- Slow, celestial-paced (1.0-1.5s entrance durations, slower than CC, similar to OA wellness)
- Easing: `power2.out` for organic entrances; `expo.out` for moments of revelation; `sine.inOut` for ambient celestial drift
- Staggers: 80-150ms between elements (slow, considered — the sky moves)
- Image Ken Burns: 1.0 → 1.06 scale over scene duration (subtle, dreamlike)
- Scene transitions: crossfade only — NEVER hard cuts, NEVER shader effects with chromatic aberration

## Media generation

- **Mode:** `illustration` via FLUX-schnell. No Freepik. Cosmic-aesthetic content does not exist in stock libraries with this quality.
- **Style suffix:** "in the style of mystical art nouveau astrology illustration, ethereal cosmic background with constellations and nebulae, deep indigo violet and antique gold palette, hand-painted celestial scene, golden ratio composition, vertical 9:16, painterly, atmospheric, no text, no logos, no faces"
- The style suffix is appended to every per-beat freepik_query string Claude generates.

## Topic sourcing (CRITICAL)

Topics MUST come from live planetary positions via `scripts/today_sky.py` (skyfield + JPL DE421 ephemeris). Never from Claude's general astrology knowledge. See `feedback_wlh_topics_from_sky.md` in `~/.claude/projects/-home-jon/memory/`. The `PLANET_SCENTS` dict inside `today_sky.py` is **design inspiration only** (Saturn-themed = dark + grave + frankincense smoke imagery; Mars-themed = red + ginger heat + sharp metal) — never recommend the herbs as ritual in script copy.

## Script doctrine: tell the horoscope, never sell the merch (Jon, 2026-05-15, locked)

**ZERO product mention anywhere in the script.** Not in body, not in close, not in beat 7. The script's only job is to be valuable horoscope content. Brand presence comes from the outro card, the brand-tag overlay on scene 1, the post caption, and the IG bio — never from the script itself.

**Content categories** that work for every beat 1-7:
- What a `<sign>` should do this week (Gemini: lean into the lightning. Taurus: don't rush the build.)
- What a `<sign>` should NOT do (Pisces: this is not your dream week.)
- Practical per-sign guidance grounded in current transits
- Fun cosmic stories (last time Uranus was in Gemini, 1941…)
- Interesting transit mechanics (Mars in detriment = action gets heavier, more methodical)
- Generational markers (this cohort starts the build phase)
- Real degrees, real ingresses, real history

**Beat 7 = poetic insight, not a sales line.** It's the lock-in feeling, the closing thought, the "if you remember one thing." Examples that work:
- "Build phase. Body anchored. The cohort that pays attention wins."
- "Six weeks of finishing. Don't start anything new."
- "If you're earth, you've been waiting for this."
- "Worn by the people who saw it first." ← NO. Wrong. Mentions wearing.
- "Mars in Taurus 2026 tee, stamped for the people building something tangible." ← NO. Product.

The reader should never feel pitched. They should feel SEEN by a generous astrologer who reads charts and shares the insight without a tip jar. People sophisticated enough to be in WLH's ICP will find the brand on their own.

## What NOT to do

- **NO product mentions in beats 1-6** — body is story/value/info only. Wearable close stays in beat 7. (Earned 2026-05-15: WLH scripts had been promoing in body, breaking the warm-hug doctrine)
- No scent / essential-oil / herbal / aromatherapy framing — that's OA + UT territory
- No "burn this" / "wear this oil" prescriptions — close on wearable merch, not ritual herbs
- No fortune-telling certainty — astrology shows pattern, not destiny
- No sun-sign essentialism — never "you ARE a Leo," always speak to transits, houses, aspects
- No "vibes" as a noun. No horoscope-app cliches. No "manifesting."
- No medical or healing claims — about anything
- No aggressive easing (`back`, `elastic`, `bounce`) — too caffeinated for celestial motion
- No staggers under 60ms — breaks the cosmic-time illusion
- No more than 2 font families per composition
- **No em-dashes in any caption or title text** — replace with periods, commas, colons (Jon-wide brand voice rule)
- No faces in FLUX-generated illustrations — keeps the cosmic-not-personality frame

## Active social platforms

Facebook, Instagram, Pinterest. **NO TikTok** (not connected to Metricool). **NO Twitter** (deactivated 2026-05-04). v1 dispatcher fires Instagram Reel only. Facebook Reel + Pinterest Pin to be added when Metricool schedule confirms IG path is stable.
