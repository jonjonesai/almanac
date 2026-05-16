# The Warm-Hug Doctrine

The single most consequential decision in this pipeline is *what the script is allowed to say*. Everything else — the visual identity, the music, the karaoke captions — is variation around that decision.

## Locked rule

> **The script tells a story, an insight, or a fact. The script never sells the product.**

Not in the body. Not in the close. Not in beat 7. Brand presence comes from:

- The **outro card** (e.g. "Wear the sky." / "Carry the night." / "Choose your air.")
- The **brand-tag overlay** on scene 1
- The **post caption**
- The **bio link**
- The **visual identity** (color, font, motion)

The script's only job is to be worth following. People sophisticated enough to be in the ICP will find the products themselves.

## Why

The opposite doctrine — "every social video is a CTA" — produces content that no human enjoys watching, gets scrolled past in 1.2 seconds, and trains the algorithm to never serve it again. The pitch IS the failure.

A warm-hug video has different math:

- Watch-time is high (the story actually pays off).
- Saves and shares happen (people forward facts, not pitches).
- The algorithm reinforces it.
- A small fraction of viewers click the bio link and convert at much higher intent than impression-buyers would.

It's slower. It compounds. It's almost the only model that works at small budget.

## The per-beat contract

Every script is 7 beats, ~60–80 words total. Each beat has a declared content type. The pipeline enforces this through the prompt; deviations earn a re-roll.

| Beat | Role | Constraint |
|---|---|---|
| 1 | Hook | Pose the surprising fact with ONE concrete specific quoted literally from `verified_facts` — a date, number, name, place. Stop the scroll. |
| 2 | Tension | Name who this is for or what's at stake. Concrete, not vague vibes. Reason the audience should care. |
| 3 | Reveal | Mechanism / origin / context. The HOW or WHY behind the hook. Cite a literal value if available. |
| 4 | Reinforce | History or prior context — when did this start, where does it come from, what came before? Quote a year, place, person literally. |
| 5 | Surprise | A non-obvious angle, deeper layer, hidden connection, theoretical or traditional framing. Something the audience didn't already know. |
| 6 | Insight | Practical takeaway or per-segment guidance. Specific not generic. |
| 7 | Stinger | Poetic close, lock-in feeling, callback to the hook, aphorism. **NO product mention. NO sales language. NO 'wear', 'buy', 'shop', 'drop', 'tee', 'hoodie', 'merch'.** |

## Examples of beat-7 lines that land

```
"Build phase. Body anchored. The cohort that pays attention wins."
"Six weeks of finishing. Body anchored. The cohort that pays attention wins."
"Staten Island basement to Cleveland stage. Thirty-three years. The sword stays sharp."
"Sometimes the molecule arrives before the body knows it has been answered."
"The sky over Magong remembers every spark."
"Slow holiday, ancient one, every two hundred and ten days."
```

Each one names something specific. Each one closes a feeling. None of them mentions a product.

## Anti-patterns (banned)

```
"Wear the marker."                            ← product mention
"Pin the moment to your chest."              ← product mention
"Drop one before the sky drops it."          ← product mention
"Worn by the people who saw it first."       ← product mention
"Mars in Taurus 2026 tee, stamped for..."    ← product mention
```

If your beat 7 sounds like a t-shirt logo, regenerate.

## Brand-specific extensions

Each brand may add its own brand-voice constraints in `brand.json` (`brand_voice` + `anti_patterns`). These layer on top of the universal doctrine — they never relax it. Examples:

- **OA** (wellness, science) — no medical claims; preserve study language ("improved sleep quality") never paraphrase to "treats."
- **WLH** (horoscope) — no fortune-telling certainty; speak to transits, not destiny; no sun-sign essentialism.
- **CC** (hip-hop) — always name the era, the record, the producer; never generic "rap" references.
- **TM** (Taiwan) — always name the place (Magong, Pingxi, Lukang); never abstract "Taiwan."
- **UT** (Bali artisan) — always name the village, plant, maker; never greenwashing language alone.

The pattern is: each brand's voice constrains the *texture*, but the *structure* (warm-hug, no product, 7 beats) is locked at the framework level.
