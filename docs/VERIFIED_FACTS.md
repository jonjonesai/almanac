# The `verified_facts` pattern

Claude is good at writing. Claude is *not* good at remembering numbers. Without a structural intervention, a generated 30-second script will drift on factual claims: "six years of fire" when the real value is "six weeks," "2,072 patients" pronounced as "twenty seventy-two" by the TTS engine, "the last time was 1987" when it was actually 1941.

`verified_facts` solves this by making the structural source of every numerical claim **explicit and literal**.

## The flow

```
                ┌──────────────────────────────────────────┐
                │  Topic researcher (per-brand)            │
                │  Pulls from skyfield / PubMed / Reddit / │
                │  cultural calendar / album anniversaries │
                └──────────────────────────────────────────┘
                                  ↓
                ┌──────────────────────────────────────────┐
                │  build_verified_facts()                  │
                │  Emits a structured JSON dict with every │
                │  date / count / name as a literal value  │
                └──────────────────────────────────────────┘
                                  ↓
                ┌──────────────────────────────────────────┐
                │  Embed in Research Brief (Airtable)      │
                │  Between [VERIFIED_FACTS_JSON_START] and │
                │  [VERIFIED_FACTS_JSON_END] markers       │
                └──────────────────────────────────────────┘
                                  ↓
                ┌──────────────────────────────────────────┐
                │  sp2_pipeline.pick_topic()               │
                │  Extracts the JSON block out of the      │
                │  research brief, stores as a structured  │
                │  dict on the topic object                │
                └──────────────────────────────────────────┘
                                  ↓
                ┌──────────────────────────────────────────┐
                │  gen_script_plan()                       │
                │  Injects "VERIFIED FACTS (literal ground │
                │  truth — quote these EXACT values)" into │
                │  Claude's prompt + the per-beat contract │
                └──────────────────────────────────────────┘
                                  ↓
                ┌──────────────────────────────────────────┐
                │  Generated script                        │
                │  Every numerical claim is now either:    │
                │  (a) a literal value from verified_facts │
                │  (b) omitted entirely                    │
                └──────────────────────────────────────────┘
```

## Anatomy of a `verified_facts` dict

```json
{
  "source": "Frontiers in Psychiatry 2025 — Inhalation aromatherapy for comorbid insomnia: meta-analysis",
  "source_url": "https://www.frontiersin.org/journals/psychiatry/articles/10.3389/fpsyt.2025.1485693/full",
  "source_run_iso_utc": "2026-05-16T06:00:00Z",
  "subject": "Aromatherapy meta-analysis on sleep / insomnia",
  "publication_year": 2025,
  "rct_count": 27,
  "total_participants": 2072,
  "primary_compound": "linalool",
  "secondary_compound": "limonene",
  "oils_named_in_review": ["lavender", "rose", "sweet orange", "valerian"],
  "outcome_measures": {
    "sleep_quality_md": -0.54,
    "time_to_fall_asleep_md": -0.58
  },
  "mechanism_primary": "Linalool binds GABA-A receptors — same family as benzodiazepines, gentler."
}
```

Three things make this work:

1. **Every fact has a primitive value.** `27` not `"twenty-seven trials"`. The numeric type is structurally checkable.
2. **Source attribution.** Anything not in `verified_facts` is interpretive layer.
3. **Mechanism strings are themselves quoted verbatim.** The phrase `"same family as benzodiazepines, gentler"` came directly from the meta-analysis abstract. Claude is then required to reproduce it literally rather than paraphrasing it.

## How the prompt uses it

The pipeline renders `verified_facts` into a block at the top of Claude's prompt:

```
VERIFIED FACTS (literal ground truth — quote these EXACT values, do not
paraphrase numbers, do not invent dates, do not round):
  Source: Frontiers in Psychiatry 2025 — Inhalation aromatherapy...
  Subject: Aromatherapy meta-analysis on sleep / insomnia
  RCT count: 27
  Total participants: 2072
  Primary compound: linalool
  ...

FACT DISCIPLINE:
- Any numerical value (degrees, days, weeks, months, years, dates,
  percentages) MUST be either (a) a literal value from VERIFIED FACTS
  above, or (b) omitted entirely. NEVER paraphrase a number.
- Any historical reference MUST be either (a) the prior_window year
  from VERIFIED FACTS quoted exactly, or (b) framed in approximate
  terms that cannot be wrong ('decades ago'). NEVER invent a year.

PER-BEAT CONTENT CONTRACT (each beat MUST do its job):
- Beat 1: hook + ONE concrete specific quoted literally
- Beat 2: tension + named consequence
- ...
```

The result on this exact input was a script that opens:

> *"A 2025 meta-analysis pooled twenty-seven randomized trials and 2072 adults to ask one question."*

The number 27 appears as "twenty-seven" (spelled out so TTS pronounces it correctly). The number 2072 appears as a digit (caught in a year-disambiguation edge case — see below). Both numbers are quoted *literally*, not invented.

## TTS gotchas

Some patterns become visible only at the audio layer. A few rules baked into the prompt:

- **Year-format 4-digit numbers (1993, 2025, 2026) stay as digits** — ElevenLabs pronounces them correctly ("nineteen ninety-three", "twenty twenty-five").
- **Non-year 4-digit-or-more numbers must be spelled out** — otherwise ElevenLabs reads them as years too ("2072 adults" → "twenty seventy-two" instead of "two thousand seventy-two").
- **Small numbers (1–30) spelled out** in body text — "twenty-seven trials" reads naturally; "27 trials" can stutter.
- **Always put a space between number and unit** — "0 deg" not "0deg", "22 months" not "22months".

## Where the structure comes from (per-brand)

Different brands have different source structures, but every researcher emits the *same shape* of `verified_facts`:

| Brand | Source | What becomes verified_facts |
|---|---|---|
| WLH | skyfield JPL DE421 ephemeris | planet, sign, degree, ingress date, current_window, prior_window |
| OA | PubMed E-utilities | PMID, journal, year, RCT count, participants, compounds, effect sizes |
| CC | Album-release calendar + Reddit | artist, album, release date, age, producers, label |
| UT | Bali cultural calendar + Reddit | festival, village, dates, anchor terms, ritual phases |
| TM | Taiwan cultural calendar + Reddit | event, city, venue, dates, IP partner, anti-cliche notes |

Each researcher writes a `build_verified_facts_<source>()` function that emits the brand-appropriate dict.

## Adding a new brand: minimum facts

When designing a new brand's researcher, ask: *what is the verifiable backbone here?* It usually falls into one of:

- **Live structured data** — ephemerides, market data, weather, sports stats.
- **Fixed structured data** — release dates, anniversaries, holidays, historical events.
- **Curated API** — PubMed, OpenAlex, Wikidata, government open data.
- **Reddit + entity extraction** — score threads by specific named entities in title + selftext.

The pattern is the same: pull the structured data, extract the literal values, hand them to Claude with an instruction to quote them verbatim.
