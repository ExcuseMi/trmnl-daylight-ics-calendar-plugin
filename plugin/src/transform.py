# TRMNL Serverless — Daylight ICS Calendar (3-day time-grid + sunrise/sunset)
#
# This is the plugin's serverless code. `serverless_language: python` in settings.yml
# tells TRMNL to run it; `trmnlp push` uploads it like any other src file (no manual paste).
# Entry point is run(input); it computes a native TRMNL-framework layout (hour axis + 3 day
# columns, events positioned by start time and sized by duration using h--[Ncqh] container-
# query heights) — real HTML/Liquid, not an image, so it renders crisply and fills any
# device (including the larger, portrait, 4-bit TRMNL X) via the framework's own responsive
# system instead of a fixed-aspect picture.
#
# Only the standard library + `requests` are guaranteed in the Serverless VM, so the ICS
# parsing and recurrence expansion below are hand-rolled (no `icalendar` dependency).
# Budget: 128 MB / 5 s — everything is bounded to the render window.

import math
import requests
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DAYS = 3
MAX_EVENTS_PER_DAY = 12
_WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def run(input):
    urls = _urls(_cf(input, "ics_url"))
    t = _I18N[_locale_of(input)]
    tzname = _cf(input, "time_zone").strip() or _user_tz(input) or "UTC"
    is_12h = _cf(input, "time_format").strip().lower() == "12h"
    location = _cf(input, "location")
    fahrenheit = _cf(input, "temperature_unit").strip().lower() == "fahrenheit"
    days_n = _int(_cf(input, "view_days"), DEFAULT_DAYS, 1, 7)
    show_title_bar = _cf(input, "show_title_bar").strip().lower() in ("true", "yes", "1")
    title_bar_pct = TITLE_BAR_PCT if show_title_bar else 0
    title_text = _title_text(input)

    tz = _resolve_tz(tzname, input)

    if not urls:
        return _empty(tzname, tz, t, days_n, "No ICS URL configured",
                       show_title_bar, title_bar_pct, title_text)

    now = datetime.now(tz)
    win_s = now.replace(hour=0, minute=0, second=0, microsecond=0)
    win_e = win_s + timedelta(days=days_n)

    occ = []
    errors = []
    for cal_idx, url in enumerate(urls):
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        try:
            resp = requests.get(url, timeout=4,
                                headers={"User-Agent": "TRMNL-ICS-Calendar"})
            resp.raise_for_status()
            _collect(resp.text, tz, win_s, win_e, occ, cal_idx)
        except Exception as exc:
            errors.append(str(exc))

    # Only surface an error if every feed failed; partial results still render.
    err = None
    if errors and not occ:
        err = "Fetch/parse failed: %s" % errors[0]

    # Bucket occurrences into day columns, split into timed vs all-day.
    raw_days = []
    for i in range(days_n):
        d0 = win_s + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        timed, allday = [], []
        for e in occ:
            if not (e["start"] < d1 and e["end"] > d0):
                continue
            if e["all_day"] or (e["end"] - e["start"]) >= timedelta(hours=24):
                allday.append(e)
            else:
                vs = max(e["start"], d0)
                ve = min(e["end"], d1)
                timed.append({
                    "h0": (vs - d0).total_seconds() / 3600.0,
                    "h1": (ve - d0).total_seconds() / 3600.0,
                    "title": e["title"], "cal_idx": e["cal_idx"],
                    "label": "%s–%s" % (_fmt_time(vs, is_12h), _fmt_time(ve, is_12h)),
                })
        timed.sort(key=lambda t: t["h0"])
        raw_days.append({
            "label": _day_label(d0, t),
            "is_today": i == 0,
            "timed": timed,
            "allday": [{"title": a["title"], "hue": _hue(a["cal_idx"]),
                        # Lets the template draw multi-day all-day events as one continuous
                        # banner (square off the edge that's mid-span) instead of a separate
                        # fully-rounded pill repeating in every day column it touches.
                        "continues_before": a["start"] < d0,
                        "continues_after": a["end"] > d1}
                       for a in allday[:3]],
        })

    # Fractional hour of "now" within today's column, e.g. 14:30 -> 14.5 — used to draw a
    # current-time marker. `win_s` (today's midnight) is always "now" with the clock zeroed,
    # so this is just the elapsed time since then.
    now_h = (now - win_s).total_seconds() / 3600.0
    sun, hourly_weather, daily_temps, weather_error = _fetch_sky(location, days_n, fahrenheit)
    for i, rd in enumerate(raw_days):
        rd["temp"] = daily_temps.get(i)
        rd["icon"] = _day_icon(hourly_weather.get(i)) if rd["temp"] else None

    # The emphasized ("important") range is automatic now, not a manual setting: it starts
    # at sunrise or the first meeting of the visible days, whichever is earlier, and ends at
    # sunset or the last meeting, whichever is later — so daylight hours and every actual
    # event are always in the expanded part of the grid, never stuck in the compressed
    # margin. Floor the start / ceil the end so a meeting or sunrise/sunset falling mid-hour
    # still pulls its whole hour into the emphasized range. Falls back to 8-22 if there's
    # neither sun data (no Location configured) nor any timed events to go on.
    sun_today = sun.get(0, [])
    sunrise_h = next((m["hour"] for m in sun_today if m["kind"] == "sunrise"), None)
    sunset_h = next((m["hour"] for m in sun_today if m["kind"] == "sunset"), None)
    event_starts = [e["h0"] for d in raw_days for e in d["timed"]]
    event_ends = [e["h1"] for d in raw_days for e in d["timed"]]
    start_candidates = [h for h in [sunrise_h] + event_starts if h is not None]
    end_candidates = [h for h in [sunset_h] + event_ends if h is not None]
    start_h = math.floor(min(start_candidates)) if start_candidates else 8
    end_h = math.ceil(max(end_candidates)) if end_candidates else 22
    end_h = max(end_h, start_h + 1)

    grid = _layout_native(raw_days, start_h, end_h, now_h, sun, hourly_weather,
                          title_bar_pct=title_bar_pct)

    return dict(grid, generated_at=int(now.timestamp()), tz=tzname, error=err,
                unavailable_label=t["unavailable"],
                has_events=any(d["timed"] or d["allday"] for d in raw_days),
                show_title_bar=show_title_bar, title_text=title_text,
                weather_error=weather_error)


# ---------------------------------------------------------------- input helpers

def _urls(raw):
    """Split a multi-line / comma-separated ICS field into clean URLs."""
    if not isinstance(raw, str):
        return []
    parts = raw.replace(",", "\n").splitlines()
    return [p.strip() for p in parts if p.strip()]


def _int(raw, default, lo, hi):
    try:
        return max(lo, min(hi, int(float(str(raw).strip()))))
    except (ValueError, TypeError):
        return default


def _cf(input, key):
    """Read a custom form field. Serverless exposes them both flat and nested."""
    if not isinstance(input, dict):
        return ""
    if key in input and isinstance(input[key], str):
        return input[key]
    try:
        cfv = input["trmnl"]["plugin_settings"]["custom_fields_values"]
        v = cfv.get(key, "")
        return v if isinstance(v, str) else ""
    except Exception:
        return ""


def _title_text(input):
    """The instance name the user gave this plugin in TRMNL, shown in the optional title bar."""
    try:
        name = input["trmnl"]["plugin_settings"]["instance_name"]
        return name if isinstance(name, str) and name.strip() else "Calendar"
    except Exception:
        return "Calendar"


def _safe_zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


def _empty(tzname, tz, t, days_n, msg, show_title_bar=False, title_bar_pct=0, title_text=""):
    now = datetime.now(tz)
    win_s = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = []
    for i in range(days_n):
        d0 = win_s + timedelta(days=i)
        days.append({
            "label": _day_label(d0, t),
            "is_today": i == 0, "timed": [], "allday": [],
        })
    grid = _layout_native(days, 8, 22, None, title_bar_pct=title_bar_pct)
    return dict(grid, generated_at=int(now.timestamp()), tz=tzname,
                error=msg, unavailable_label=t["unavailable"], has_events=False,
                show_title_bar=show_title_bar, title_text=title_text, weather_error=None)


# ------------------------------------------------------------------- translations
#
# Keyed by the 2-letter language code trmnl.user.locale resolves to (see _locale_of).
# Add a language by adding an entry here — everything else picks it up automatically.
_I18N = {
    "en": {
        "wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "months": ["January", "February", "March", "April", "May", "June", "July",
                   "August", "September", "October", "November", "December"],
        "unavailable": "Calendar unavailable",
    },
    "nl": {
        "wd": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
        "months": ["Januari", "Februari", "Maart", "April", "Mei", "Juni", "Juli",
                   "Augustus", "September", "Oktober", "November", "December"],
        "unavailable": "Kalender niet beschikbaar",
    },
    "fr": {
        "wd": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
        "months": ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
                   "Août", "Septembre", "Octobre", "Novembre", "Décembre"],
        "unavailable": "Agenda indisponible",
    },
    "de": {
        "wd": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "months": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                   "August", "September", "Oktober", "November", "Dezember"],
        "unavailable": "Kalender nicht verfügbar",
    },
    "es": {
        "wd": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        "months": ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
                   "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
        "unavailable": "Calendario no disponible",
    },
}
_DEFAULT_LOCALE = "en"


def _locale_of(input):
    """trmnl.user.locale is a real merge var (e.g. "en") — see usetrmnl/api-docs,
    plugin-marketplace/plugin-screen-generation-flow.md. Defensively handle a
    region-qualified form ("en-US") even though the documented example is bare."""
    try:
        loc = input["trmnl"]["user"]["locale"]
        if isinstance(loc, str) and loc.strip():
            code = loc.strip().lower().replace("_", "-").split("-")[0]
            if code in _I18N:
                return code
    except Exception:
        pass
    return _DEFAULT_LOCALE


def _user_tz(input):
    """Fall back to the device owner's own timezone (trmnl.user.time_zone_iana) when
    the plugin's own Time Zone field is left blank, instead of defaulting to UTC."""
    try:
        tz = input["trmnl"]["user"]["time_zone_iana"]
        return tz.strip() if isinstance(tz, str) and tz.strip() else None
    except Exception:
        return None


def _user_utc_offset(input):
    """trmnl.user.utc_offset (seconds from UTC) as a last-resort fallback for computing
    "now" — see _resolve_tz."""
    try:
        return int(input["trmnl"]["user"]["utc_offset"])
    except Exception:
        return None


def _resolve_tz(tzname, input):
    """An IANA name (from the Time Zone field or trmnl.user.time_zone_iana) is preferred
    since it's DST-aware for future recurring events, but ZoneInfo(name) raises
    ZoneInfoNotFoundError if the Serverless sandbox's tzdata is missing/incomplete for that
    name — silently falling back to UTC in that case mispositions everything computed from
    "now" (event bucketing, day boundaries, and the current-time line) by the zone's actual
    offset. trmnl.user.utc_offset is a raw number that needs no tzdata lookup at all, so it's
    a strictly more reliable fallback than defaulting straight to UTC."""
    tz = _safe_zone(tzname) if tzname else None
    if tz is not None:
        return tz
    offset_s = _user_utc_offset(input)
    if offset_s is not None:
        return timezone(timedelta(seconds=offset_s))
    return timezone.utc


def _day_label(d0, t):
    return "%s %s %s" % (t["wd"][d0.weekday()], d0.strftime("%-d"), t["months"][d0.month - 1])


def _fmt_time(dt, is_12h):
    if is_12h:
        h = dt.hour % 12 or 12
        return "%d:%02d %s" % (h, dt.minute, "AM" if dt.hour < 12 else "PM")
    return dt.strftime("%-H:%M")


# ------------------------------------------------------------------ ICS parsing

def _unfold(text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _prop(line):
    idx = line.find(":")
    if idx == -1:
        return None
    head, value = line[:idx], line[idx + 1:]
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def _untext(v):
    return (v.replace("\\n", "\n").replace("\\N", "\n")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")).strip()


def _parse_dt(value, params, tz):
    """Return (aware datetime, all_day bool)."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or (len(v) == 8 and "T" not in v):
        d = datetime.strptime(v[:8], "%Y%m%d")
        return d.replace(tzinfo=tz), True
    if v.endswith("Z"):
        return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    z = _safe_zone(params.get("TZID", "")) or tz
    return dt.replace(tzinfo=z), False


def _collect(text, tz, win_s, win_e, out, cal_idx=0):
    in_ev = False
    ev = {}
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            in_ev, ev = True, {}
            continue
        if line == "END:VEVENT":
            in_ev = False
            ev["cal_idx"] = cal_idx
            _expand(ev, tz, win_s, win_e, out)
            continue
        if not in_ev:
            continue
        parsed = _prop(line)
        if not parsed:
            continue
        name, params, value = parsed
        if name == "DTSTART":
            ev["start"], ev["all_day"] = _parse_dt(value, params, tz)
        elif name == "DTEND":
            ev["end"], _ = _parse_dt(value, params, tz)
        elif name == "SUMMARY":
            ev["title"] = _untext(value)
        elif name == "DESCRIPTION":
            ev["desc"] = _untext(value)
        elif name == "RRULE":
            ev["rrule"] = _parse_rrule(value, tz)
        elif name == "EXDATE":
            ev.setdefault("exdate", set())
            for part in value.split(","):
                try:
                    dt, _ = _parse_dt(part, params, tz)
                    ev["exdate"].add(dt.astimezone(tz).replace(microsecond=0))
                except Exception:
                    pass


def _parse_rrule(value, tz):
    rr = {}
    for token in value.split(";"):
        if "=" in token:
            k, v = token.split("=", 1)
            rr[k.upper()] = v
    if "UNTIL" in rr:
        try:
            u = rr["UNTIL"]
            if u.endswith("Z"):
                rr["_until"] = datetime.strptime(u, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            elif "T" in u:
                rr["_until"] = datetime.strptime(u[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tz)
            else:
                rr["_until"] = datetime.strptime(u[:8], "%Y%m%d").replace(tzinfo=tz)
        except Exception:
            rr["_until"] = None
    return rr


# ------------------------------------------------------------ recurrence expand

def _add_months(dt, n):
    m = dt.month - 1 + n
    y = dt.year + m // 12
    m = m % 12 + 1
    day = min(dt.day, [31, 29 if _leap(y) else 28, 31, 30, 31, 30,
                       31, 31, 30, 31, 30, 31][m - 1])
    return dt.replace(year=y, month=m, day=day)


def _leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _expand(ev, tz, win_s, win_e, out):
    start = ev.get("start")
    if not start:
        return
    all_day = ev.get("all_day", False)
    end = ev.get("end")
    if not end:
        end = start + timedelta(days=1) if all_day else start + timedelta(hours=1)
    dur = end - start
    title = ev.get("title", "(no title)")
    desc = ev.get("desc", "")
    exdate = ev.get("exdate", set())
    rr = ev.get("rrule")
    cal_idx = ev.get("cal_idx", 0)

    def emit(s):
        s = s.astimezone(tz)
        if s.replace(microsecond=0) in exdate:
            return
        e = s + dur
        if s < win_e and e > win_s:
            out.append({"start": s, "end": e, "all_day": all_day,
                        "title": title, "desc": desc, "cal_idx": cal_idx})

    if not rr or not rr.get("FREQ"):
        emit(start)
        return

    freq = rr["FREQ"]
    interval = max(1, int(rr.get("INTERVAL", "1") or "1"))
    count = int(rr["COUNT"]) if rr.get("COUNT") else None
    until = rr.get("_until")
    byday = None
    if rr.get("BYDAY"):
        byday = sorted(_WD[t[-2:]] for t in rr["BYDAY"].split(",") if t[-2:] in _WD)

    emitted = 0
    cur = start

    # Fast-forward so ancient DTSTARTs don't blow the iteration budget.
    if freq in ("DAILY", "WEEKLY") and not (freq == "WEEKLY" and byday):
        unit = 1 if freq == "DAILY" else 7
        gap = (win_s.date() - start.date()).days // unit
        if gap > 0:
            k = gap // interval
            if count is not None and k >= count:
                return
            emitted = k
            cur = start + timedelta(days=k * interval * unit)

    guard = 0
    while guard < 6000:
        guard += 1
        if count is not None and emitted >= count:
            return
        if until is not None and cur.astimezone(tz) > until.astimezone(tz):
            return

        if freq == "WEEKLY" and byday:
            base = cur.date()
            monday = base - timedelta(days=base.weekday())
            for wd in byday:
                day = monday + timedelta(days=wd)
                if day < start.date():
                    continue
                occ = cur.replace(year=day.year, month=day.month, day=day.day)
                if count is not None and emitted >= count:
                    return
                if until is not None and occ.astimezone(tz) > until.astimezone(tz):
                    return
                emitted += 1
                emit(occ)
        else:
            emitted += 1
            emit(cur)

        # Advance one cycle.
        if freq == "DAILY":
            cur = cur + timedelta(days=interval)
        elif freq == "WEEKLY":
            cur = cur + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            cur = _add_months(cur, interval)
        elif freq == "YEARLY":
            cur = _add_months(cur, 12 * interval)
        else:
            return

        if cur.astimezone(tz) > win_e and not (freq == "WEEKLY" and byday):
            return
        if freq == "WEEKLY" and byday and (cur - timedelta(days=cur.weekday())).astimezone(tz) > win_e:
            return


# ------------------------------------------------------------------- sun times
#
# Open-Meteo is free and needs no API key: geocode a place name to lat/lon, then ask
# its forecast endpoint for sunrise/sunset. Best-effort only — the calendar itself is
# the primary feature, so any failure here (bad location, network hiccup, timeout)
# just omits the sun marks instead of surfacing as a page-level error.

def _parse_latlon(raw):
    parts = raw.split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None


def _geocode(name):
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                      params={"name": name, "count": 1, "format": "json"},
                      timeout=3, headers={"User-Agent": "TRMNL-ICS-Calendar"})
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"]


# WMO weather codes (Open-Meteo's `weathercode`/`weather_code`), grouped into the categories
# worth flagging on the grid. Codes not listed (clear/cloudy) map to no overlay.
_WEATHER_CODES = {
    "fog": {45, 48},
    "rain": {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82},
    "snow": {71, 73, 75, 77, 85, 86},
    "storm": {95, 96, 99},
}


def _weather_kind(code):
    for kind, codes in _WEATHER_CODES.items():
        if code in codes:
            return kind
    return None


# TRMNL hosts a full weather-icon set — reused here from the same set the daily-weather
# plugin (elsewhere in this workspace) already uses. Priority order for picking ONE icon to
# represent a whole day: worst condition wins, defaulting to sunny when nothing's flagged.
_ICON_BASE = "https://trmnl.com/images/plugins/weather/"
_ICON_PRIORITY = ["storm", "snow", "rain", "fog"]
_ICON_FILE = {
    "storm": "wi-day-thunderstorm.svg", "snow": "wi-day-snow.svg",
    "rain": "wi-day-rain.svg", "fog": "wi-day-fog.svg", None: "wi-day-sunny.svg",
}


def _day_icon(hours):
    """One representative icon URL for a day, from its {hour: kind} weather map — the worst
    condition present anywhere in the day wins (a storm at 3pm matters more than clear
    mornings), defaulting to sunny when nothing significant is flagged."""
    present = set((hours or {}).values())
    kind = next((k for k in _ICON_PRIORITY if k in present), None)
    return _ICON_BASE + _ICON_FILE[kind]


def _fetch_sky(location, days_n, fahrenheit=False):
    """Sunrise/sunset + significant hourly weather + daily high/low, from a single Open-Meteo
    forecast call. `location` is either "lat,lon" or a place name (geocoded).
    Returns (sun_marks, hourly_weather, daily_temps, error):
      sun_marks:      {day_index: [{"hour", "kind"}, ...]}            kind: sunrise/sunset
      hourly_weather: {day_index: {hour_int: "rain"/"snow"/"storm"/"fog"}}
      daily_temps:    {day_index: {"high": float, "low": float}}      Celsius, or Fahrenheit
                                                                       if fahrenheit=True
      error:          None on success (including "no location configured"), else a short
                      diagnostic string — geocode failure, HTTP error, timeout, etc.
    Best-effort only — the calendar itself is the primary feature, so any failure here
    (bad location, network hiccup, timeout) just omits sun/weather/temps instead of surfacing
    as a page-level error. `error` is carried into the output as a debug-only field so a
    failure like this is diagnosable from the next poll's output instead of guesswork.

    Uses timezone="auto" so Open-Meteo derives the zone straight from the geocoded lat/lon,
    independent of whatever zone this plugin resolved for the calendar grid — that avoids
    tying sun-time accuracy to our own tzdata resolution (see _resolve_tz for why that can't
    always be trusted in the Serverless sandbox).

    Matched by POSITION, not by comparing date strings: requesting forecast_days=days_n
    guarantees Open-Meteo's daily.time[i] is "today+i" in whatever zone it resolved, and its
    hourly arrays are days_n*24 contiguous entries starting at day 0 hour 0 (verified against
    the live API). An earlier version matched by parsing/re-formatting each timestamp and
    comparing it to a locally-computed date string — any drift between how the two sides
    represented "today" (formatting, rounding, a request straddling a day boundary) silently
    dropped that day's data, which is what caused sunrise/sunset to sometimes go missing for
    today specifically while still showing for the other two days. Position can't drift."""
    location = (location or "").strip()
    if not location:
        return {}, {}, {}, None
    try:
        latlon = _parse_latlon(location) or _geocode(location)
        if not latlon:
            return {}, {}, {}, "could not geocode location %r" % location
        lat, lon = latlon
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min",
            "hourly": "weathercode", "timezone": "auto", "forecast_days": days_n,
        }
        if fahrenheit:
            params["temperature_unit"] = "fahrenheit"
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params,
                          timeout=3, headers={"User-Agent": "TRMNL-ICS-Calendar"})
        r.raise_for_status()
        body = r.json() or {}
        daily = body.get("daily") or {}
        sunrises = daily.get("sunrise") or []
        sunsets = daily.get("sunset") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        sun_marks = {}
        daily_temps = {}
        for i in range(min(days_n, len(sunrises), len(sunsets))):
            marks = []
            for arr, kind in ((sunrises, "sunrise"), (sunsets, "sunset")):
                dt = datetime.fromisoformat(arr[i])
                marks.append({"hour": dt.hour + dt.minute / 60.0, "kind": kind})
            sun_marks[i] = marks
        for i in range(min(days_n, len(highs), len(lows))):
            daily_temps[i] = {"high": round(highs[i]), "low": round(lows[i])}

        # Walk hourly entries in order, incrementing the day index whenever the date
        # actually changes — robust to a DST day being 23 or 25 hours instead of assuming
        # a fixed stride of 24, while still needing no locally-computed reference date.
        hourly = body.get("hourly") or {}
        h_times = hourly.get("time") or []
        codes = hourly.get("weathercode") or []
        hourly_weather = {}
        day_i, prev_date = -1, None
        for j, ts in enumerate(h_times):
            dt = datetime.fromisoformat(ts)
            if dt.date() != prev_date:
                day_i += 1
                prev_date = dt.date()
            if day_i >= days_n:
                break
            if j < len(codes):
                kind = _weather_kind(codes[j])
                if kind is not None:
                    hourly_weather.setdefault(day_i, {})[dt.hour] = kind

        return sun_marks, hourly_weather, daily_temps, None
    except Exception as exc:
        # Best-effort: never break the calendar over a weather hiccup. But silently
        # swallowing the reason made a real failure indistinguishable from "no location
        # configured" — return it so run() can surface it as a debug-only field (never a
        # page-level error) instead of leaving a future occurrence to guesswork.
        return {}, {}, {}, "%s: %s" % (type(exc).__name__, exc)


# ------------------------------------------------------------ native grid layout
#
# Builds percent-of-screen heights (h--[Ncqh]) for a real HTML/Liquid grid instead of
# drawing an image. cqh is a percentage of the outer .layout element (see shared.liquid),
# so every number here is a 0-100 integer share of the WHOLE screen — not pixels — which
# is what lets the same numbers render correctly on any device, including the larger
# TRMNL X. Liquid does no layout math itself; it just loops over this pre-baked structure.

HEADER_PCT = 15  # bumped from 11 to fit a second line (daily high/low) under the day label
ALLDAY_ROW_PCT = 6
TITLE_BAR_PCT = 6  # optional plugin-name bar at the very top, off by default (see run())
MIN_EVENT_PCT = 10  # floor so a block is never a literally invisible sliver — actual font
                     # sizing is handled client-side by the fit-text script (see shared.liquid),
                     # which measures the real rendered box and grows/shrinks text to match.
                     # Unlike every other *_pct value on this page, an event's own top_pct/
                     # height_pct (see _layout_native) are a share of grid_pct specifically
                     # (the day column's own height), not the whole screen — events are an
                     # absolutely-positioned overlay sized relative to their direct container.

# One hue per configured ICS URL (cycled if more calendars than hues). These are real
# framework chromatic classes (bg--{hue}-30) — on a grayscale panel they automatically
# fall back to distinct perceptually-appropriate gray shades (no manual gray mapping
# needed), and render as actual color on a chromatic panel.
_HUES = ["blue", "green", "orange", "purple", "red", "cyan", "pink", "lime", "violet", "yellow"]


def _hue(cal_idx):
    return _HUES[cal_idx % len(_HUES)]




def _cluster(events):
    """Group overlapping/touching timed events; assign side-by-side lanes within each.

    Returns a list of clusters: {"h0", "h1", "lanes": [(event, lane_index), ...], "nlanes"}.
    """
    clusters = []
    active = []   # (end_h, lane) for the cluster currently being built
    cur = None    # in-progress cluster dict

    def close():
        if cur is not None:
            cur["nlanes"] = max(l for _, l in cur["lanes"]) + 1
            clusters.append(cur)

    for ev in sorted(events, key=lambda e: e["h0"]):
        if cur is not None and ev["h0"] >= cur["h1"]:
            close()
            cur, active = None, []
        if cur is None:
            cur = {"h0": ev["h0"], "h1": ev["h1"], "lanes": []}
        active = [a for a in active if a[0] > ev["h0"]]
        used = {a[1] for a in active}
        lane = 0
        while lane in used:
            lane += 1
        active.append((ev["h1"], lane))
        cur["lanes"].append((ev, lane))
        cur["h1"] = max(cur["h1"], ev["h1"])
    close()
    return clusters


IMPORTANT_HOUR_WEIGHT = 4  # every hour in the configured day_start-day_end range gets this
                           # many times the vertical space of an hour outside it


def _layout_native(days, important_start, important_end, now_h=None, sun_marks=None,
                    hourly_weather=None, title_bar_pct=0):
    important_start = max(0, min(23, int(important_start)))
    important_end = max(important_start + 1, min(24, int(important_end)))

    max_ad_rows = max((len(d["allday"]) for d in days), default=0)
    allday_pct = min(3, max_ad_rows) * ALLDAY_ROW_PCT
    grid_base = HEADER_PCT + allday_pct + title_bar_pct
    grid_pct = 100 - grid_base

    # The full day (0-24) is always shown now — day_start/day_end used to be a hard crop
    # (hours outside just weren't rendered), but hiding the rest of the day entirely lost
    # context. Now they mark an "important" range that gets more vertical weight per hour,
    # while hours outside still show, just compressed.
    #
    # h--[Ncqh] is a bracket "arbitrary value" utility class, and — like the p--[Npx]
    # padding bracket-value bug found earlier this project — it turns out to only work for
    # INTEGERS: a decimal value (verified directly: h--[10.5cqh] and h--[5.1515cqh] both
    # silently no-op, the element falling back to its unstyled content-box height) generates
    # no CSS rule at all. So every hour in the important zone must share one INTEGER percent,
    # and every hour outside it another — but scaling an integer weight ratio essentially
    # never divides grid_pct evenly, so the leftover remainder has to land somewhere. Putting
    # it on the important hours would break the exact uniformity that's the whole point here;
    # instead spread it one-per-hour across the LEAST prominent (compressed, outside-range)
    # hours, where a handful being 1% taller than the rest is essentially invisible. Every
    # important hour ends up pixel-for-pixel identical; only the compressed hours have any
    # variance at all, and only ever by 1%.
    important_n = important_end - important_start
    outside_n = 24 - important_n
    total_units = IMPORTANT_HOUR_WEIGHT * important_n + outside_n

    # Floor (not round) each zone's ideal share — floors always sum to at most grid_pct,
    # never over, so the leftover ("deficit" below) is always >= 0 and only ever needs
    # handing OUT as +1s, never clawed back. Rounding instead could overshoot (verified: an
    # important_start=0/important_end=24 range — no outside hours at all to absorb anything
    # — rounded up to 4 per hour and totaled 96% against an 85% budget).
    important_base = int(IMPORTANT_HOUR_WEIGHT / total_units * grid_pct)
    outside_base = int(1 / total_units * grid_pct) if outside_n else 0
    deficit = grid_pct - (important_base * important_n + outside_base * outside_n)

    # Hand out the deficit as +1-per-hour, preferring outside (compressed, least prominent)
    # hours first so the important zone stays perfectly uniform whenever there's enough
    # outside hours to absorb it all; only overflows onto important hours if not (e.g. the
    # important range covers the whole day and there are no outside hours to use at all).
    bump_outside = min(deficit, outside_n)
    bump_important = deficit - bump_outside

    hour_pct = []
    outside_bumped = important_bumped = 0
    for h in range(24):
        if important_start <= h < important_end:
            extra = 1 if important_bumped < bump_important else 0
            important_bumped += extra
            hour_pct.append(important_base + extra)
        else:
            extra = 1 if outside_bumped < bump_outside else 0
            outside_bumped += extra
            hour_pct.append(outside_base + extra)

    cum_pct = [0]
    for p in hour_pct:
        cum_pct.append(cum_pct[-1] + p)

    def pct_at(t):
        # Cumulative position for fractional hours (events, sunrise/sunset, "now") — these
        # are positioned with plain CSS % on an inline style, not a bracket utility class,
        # so decimals are fine there; only the h--[Ncqh] rows above need integers.
        whole = int(t)
        frac = t - whole
        cum = cum_pct[whole] + (hour_pct[whole] * frac if whole < 24 else 0)
        return grid_base + cum

    # Bold the hour label wherever a timed event starts TODAY specifically, so the axis
    # doubles as a quick glance of "something happens around here" for the day that matters
    # most — bolding for every visible day made the axis mostly-bold on a busy week and lost
    # that at-a-glance signal.
    start_hours = {int(e["h0"]) for d in days if d["is_today"] for e in d["timed"] if 0 <= e["h0"] < 24}
    hour_rows = [{"hour": h, "pct": hour_pct[h], "shade": h % 2, "bold": h in start_hours,
                  "important": important_start <= h < important_end}
                 for h in range(24)]

    out_days = []
    for di, d in enumerate(days):
        clusters = [c for c in _cluster(d["timed"])
                    if max(c["h0"], 0) < min(c["h1"], 24)]
        for c in clusters:
            c["h0"] = max(c["h0"], 0)
            c["h1"] = min(c["h1"], 24)

        # Background bounds: window edges + every whole hour, ALWAYS — regardless of whether
        # an event happens to be running — so the zebra/night/weather background keeps its
        # normal per-hour texture underneath an event exactly like it would without one.
        # Events used to flatten this into one shade for their whole span (computed at a
        # single midpoint), which looked visibly wrong for anything spanning more than an
        # hour. Events are a separate absolutely-positioned overlay (below) painted on top,
        # so they still cover the background naturally without the background needing to
        # know about them.
        bounds = {0, 24}
        for h in range(1, 24):
            bounds.add(h)

        # Sunrise/sunset: a thin colored line (like "now") measured unreadable on the real
        # (grayscale) device — a 1-3px tinted sliver against an already-striped background
        # is too subtle to register. Shading the whole night portion of the column solid
        # dark is a large-area signal instead, so it survives e-ink rendering the same way
        # the hour zebra-stripe does. Still split bounds at the sunrise/sunset hour so the
        # transition lands at the right minute, not just the right hour band.
        #
        # Deliberately uses day 0's (today's) sunrise/sunset for EVERY column, not each day's
        # own — sunrise/sunset drifts by about a minute a day, and that real difference,
        # rounded to whole percentage points, made the night/day boundary land a few pixels
        # apart between adjacent columns. Three side-by-side columns with a visibly zigzagging
        # boundary line reads as a layout bug even though the underlying data is correct; a
        # single shared reference keeps it a straight, aligned line since nobody needs
        # per-column, to-the-minute precision here anyway.
        day_sun = (sun_marks or {}).get(0, [])
        sunrise_h = next((m["hour"] for m in day_sun if m["kind"] == "sunrise"), None)
        sunset_h = next((m["hour"] for m in day_sun if m["kind"] == "sunset"), None)
        for h in (sunrise_h, sunset_h):
            if h is not None and 0 <= h < 24:
                bounds.add(h)
        bounds = sorted(bounds)

        def is_night(mid):
            if sunrise_h is None or sunset_h is None:
                return False
            return mid < sunrise_h or mid >= sunset_h

        day_weather = (hourly_weather or {}).get(di, {})

        segments = []
        for a, b in zip(bounds, bounds[1:]):
            mid = (a + b) / 2.0
            # A segment never spans more than one hour (bounds already split at every whole
            # hour), so int(a) identifies which hour's budget it draws from. Telescope WITHIN
            # that hour — round(hour_pct[h] * local_b) - round(hour_pct[h] * local_a) — rather
            # than rounding pct_at(b) - pct_at(a) independently: rounding each fragment of a
            # split hour (sunrise/sunset falling inside it) on its own doesn't guarantee the
            # fragments sum back to that hour's already-fixed integer total (e.g. a 5%-hour
            # split 90/10 independently rounds to 5 and 1, summing to 6, one too many — or 4
            # and 0, one too few). Telescoping the local fraction guarantees an exact match:
            # at local_b=1 it reduces to exactly hour_pct[h], the same value hour_rows uses,
            # so a whole (unsplit) hour is also automatically identical to the axis row.
            h = int(a)
            pct = round(hour_pct[h] * (b - h)) - round(hour_pct[h] * (a - h))
            # Zebra-stripe by hour band instead of drawing a boundary line: five different
            # thin-line techniques (border utility, gray fill, black fill, 1px/2px, with/
            # without the JS overflow safety net) all measured correct server-side and
            # rendered fine in local headless-Chrome testing, but never once showed up on
            # the actual device — a real, unexplained divergence. A per-hour background
            # shade is a large-area fill instead of a 1-2px sliver, so it can't fail the same
            # way. Bounds already split at each whole hour, so a band never spans more than
            # one hour — int(a) (its start, floored) reliably identifies which band it
            # belongs to, even when a is fractional (e.g. right after sunrise mid-hour).
            shade = int(a) % 2
            segments.append({"pct": pct, "shade": shade,
                              "night": is_night(mid), "weather": day_weather.get(int(a))})

        # "Now" is an absolutely-positioned overlay too, for the same reason events are: it
        # used to be enforced to a minimum visible height by borrowing pct from whichever
        # OTHER background segment in today's column happened to be largest — which shrank
        # that segment relative to the same hour band on every other day (none of which have
        # a "now" mark, so none of them ever borrowed). Today's zebra bands would then be
        # sized slightly differently from both the hour axis and every other column even
        # though they cover the same hours — exactly the kind of "first column doesn't line
        # up" bug the event-overlay refactor already fixed once for events. Deriving its
        # position straight from pct_at(now_h), independent of the segment list, means
        # nothing about today's own background sizing ever has to change to show it.
        now_marker = None
        if d["is_today"] and now_h is not None and 0 <= now_h < 24:
            top = pct_at(now_h) - grid_base
            now_marker = {"top_pct": round(top / grid_pct * 100, 4), "night": is_night(now_h)}

        # Events are absolutely-positioned overlays, sized straight from pct_at() on each
        # cluster's own h0/h1 as a fraction of the GRID area's own height (0-100, relative to
        # grid_pct) — independent of the background's own segmentation, so an event's minimum-
        # size enforcement can never borrow space from a spacer and drag its rendered start
        # time away from its real hour (see git history for the alignment bug that caused).
        events = []
        sorted_clusters = sorted(clusters, key=lambda c: c["h0"])
        for idx, c in enumerate(sorted_clusters):
            top = pct_at(c["h0"]) - grid_base
            height = pct_at(c["h1"]) - grid_base - top
            if height < MIN_EVENT_PCT:
                # Grow downward to the minimum readable size, but don't run past the next
                # event if there isn't room — a slightly-undersized block reads better than
                # two events visually overlapping.
                next_top = (pct_at(sorted_clusters[idx + 1]["h0"]) - grid_base
                            if idx + 1 < len(sorted_clusters) else grid_pct)
                height = min(MIN_EVENT_PCT, max(0, next_top - top))
            lanes = [{"title": ev["title"], "hue": _hue(ev["cal_idx"])}
                     for ev, lane in sorted(c["lanes"], key=lambda p: p[1])]
            events.append({"top_pct": round(top / grid_pct * 100, 4),
                            "height_pct": round(height / grid_pct * 100, 4),
                            "lanes": lanes})

        out_days.append({
            "label": d["label"], "is_today": d["is_today"],
            "temp": d.get("temp"), "icon": d.get("icon"),
            "allday": d["allday"], "segments": segments, "events": events,
            "now_marker": now_marker,
        })

    return {
        "header_pct": HEADER_PCT, "allday_pct": allday_pct, "grid_pct": grid_pct,
        "title_bar_pct": title_bar_pct, "hour_rows": hour_rows, "days": out_days,
    }
