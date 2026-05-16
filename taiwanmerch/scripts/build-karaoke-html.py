#!/usr/bin/env python3
"""Build CC HyperFrames index.html with per-word karaoke captions from WhisperX timings.

Forked from OA build-karaoke-html.py 2026-05-12. Differences:
- Bottom scrim gradient uses canvas_bg dynamically (OA hardcodes its navy).
- Accent words use brand display font (Anton) with synthetic italic + neon color.
- Slightly faster scene entrances (power3.out vs sine.inOut), matching CC DESIGN.md energy.

Config-driven (same shape as OA). Usage:
    build-karaoke-html.py <config.json>
"""
import json
import sys
from pathlib import Path


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    s = hex_str.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def build(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text())
    project_dir = Path(cfg["project_dir"])
    words = json.loads((project_dir / cfg["words_path"]).read_text())
    bi = cfg["brand_identity"]
    beats = cfg["beats"]
    scene_timings = cfg["scene_timings"]
    duration = cfg["duration"]

    title_card = cfg.get("title_card") or {"topic_title": "", "duration": 0.0}
    audio_offset = float(cfg.get("audio_offset", 0.0))
    topic_title = title_card.get("topic_title", "")
    title_duration = float(title_card.get("duration", audio_offset))

    primary = bi["primary_color"]
    bg = bi["canvas_bg"]
    bg_r, bg_g, bg_b = hex_to_rgb(bg)
    display_font = bi["display_font"]
    body_font = bi["body_font"]
    tagline = bi["tagline"]
    brand_mark = bi["brand_mark"]
    outro_top = bi["outro_top"]
    outro_bottom = bi["outro_bottom"]
    display_name = bi["display_name"]

    def caption_html(beat):
        spans = []
        for j, gi in enumerate(range(beat["lo"], beat["hi"])):
            w = words[gi]["w"]
            cls = "word accent" if j in beat["accent"] else "word"
            spans.append(f'<span class="{cls}" id="w-{gi}">{w}</span>')
        return " ".join(spans)

    scenes_html = ""
    for i, beat in enumerate(beats):
        cap = caption_html(beat)
        brand_tag = (
            f'<div class="brand-tag">{display_name} · {tagline}</div>' if i == 0 else ""
        )
        bg_type = beat.get("bg_type", "image")
        st = scene_timings[i]
        if bg_type == "video":
            bg_html = (
                f'<video class="scene-bg clip" muted loop autoplay playsinline preload="auto" '
                f'data-start="{st["start"]}" data-duration="{st["duration"]}" data-track-index="{i+1}" '
                f'src="assets/{beat["bg"]}"></video>'
            )
        else:
            bg_html = f'<div class="scene-bg" style="background-image: url(\'assets/{beat["bg"]}\');"></div>'
        scenes_html += f"""
      <div id="{beat['scene']}" class="scene" data-bg-type="{bg_type}">
        {bg_html}
        <div class="scene-overlay"></div>
        {brand_tag}
        <div class="caption">{cap}</div>
      </div>
"""

    word_tweens = []
    for beat in beats:
        for gi in range(beat["lo"], beat["hi"]):
            w = words[gi]
            word_tweens.append(f"        {{ sel: '#w-{gi}', t: {w['start'] + audio_offset:.3f} }}")
    words_js = ",\n".join(word_tweens)

    scenes_js_lines = [
        f"        {{ id: '#scene-title', start: 0, duration: {title_duration:.2f}, bgType: 'title' }}"
    ]
    for i, beat in enumerate(beats):
        t = scene_timings[i]
        scenes_js_lines.append(
            f"        {{ id: '#{beat['scene']}', start: {t['start']}, duration: {t['duration']}, bgType: '{beat.get('bg_type', 'image')}' }}"
        )
    t = scene_timings[-1]
    scenes_js_lines.append(
        f"        {{ id: '#scene-outro', start: {t['start']}, duration: {t['duration']}, bgType: 'outro' }}"
    )
    scenes_js = ",\n".join(scenes_js_lines)

    df_query = display_font.replace(" ", "+")
    bf_query = body_font.replace(" ", "+")
    # Anton has only weight 400; Inter we want 500/600/700.
    fonts_url = (
        f"https://fonts.googleapis.com/css2?"
        f"family={df_query}"
        f"&family={bf_query}:wght@500;600;700&display=swap"
    )

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <link href="{fonts_url}" rel="stylesheet" />
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: {bg}; }}
      #root {{
        position: relative;
        width: 1080px;
        height: 1920px;
        background: {bg};
        overflow: hidden;
        font-family: "{body_font}", sans-serif;
      }}
      .scene {{ position: absolute; inset: 0; opacity: 0; will-change: opacity, transform; }}
      .scene-bg {{ position: absolute; inset: 0; background-size: cover; background-position: center; will-change: transform; }}
      video.scene-bg {{ width: 100%; height: 100%; object-fit: cover; }}
      .scene-overlay {{
        position: absolute; inset: 0;
        background: linear-gradient(180deg, rgba({bg_r},{bg_g},{bg_b},0) 0%, rgba({bg_r},{bg_g},{bg_b},0) 50%, rgba({bg_r},{bg_g},{bg_b},0.94) 95%);
        pointer-events: none;
      }}
      .caption {{
        position: absolute;
        left: 80px; right: 80px; bottom: 280px;
        font-family: "{body_font}", sans-serif;
        font-weight: 700;
        font-size: 58px;
        line-height: 1.22;
        color: #fafafa;
        text-shadow: 0 2px 20px rgba(0,0,0,0.95), 0 1px 6px rgba(0,0,0,1);
        letter-spacing: -0.005em;
      }}
      .word {{
        display: inline-block;
        opacity: 0;
        margin-right: 0.25em;
        will-change: opacity, transform;
      }}
      .word.accent {{
        color: {primary};
        font-style: italic;
        font-family: "{display_font}", "Impact", sans-serif;
        font-weight: 400;
        letter-spacing: 0.01em;
        text-transform: uppercase;
      }}
      .brand-tag {{
        position: absolute; top: 100px; left: 80px;
        font-family: "{body_font}", sans-serif;
        font-weight: 500; font-size: 22px;
        letter-spacing: 0.22em;
        color: {primary};
        text-transform: uppercase;
        text-shadow: 0 1px 8px rgba(0,0,0,0.7);
        opacity: 0.92;
      }}
      #scene-title {{ background: {bg}; }}
      #scene-title .title-text {{
        position: absolute; left: 70px; right: 70px; top: 50%;
        transform: translateY(-50%);
        font-family: "{display_font}", "Impact", sans-serif;
        font-weight: 400;
        font-size: 108px; line-height: 1.02;
        color: #fafafa;
        text-align: center;
        text-transform: uppercase;
        letter-spacing: 0.01em;
      }}
      #scene-title .title-brandmark {{
        position: absolute; bottom: 220px; left: 0; right: 0;
        text-align: center;
        font-family: "{body_font}", sans-serif;
        font-weight: 700; font-size: 26px;
        letter-spacing: 0.32em;
        color: {primary};
        text-transform: uppercase;
      }}
      #scene-outro {{ background: {bg}; }}
      #scene-outro .outro-title {{
        position: absolute; left: 80px; right: 80px; top: 50%;
        transform: translateY(-50%);
        font-family: "{display_font}", "Impact", sans-serif;
        font-weight: 400;
        font-size: 128px; line-height: 1.0;
        color: #fafafa;
        text-align: center;
        text-transform: uppercase;
        letter-spacing: 0.01em;
      }}
      #scene-outro .outro-title .accent-primary {{
        color: {primary};
        display: block;
        margin-top: 16px;
      }}
      #scene-outro .brand-mark {{
        position: absolute; bottom: 220px; left: 0; right: 0;
        text-align: center;
        font-family: "{body_font}", sans-serif;
        font-weight: 700; font-size: 28px;
        letter-spacing: 0.34em;
        color: {primary};
        text-transform: uppercase;
      }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{duration}" data-width="1080" data-height="1920">
      <div id="scene-title" class="scene">
        <div class="title-text">{topic_title}</div>
        <div class="title-brandmark">{brand_mark} · {tagline}</div>
      </div>
{scenes_html}
      <div id="scene-outro" class="scene">
        <div class="outro-title">
          {outro_top}
          <span class="accent-primary">{outro_bottom}</span>
        </div>
        <div class="brand-mark">{brand_mark}</div>
      </div>

      <audio id="voice" data-start="{audio_offset:.2f}" data-duration="{duration - 2 - audio_offset:.1f}" data-track-index="10" src="{cfg['voice_path']}" data-volume="1.0"></audio>
      <audio id="music" data-start="0" data-duration="{duration}" data-track-index="11" src="{cfg['music_path']}" data-volume="0.18"></audio>
    </div>

    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});

      const scenes = [
{scenes_js}
      ];

      const words = [
{words_js}
      ];


      scenes.forEach((sc, i) => {{
        if (sc.bgType === "title") {{
          gsap.set(sc.id, {{ opacity: 1 }});
          tl.fromTo(sc.id + " .title-text", {{ y: 22, opacity: 0, scale: 0.97 }}, {{ y: 0, opacity: 1, scale: 1.0, duration: 0.40, ease: "expo.out" }}, 0.04);
          tl.fromTo(sc.id + " .title-brandmark", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: "power3.out" }}, 0.18);
          tl.to(sc.id, {{ opacity: 0, duration: 0.22, ease: "power3.inOut" }}, Math.max(0, sc.duration - 0.16));
          return;
        }}
        tl.to(sc.id, {{ opacity: 1, duration: 0.5, ease: "power3.out" }}, sc.start);
        // Ken Burns only for static image bgs — video clips have their own motion.
        if (sc.bgType === "image") {{
          tl.fromTo(sc.id + " .scene-bg", {{ scale: 1.0 }}, {{ scale: 1.10, duration: sc.duration, ease: "power2.inOut" }}, sc.start);
        }}
        if (sc.id === "#scene-1") {{
          tl.from(sc.id + " .brand-tag", {{ y: -18, opacity: 0, duration: 0.55, ease: "power3.out" }}, sc.start + 0.08);
        }}
        if (sc.id === "#scene-outro") {{
          tl.from(sc.id + " .outro-title", {{ y: 36, opacity: 0, duration: 0.9, ease: "expo.out" }}, sc.start + 0.2);
          tl.from(sc.id + " .brand-mark", {{ opacity: 0, duration: 0.7, ease: "power2.out" }}, sc.start + 0.9);
        }}
        // Fade-out anchored to current scene's end so last word of each beat
        // finishes at full opacity. Was: next - 0.35 → last word ate the fade.
        if (i < scenes.length - 1) {{
          tl.to(sc.id, {{ opacity: 0, duration: 0.5, ease: "power3.inOut" }}, sc.start + sc.duration - 0.5);
        }} else {{
          tl.to(sc.id, {{ opacity: 0, duration: 1.0, ease: "sine.inOut" }}, sc.start + sc.duration - 1.0);
        }}
      }});

      words.forEach(w => {{
        tl.to(w.sel, {{ opacity: 1, y: 0, duration: 0.16, ease: "power2.out" }}, w.t);
        tl.fromTo(w.sel, {{ y: 8 }}, {{ y: 0, duration: 0.16, ease: "power2.out" }}, w.t);
      }});

      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""

    out_path = project_dir / "index.html"
    out_path.write_text(html)
    print(
        f"Wrote {out_path} ({len(html)} chars), {len(beats)} scenes + outro, {sum(b['hi']-b['lo'] for b in beats)} word tweens"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: build-karaoke-html.py <config.json>", file=sys.stderr)
        sys.exit(2)
    build(sys.argv[1])
