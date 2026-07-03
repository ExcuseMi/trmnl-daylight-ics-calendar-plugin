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

import requests
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = 3
MAX_EVENTS_PER_DAY = 12
_WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def run(input):
    urls = _urls(_cf(input, "ics_url"))
    t = _I18N[_locale_of(input)]
    tzname = _cf(input, "time_zone").strip() or _user_tz(input) or "UTC"
    is_12h = _cf(input, "time_format").strip().lower() == "12h"
    location = _cf(input, "location")
    start_h = _int(_cf(input, "day_start"), 8, 0, 23)
    end_h = _int(_cf(input, "day_end"), 22, 1, 24)
    if end_h <= start_h:
        start_h, end_h = 8, 22

    tz = _resolve_tz(tzname, input)

    if not urls:
        return _empty(tzname, tz, t, "No ICS URL configured")

    now = datetime.now(tz)
    win_s = now.replace(hour=0, minute=0, second=0, microsecond=0)
    win_e = win_s + timedelta(days=DAYS)

    occ = []
    errors = []
    for cal_idx, url in enumerate(urls):
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        try:
            resp = requests.get(url, timeout=4,
                                headers={"User-Agent": "TRMNL-Nextcloud-Calendar"})
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
    for i in range(DAYS):
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
            "allday": [{"title": a["title"], "hue": _hue(a["cal_idx"])} for a in allday[:3]],
        })

    # Fractional hour of "now" within today's column, e.g. 14:30 -> 14.5 — used to draw a
    # current-time marker. `win_s` (today's midnight) is always "now" with the clock zeroed,
    # so this is just the elapsed time since then.
    now_h = (now - win_s).total_seconds() / 3600.0
    sun, hourly_weather = _fetch_sky(location)

    # TEMP DEBUG — forcing weather flags to verify the hatch overlay actually renders on the
    # real device (current forecast is clear, so nothing would show otherwise). Remove after
    # confirming on-device.
    for h in range(int(start_h), int(end_h)):
        hourly_weather.setdefault(0, {})[h] = "rain" if h % 2 == 0 else "snow"

    grid = _layout_native(raw_days, start_h, end_h, now_h, sun, hourly_weather)

    return dict(grid, generated_at=int(now.timestamp()), tz=tzname, error=err,
                unavailable_label=t["unavailable"],
                has_events=any(d["timed"] or d["allday"] for d in raw_days))


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


def _safe_zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


def _empty(tzname, tz, t, msg):
    now = datetime.now(tz)
    win_s = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = []
    for i in range(DAYS):
        d0 = win_s + timedelta(days=i)
        days.append({
            "label": _day_label(d0, t),
            "is_today": i == 0, "timed": [], "allday": [],
        })
    grid = _layout_native(days, 8, 22, None)
    return dict(grid, generated_at=int(now.timestamp()), tz=tzname,
                error=msg, unavailable_label=t["unavailable"], has_events=False)


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
                      timeout=3, headers={"User-Agent": "TRMNL-Nextcloud-Calendar"})
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


def _fetch_sky(location):
    """Sunrise/sunset + significant hourly weather for the visible days, from a single
    Open-Meteo forecast call. `location` is either "lat,lon" or a place name (geocoded).
    Returns (sun_marks, hourly_weather):
      sun_marks:      {day_index: [{"hour", "kind"}, ...]}            kind: sunrise/sunset
      hourly_weather: {day_index: {hour_int: "rain"/"snow"/"storm"/"fog"}}
    Best-effort only — the calendar itself is the primary feature, so any failure here
    (bad location, network hiccup, timeout) just omits sun/weather instead of surfacing
    as a page-level error.

    Uses timezone="auto" so Open-Meteo derives the zone straight from the geocoded lat/lon,
    independent of whatever zone this plugin resolved for the calendar grid — that avoids
    tying sun-time accuracy to our own tzdata resolution (see _resolve_tz for why that can't
    always be trusted in the Serverless sandbox).

    Matched by POSITION, not by comparing date strings: requesting forecast_days=DAYS
    guarantees Open-Meteo's daily.time[i] is "today+i" in whatever zone it resolved, and its
    hourly arrays are DAYS*24 contiguous entries starting at day 0 hour 0 (verified against
    the live API). An earlier version matched by parsing/re-formatting each timestamp and
    comparing it to a locally-computed date string — any drift between how the two sides
    represented "today" (formatting, rounding, a request straddling a day boundary) silently
    dropped that day's data, which is what caused sunrise/sunset to sometimes go missing for
    today specifically while still showing for the other two days. Position can't drift."""
    location = (location or "").strip()
    if not location:
        return {}, {}
    try:
        latlon = _parse_latlon(location) or _geocode(location)
        if not latlon:
            return {}, {}
        lat, lon = latlon
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "daily": "sunrise,sunset",
            "hourly": "weathercode", "timezone": "auto", "forecast_days": DAYS,
        }, timeout=3, headers={"User-Agent": "TRMNL-Nextcloud-Calendar"})
        r.raise_for_status()
        body = r.json() or {}
        daily = body.get("daily") or {}
        sunrises = daily.get("sunrise") or []
        sunsets = daily.get("sunset") or []
        sun_marks = {}
        for i in range(min(DAYS, len(sunrises), len(sunsets))):
            marks = []
            for arr, kind in ((sunrises, "sunrise"), (sunsets, "sunset")):
                dt = datetime.fromisoformat(arr[i])
                marks.append({"hour": dt.hour + dt.minute / 60.0, "kind": kind})
            sun_marks[i] = marks

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
            if day_i >= DAYS:
                break
            if j < len(codes):
                kind = _weather_kind(codes[j])
                if kind is not None:
                    hourly_weather.setdefault(day_i, {})[dt.hour] = kind

        return sun_marks, hourly_weather
    except Exception:
        return {}, {}


# ------------------------------------------------------------ native grid layout
#
# Builds percent-of-screen heights (h--[Ncqh]) for a real HTML/Liquid grid instead of
# drawing an image. cqh is a percentage of the outer .layout element (see shared.liquid),
# so every number here is a 0-100 integer share of the WHOLE screen — not pixels — which
# is what lets the same numbers render correctly on any device, including the larger
# TRMNL X. Liquid does no layout math itself; it just loops over this pre-baked structure.

HEADER_PCT = 11
ALLDAY_ROW_PCT = 9
MIN_EVENT_PCT = 10  # floor so a block is never a literally invisible sliver — actual font
                     # sizing is handled client-side by the fit-text script (see shared.liquid),
                     # which measures the real rendered box and grows/shrinks text to match

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


def _enforce_min_event_pct(segments):
    """Bump undersized event blocks up to MIN_EVENT_PCT, borrowing from the largest
    spacers so the day column's total pct is unchanged (telescoping sum stays exact)."""
    deficit = 0
    for seg in segments:
        if seg["type"] == "event" and seg["pct"] < MIN_EVENT_PCT:
            deficit += MIN_EVENT_PCT - seg["pct"]
            seg["pct"] = MIN_EVENT_PCT
    if deficit <= 0:
        return
    spacers = sorted((s for s in segments if s["type"] == "spacer"),
                      key=lambda s: -s["pct"])
    for sp in spacers:
        if deficit <= 0:
            break
        take = min(sp["pct"], deficit)
        sp["pct"] -= take
        deficit -= take


MIN_MARK_PCT = 2  # a mark landing right at the window edge (e.g. sunset a minute before
                   # max_h) can produce a 0%-height sliver that just doesn't render — same
                   # class of bug as undersized events, same borrow-from-largest-spacer fix.


def _enforce_min_mark_pct(segments):
    deficit = 0
    for seg in segments:
        if seg["type"] == "spacer" and seg.get("marks") and seg["pct"] < MIN_MARK_PCT:
            deficit += MIN_MARK_PCT - seg["pct"]
            seg["pct"] = MIN_MARK_PCT
    if deficit <= 0:
        return
    donors = sorted((s for s in segments if s["type"] == "spacer" and not s.get("marks")),
                     key=lambda s: -s["pct"])
    for sp in donors:
        if deficit <= 0:
            break
        take = min(sp["pct"], deficit)
        sp["pct"] -= take
        deficit -= take


def _layout_native(days, min_h, max_h, now_h=None, sun_marks=None, hourly_weather=None):
    min_h = max(0, min(23, int(min_h)))
    max_h = max(min_h + 1, min(24, int(max_h)))

    max_ad_rows = max((len(d["allday"]) for d in days), default=0)
    allday_pct = min(3, max_ad_rows) * ALLDAY_ROW_PCT
    grid_base = HEADER_PCT + allday_pct
    grid_pct = 100 - grid_base

    def pct_at(t):
        return round(grid_base + (t - min_h) / (max_h - min_h) * grid_pct)

    # Hour axis rows (shared reference scale — day columns snap their spacer
    # boundaries to these same hour marks so the gridlines line up).
    hour_bounds = [pct_at(h) for h in range(min_h, max_h + 1)]
    # Bold the hour label wherever a timed event starts (in any of the visible days), so the
    # axis doubles as a quick glance of "something happens around here" independent of color.
    start_hours = {int(e["h0"]) for d in days for e in d["timed"] if min_h <= e["h0"] < max_h}
    hour_rows = [{"hour": h, "pct": hour_bounds[i + 1] - hour_bounds[i], "shade": h % 2,
                  "bold": h in start_hours}
                 for i, h in enumerate(range(min_h, max_h))]

    out_days = []
    for di, d in enumerate(days):
        clusters = [c for c in _cluster(d["timed"])
                    if max(c["h0"], min_h) < min(c["h1"], max_h)]
        for c in clusters:
            c["h0"] = max(c["h0"], min_h)
            c["h1"] = min(c["h1"], max_h)

        # Boundaries: window edges + cluster edges + hour marks that fall in a gap
        # (not inside any cluster, so events stay one unbroken block).
        bounds = {min_h, max_h}
        for c in clusters:
            bounds.add(c["h0"])
            bounds.add(c["h1"])
        for h in range(min_h + 1, max_h):
            if not any(c["h0"] < h < c["h1"] for c in clusters):
                bounds.add(h)

        # Current-time marker: today-only, only within the visible window, and only where
        # it doesn't fall inside an ongoing event (event blocks stay solid/uncut, same rule
        # as hour marks above).
        marks = []
        if (d["is_today"] and now_h is not None and min_h <= now_h < max_h
                and not any(c["h0"] < now_h < c["h1"] for c in clusters)):
            marks.append({"hour": now_h, "kind": "now"})
        for m in marks:
            bounds.add(m["hour"])

        # Sunrise/sunset: a thin colored line (like "now") measured unreadable on the real
        # (grayscale) device — a 1-3px tinted sliver against an already-striped background
        # is too subtle to register. Shading the whole night portion of the column solid
        # dark is a large-area signal instead, so it survives e-ink rendering the same way
        # the hour zebra-stripe does. Still split bounds at the sunrise/sunset hour so the
        # transition lands at the right minute, not just the right hour band.
        day_sun = (sun_marks or {}).get(di, [])
        sunrise_h = next((m["hour"] for m in day_sun if m["kind"] == "sunrise"), None)
        sunset_h = next((m["hour"] for m in day_sun if m["kind"] == "sunset"), None)
        for h in (sunrise_h, sunset_h):
            if h is not None and min_h <= h < max_h and not any(c["h0"] < h < c["h1"] for c in clusters):
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
            cl = next((c for c in clusters if c["h0"] <= mid < c["h1"]), None)
            pct = pct_at(b) - pct_at(a)
            if cl is None:
                # Zebra-stripe by hour band instead of drawing a boundary line: five
                # different thin-line techniques (border utility, gray fill, black fill,
                # 1px/2px, with/without the JS overflow safety net) all measured correct
                # server-side and rendered fine in local headless-Chrome testing, but never
                # once showed up on the actual device — a real, unexplained divergence.
                # A per-hour background shade is a large-area fill instead of a 1-2px sliver,
                # so it can't fail the same way. Bounds already split every plain gap at each
                # whole hour, so a spacer never spans more than one hour band — int(a) (its
                # start, floored) reliably identifies which band it belongs to, even when a
                # is fractional (e.g. a spacer starting right after an event ends mid-hour).
                shade = int(a) % 2
                seg_marks = [m for m in marks if m["hour"] == a]
                segments.append({"type": "spacer", "pct": pct, "shade": shade, "marks": seg_marks,
                                  "night": is_night(mid), "weather": day_weather.get(int(a))})
                continue
            lanes = []
            for ev, lane in sorted(cl["lanes"], key=lambda p: p[1]):
                lanes.append({
                    "title": ev["title"], "hue": _hue(ev["cal_idx"]),
                })
            segments.append({"type": "event", "pct": pct, "lanes": lanes})

        _enforce_min_event_pct(segments)
        _enforce_min_mark_pct(segments)
        out_days.append({
            "label": d["label"], "is_today": d["is_today"],
            "allday": d["allday"], "segments": segments,
        })

    return {
        "header_pct": HEADER_PCT, "allday_pct": allday_pct, "grid_pct": grid_pct,
        "hour_rows": hour_rows, "days": out_days,
    }
