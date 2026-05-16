# <Brand Display Name> — SP2 Video Visual Identity

> Template — fill all 8 sections. Delete this blockquote when done. Required: every section must be present (sp2_pipeline.py reads several of these into the script-gen prompt; missing sections = generic LLM output, not brand-locked).

## Style Prompt

<1 paragraph, 60-100 words. The brand's visual personality. What aesthetic, what era, what feeling, what palette. Goes into FLUX/Freepik prompts implicitly via brand voice. Reference brand site, look book, hero pages if they exist. Be specific: "deep cosmic indigo canvas with antique-gold and amethyst-purple accents, hand-painted art nouveau astrology illustration backgrounds" beats "mystical purple vibe.">

## Product context (CRITICAL)

<2-3 sentences. What the brand SELLS, and what it does NOT sell. This is the wedge-vs-substitute boundary the script-gen MUST respect. Example WLH: "WLH is a personalized horoscope merch brand — tees, hoodies, stickers, mugs, prints. Not scent / aromatherapy / essential oils (those are OrganicAromas + Utama Spice lanes). Every SP2 script must close on a wearable form, never a ritual prescription.">

## Colors

| Hex | Role |
| --- | --- |
| `#xxxxxx` | Primary — accent/emphasis color |
| `#xxxxxx` | Canvas — primary dark base |
| `#xxxxxx` | Deep secondary surface |
| `#xxxxxx` | Off-white body text |
| `#xxxxxx` | Tertiary accent (rare/highlight use) |
| `#xxxxxx` | Lower-third labels, secondary text |

<1 sentence on caption-on-image treatment: white body + accent emphasis words on a gradient scrim, etc.>

## Typography

- **Display headlines** (60-130px): `<font>` weight 400 — <one-line aesthetic justification>
- **Accent emphasis words**: <font + style — italic if available>
- **Captions / body** (38-56px): `<font>` weight 700 — <one-line legibility justification>
- **Lower-third labels** (20-28px): `<font>` weight 500, letter-spacing 0.22em uppercase

## Motion

- Tempo: <slow celestial / medium wellness / punchy hip-hop / etc> — entrance durations <1.0-1.5s slow vs 0.3-0.6s fast>
- Easing: `<gsap ease>` for organic entrances; `<gsap ease>` for revelation; `<gsap ease>` for ambient drift
- Staggers: <ms range> between elements
- Ken Burns: <scale start → end> over scene duration
- Scene transitions: <crossfade only / hard cuts allowed / shader effects allowed>

## Media generation

- **Mode:** `photo` via Freepik premium OR `illustration` via FLUX-schnell. Pick one based on whether stock libraries can deliver the aesthetic.
- **Style suffix (illustration only):** <single style-prompt string, appended to every per-beat query>

## Topic sourcing (CRITICAL)

<2-4 sentences. WHERE topics come from for THIS brand. Generic LLM gen is forbidden — every brand must have a real-data source. Examples:
- WLH: skyfield (`scripts/today_sky.py`) + JPL DE421 ephemeris. Real planetary positions only.
- OA: Airtable Social Queue populated by social-researcher (community + research-driven).
- CC: Airtable Social Queue + hip-hop news researcher.
- TaiwanMerch: Taiwan-anchored topics with intrinsic regional relevance.

If the brand doesn't have a real-data source defined yet, building one is a prerequisite — not optional.>

## Script must close on <brand wedge>

<1-3 sentences. The mandatory last-beat constraint. What every script's final 7th beat must point at. Example WLH: "Beats 1-6 explain the sky event. Beat 7 lands on a wearable: 'Wear the marker.' / 'The 7-year tee.' / 'Worn by the people who saw it first.' — NEVER on burning herbs, anointing oils, or scent prescription.">

## What NOT to do

<6-12 bullet anti-patterns. Be ruthless. Each item enforced both at script-gen (Claude prompt) and at post-gen lint. Examples:
- No <category the brand competes against>
- No <stylistic tic that breaks tonal envelope>
- No <claim type that triggers compliance issues> — medical, financial, fortune-telling
- No more than 2 font families per composition
- **No em-dashes in any caption or title text** — Jon-wide rule
- No <motion style that breaks brand pacing> — `back`/`elastic`/`bounce` for slow brands, etc.>

## Active social platforms

<List which Metricool networks this brand fires to and which it skips and WHY. Example: "Facebook, Instagram, Pinterest. NO TikTok (not connected to Metricool). NO Twitter (deactivated 2026-05-04). v1 dispatcher fires Instagram Reel only.">
