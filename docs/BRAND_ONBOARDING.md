# Adding a new brand

The 5 brands in this repo are concrete examples. To add your own:

## 1. Decide the tonal envelope

Before writing config, pick the brand's tonal envelope. The 5 examples cover a wide range:

| Example | Tone | Visual mode | Music |
|---|---|---|---|
| `welovehoroscope` | Cosmic, mystical, slow | Hand-painted Studio Ghibli illustration (FLUX) | Ethereal harp + ambient pads |
| `customcreative` | Hip-hop, punchy, reverent | Vintage gritty (Freepik + video b-roll) | Lo-fi boom-bap |
| `organicaromas` | Wellness, neuroscience, calm | Premium lifestyle photo (Freepik) | Slow contemplative pads |
| `utamaspice` | Artisan, slow, ritual | Tropical golden hour (Freepik) | Indonesian-inflected ambient |
| `taiwanmerch` | Island energy, neon | Studio Ghibli watercolor (FLUX) | Synthwave neon |

Fork the example closest to your brand's tonal envelope.

## 2. Brand directory

```
your-brand/
├── brand.json            # voice, colors, fonts, Airtable IDs, anti-patterns
├── DESIGN.md             # visual identity manifesto (human-readable)
├── hyperframes.json      # HyperFrames project config (npx hyperframes init)
├── meta.json             # HyperFrames project metadata
├── package.json          # HyperFrames dependencies
├── scripts/
│   ├── build-karaoke-html.py    # forked from a similar brand
│   └── <brand>_topic_researcher.py   # optional but recommended
├── assets/               # populated per fire (gitignored)
└── renders/              # populated per fire (gitignored)
```

## 3. Configure `brand.json`

Start from [`templates/brand.template.json`](../templates/brand.template.json). Required fields:

- `brand` — lowercase no-space slug, must match directory name
- `display_name` — human-readable brand name
- `voice_id` + `voice_label` — ElevenLabs voice (see https://elevenlabs.io/voice-lab)
- `primary_color` + `canvas_bg` — hex codes for accent + dark canvas
- `tagline`, `outro_top`, `outro_bottom`, `brand_mark` — the outro card text
- `display_font` + `body_font` — Google Fonts (2 max per composition)
- `cloudinary_folder` — where rendered mp4s land
- `metricool_blog_id` + `social_handle` — your Metricool config
- `social_queue.base_id` + `table_id` — Airtable for topic queue (one base per brand)
- `anti_patterns` — list of brand-voice-specific banned patterns
- `brand_voice` — one-paragraph voice description, fed directly into Claude's prompt
- `music_prompt` — MusicGen prompt for background score

Optional:

- `prefer_video_clips: true|false` — whether Freepik video b-roll is preferred over static images
- `media_style: "illustration"|"photo"` — FLUX illustration vs Freepik photography
- `illustration_style_suffix` — when illustration mode, the per-beat style suffix appended to every FLUX prompt
- `beat_sync.enabled` + `snap_window_sec` — snap scene transitions to MusicGen beat boundaries
- `beat_contract` — override the default 7-beat content contract (rare)

## 4. Configure `DESIGN.md`

The DESIGN.md is the *human-readable* version of brand.json. It documents:

- Style prompt (the elevator pitch of the brand's aesthetic)
- Product context (what the brand sells, what it doesn't)
- Colors (with role descriptions)
- Typography (with use cases)
- Motion (eases, durations, what NOT to do)
- Media generation rules
- Topic sourcing rules
- Script doctrine (the warm-hug doctrine, with brand-specific examples)
- What NOT to do

This file gets read by humans (you, future you, future contributors) more often than the brand.json. Make it precise.

Start from [`templates/DESIGN.template.md`](../templates/DESIGN.template.md).

## 5. Fork a build-karaoke-html.py

Pick the most tonally similar example:

```bash
cp welovehoroscope/scripts/build-karaoke-html.py your-brand/scripts/
```

Adjust:

- Caption font sizes (44–68px typical range)
- Accent word styling (italic / uppercase / different font?)
- Title card typography (display font size, weight, italic)
- Outro card typography (often bigger than the title, splits across two lines)
- Easing curves (`expo.out` for revelation moments, `sine.inOut` for ambient, `power2.out` for organic, `power3.out` for punchy)
- Scene fade timings
- Ken Burns scale (1.0 → 1.06 gentle, 1.0 → 1.10 punchier)

## 6. (Recommended) Write a topic researcher

A topic researcher is what makes the pipeline autonomous. Without it, you'll hand-curate Airtable rows; with it, the pipeline runs itself.

Pick a sourcing strategy:

- **Structured live data** — if your brand maps to something with an API (ephemerides, sports stats, market data, weather, government records)
- **Fixed calendar** — if your brand has annual recurring anchor events (festivals, anniversaries, holidays)
- **API research** — if your brand maps to academic / curated content (PubMed for wellness, OpenAlex for academic, Wikipedia for general history)
- **Reddit signal** — top-weekly threads in 2–4 relevant subreddits, scored by named-entity matches

Most brands need a *mix* — a calendar for always-on baseline + Reddit for fresh signal. See `customcreative/scripts/cc_topic_researcher.py` and `utamaspice/scripts/ut_topic_researcher.py` for hybrid examples.

Every researcher should:

1. **Score candidates** — higher = better editorial fit for the brand.
2. **Emit `verified_facts`** — the structured dict your script-gen will quote literally.
3. **Frame via Claude** — produce `topic`, `angle`, `key_points`, `visual_notes` shaped for the brand voice.
4. **Embed facts in the brief** — wrap your `verified_facts` JSON between `[VERIFIED_FACTS_JSON_START]` and `[VERIFIED_FACTS_JSON_END]` inside the Research Brief text.
5. **Write to Airtable** — Status=Queued row in the brand's Social Queue.

Then schedule the researcher to run weekly (cron, GitHub Actions, or any scheduler).

## 7. Smoke-test

```bash
# Generation-only (no Metricool, no Airtable mutation)
python3 sp2_pipeline.py \
  --brand your-brand \
  --topic-override "Your test topic" \
  --angle-override "Your test angle with verified_facts" \
  --dryrun

# Or with --script-only to just generate the 7-beat plan (no API spend beyond Claude script-gen)
python3 sp2_pipeline.py --brand your-brand --script-only
```

Inspect:

- `<brand>/renders/run-<ts>/plan.json` — the generated 7-beat script
- `<brand>/index.html` — the composition (open in browser to preview)
- `<brand>/renders/run-<ts>/<brand>_*.mp4` — the rendered mp4

Frame-by-frame audit:

```bash
ffmpeg -i <brand>/renders/run-<ts>/*.mp4 -vf "select='eq(mod(n,30),0)'" \
  -fps_mode vfr -q:v 2 /tmp/frame_%03d.jpg -y
```

Look for: title card visibility, caption layout, fade-out timing, brand-tag positioning.

## 8. Ship

Once smoke-test passes:

- Schedule the topic researcher (cron weekly).
- Schedule the pipeline to fire from the queue (cron 1–3× per week).
- Connect Metricool networks (Instagram Reel, TikTok, Pinterest Pin, Facebook Reel).
- Watch the first week. The DRYRUN sentinel at `$HEARTBEAT_DIR/DRYRUN` is your kill switch.
