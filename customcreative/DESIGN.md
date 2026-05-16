# Custom Creative — SP2 Video Visual Identity

## Style Prompt

Hip-hop merch with cultural authority. Streetwear-grade typography. Neon-yellow accent against deep black canvas (mirrors the LED-neon wedge product). Confident, percussive motion — but never frantic. Headlines hit like a sample drop. Captions read like crate-digger field notes. This is a brand for someone who knows the record, the year, the producer, the sample.

## Colors

| Hex       | Role                                                                |
| --------- | ------------------------------------------------------------------- |
| `#facc15` | Primary — neon yellow (highlights, key-word emphasis, LED-neon feel)|
| `#0a0a0a` | Near-black — primary canvas, deep visual ground                     |
| `#1f1f1f` | Off-black — secondary surface                                       |
| `#fafafa` | Off-white — primary body text on dark surface                       |
| `#a3a3a3` | Mid grey — secondary text                                           |
| `#ef4444` | Accent red — rare use, only for "drop / new / sold out" moments     |

Caption-on-image style: white body text + neon-yellow accent words in italic display serif. All caption text sits on a black gradient scrim so it reads cleanly over varied b-roll.

## Typography

- **Display headlines** (60-130px): `Anton` weight 400 — condensed sans-serif, streetwear headline DNA (Vetements / Supreme / sports-jersey feel)
- **Accent emphasis words**: `Anton` italic if available, else fall back to display weight with `font-style: italic`
- **Captions / body** (38-56px): `Inter` weight 700 — bold sans for video legibility
- **Lower-third labels** (20-28px): `Inter` weight 500, letter-spacing 0.18em uppercase, neon-yellow

## Motion

- Confident, percussive (0.6-1.2s entrance durations, faster than OA wellness)
- Easing: `power3.out` for headline drops; `expo.out` for revelations; `power2.inOut` for ambient ramps
- Staggers: 50-120ms between elements (tighter than OA — hip-hop has more energy)
- Image Ken Burns: 1.0 → 1.10 scale over scene duration (slightly more push than OA)
- Scene transitions: crossfade or hard cut on beat — NO shader transitions yet, NO chromatic-aberration glitches (cliché)

## What NOT to do

- No "urban" as an adjective. No generic "rap" references. Name the era, the record, the producer.
- No corporate hip-hop clichés. No "vibes" as a noun.
- No rainbow gradients on dark backgrounds (H.264 banding ugly).
- No fast shaky-cam, no rotating-text-on-axis gimmicks
- No staggers under 40ms — feels frantic even for hip-hop
- No more than 2 font families per composition
- **No em-dashes in any caption or title text** — replace with periods, commas, colons (Jon-wide brand voice rule)
- Never reference the brand or products in beats 1-6; outro card only.
