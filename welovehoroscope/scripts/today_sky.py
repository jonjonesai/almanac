#!/usr/bin/env python3
"""Pull today's actual sky from JPL DE421 ephemeris via skyfield.

Returns a JSON object describing the current state of the sky in astrological
terms: each planet's zodiac sign, degree within sign, whether retrograde, plus
moon phase. This is the actual astronomy data NASA publishes — the same
ephemeris every serious astrology tool consumes under the hood.

Output goes to stdout as JSON. The orchestrator (or a topic-researcher script)
turns this raw sky into a WLH-shaped topic + angle + notes.

Usage:
    today_sky.py [YYYY-MM-DD]   # defaults to today UTC

Requires skyfield: python3 (with skyfield installed) today_sky.py
"""
import json
import sys
from datetime import datetime, timezone, timedelta

from skyfield.api import load

SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]

# Planets we care about for WLH content. Skyfield names → display names.
PLANETS = [
    ("sun", "Sun"),
    ("moon", "Moon"),
    ("mercury", "Mercury"),
    ("venus", "Venus"),
    ("mars", "Mars"),
    ("jupiter barycenter", "Jupiter"),
    ("saturn barycenter", "Saturn"),
    ("uranus barycenter", "Uranus"),
    ("neptune barycenter", "Neptune"),
    ("pluto barycenter", "Pluto"),
]

# Hermetic / Culpeper / Agrippa planet-scent rulerships. Pulled from canonical
# Western magical correspondence tables. Each planet maps to scents traditionally
# burned, worn, or used in ritual for that planetary day/sphere.
PLANET_SCENTS = {
    "Sun": ["frankincense", "cinnamon", "bay laurel", "saffron", "marigold", "juniper"],
    "Moon": ["jasmine", "sandalwood", "ylang-ylang", "eucalyptus", "camphor", "lemon balm"],
    "Mercury": ["lavender", "peppermint", "rosemary", "lemongrass", "dill", "fennel"],
    "Venus": ["rose", "geranium", "vanilla", "palmarosa", "neroli", "violet"],
    "Mars": ["ginger", "black pepper", "dragon's blood", "tobacco", "basil", "garlic"],
    "Jupiter": ["clary sage", "cedarwood", "oakmoss", "magnolia", "melissa", "sage"],
    "Saturn": ["myrrh", "vetiver", "patchouli", "cypress", "oakmoss", "frankincense"],
    "Uranus": ["peppermint", "eucalyptus", "lemon", "citrus", "ozone"],
    "Neptune": ["lotus", "jasmine", "sea salt", "kelp", "vetiver", "opium poppy"],
    "Pluto": ["patchouli", "vetiver", "oud", "smoke", "myrrh"],
}

MOON_PHASE_NAMES = [
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
    "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
]


def ecliptic_lon_deg(eph, ts, planet_name: str, t) -> float:
    """Return geocentric ecliptic longitude (tropical zodiac, deg 0..360)."""
    earth = eph["earth"]
    target = eph[planet_name]
    astrometric = earth.at(t).observe(target)
    lat, lon, dist = astrometric.ecliptic_latlon()
    return lon.degrees % 360


def lon_to_sign(lon: float) -> tuple[str, float]:
    sign_idx = int(lon // 30)
    deg_in_sign = lon - sign_idx * 30
    return SIGNS[sign_idx], deg_in_sign


def moon_phase_name(angle_deg: float) -> str:
    # Angle from Sun to Moon as seen from Earth (0=New, 180=Full, 360=New again).
    a = angle_deg % 360
    idx = int((a + 22.5) // 45) % 8
    return MOON_PHASE_NAMES[idx]


def compute_sky(date_str: str | None = None) -> dict:
    ts = load.timescale()
    eph = load("de421.bsp")

    if date_str:
        dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    t_today = ts.from_datetime(dt)
    t_tomorrow = ts.from_datetime(dt + timedelta(hours=24))

    planets_out = []
    for ephem_key, display in PLANETS:
        lon_today = ecliptic_lon_deg(eph, ts, ephem_key, t_today)
        lon_tomorrow = ecliptic_lon_deg(eph, ts, ephem_key, t_tomorrow)
        sign, deg = lon_to_sign(lon_today)
        # Retrograde = apparent longitude decreasing. Handle 360→0 wraparound.
        delta = (lon_tomorrow - lon_today + 540) % 360 - 180
        retrograde = bool(delta < 0 and display not in ("Sun", "Moon"))
        speed_deg_per_day = float(delta)
        planets_out.append({
            "planet": display,
            "longitude_deg": float(round(lon_today, 3)),
            "sign": sign,
            "degree_in_sign": float(round(deg, 3)),
            "retrograde": retrograde,
            "speed_deg_per_day": round(speed_deg_per_day, 4),
            "scents": PLANET_SCENTS.get(display, []),
        })

    sun_lon = next(p["longitude_deg"] for p in planets_out if p["planet"] == "Sun")
    moon_lon = next(p["longitude_deg"] for p in planets_out if p["planet"] == "Moon")
    sun_moon_angle = (moon_lon - sun_lon + 360) % 360
    moon_phase = moon_phase_name(sun_moon_angle)
    illumination_pct = round((1 - abs(180 - sun_moon_angle) / 180) * 100, 1)

    return {
        "date_utc": dt.strftime("%Y-%m-%d"),
        "planets": planets_out,
        "retrogrades": [p["planet"] for p in planets_out if p["retrograde"]],
        "moon": {
            "phase_name": moon_phase,
            "sun_moon_angle_deg": round(sun_moon_angle, 2),
            "illumination_pct": illumination_pct,
            "sign": next(p["sign"] for p in planets_out if p["planet"] == "Moon"),
        },
        "sun_sign": next(p["sign"] for p in planets_out if p["planet"] == "Sun"),
    }


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sky = compute_sky(date_arg)
    print(json.dumps(sky, indent=2))
