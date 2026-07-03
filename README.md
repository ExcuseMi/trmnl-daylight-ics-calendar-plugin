# TRMNL — Daylight ICS Calendar (3-day view)

A [TRMNL](https://usetrmnl.com) private plugin that shows the **next 3 days** of one or more
ICS calendar feeds (Nextcloud or any other) as a time-grid, with sunrise/sunset lines on the
timeline.

Runs entirely on TRMNL **[Serverless](https://help.trmnl.com/en/articles/14130649-serverless)** —
no server to host, no middleman service. `plugin/src/transform.py`'s `run()` fetches the ICS
link(s), expands recurring events for the window, and returns a pre-computed native layout
(percent-of-screen heights) to the Liquid template.

## What it shows

- Three day columns: today + the next two days, drawn as a real hour-grid (not an image).
- All-day events as chips, timed events as blocks sized by duration, overlapping events split
  into side-by-side lanes.
- A red line for the current time, plus orange/purple lines for sunrise/sunset when a location
  is configured.
- One color per configured calendar (cycled if you add more than the palette covers).
- Recurring events (`DAILY` / `WEEKLY` incl. `BYDAY` / `MONTHLY` / `YEARLY`, with `INTERVAL`,
  `COUNT`, `UNTIL`, `EXDATE`) expanded into the window.
- Language (day/month names) auto-detected from your TRMNL account locale, with 24h/12h time
  format as a setting.
- Graceful states: an `error` banner if every feed fails to fetch.

## Setup

1. In TRMNL: **Plugins → Private Plugins → New**, name it, **Save**.
2. Push this repo with `trmnlp push` (see below) — it uploads `settings.yml`, the `.liquid`
   templates, and `transform.py` in one go.
3. Fill in the plugin's custom fields:
   - **Calendar ICS URLs** — one per line to combine several calendars. In Nextcloud:
     *Calendar → hover a calendar → ⋯ → Copy private link* (ends in `?export`). `webcal://`
     links are handled automatically.
   - **Time Zone** — leave blank to use your TRMNL account's own time zone, or set one explicitly.
   - **Time Format** — 24-hour or 12-hour (AM/PM).
   - **Location** — a city name or `lat,lon`, for sunrise/sunset. Leave blank to hide sun times.
   - **Grid starts/ends at** — the visible hour range.

## Local layout development

`run()` doesn't execute locally, but you can iterate on the Liquid with mock data:

```bash
cd plugin
trmnlp serve      # http://127.0.0.1:4567
trmnlp build      # writes static HTML to _build/
trmnlp push       # uploads settings.yml + src/* to the TRMNL plugin
```

Mock data lives in `plugin/.trmnlp.yml` and mirrors the shape `_layout_native()` returns —
regenerate it by calling `transform.py`'s internals directly with representative input rather
than hand-editing the percentages.

## Files

| Path | Purpose |
|------|---------|
| `plugin/src/transform.py` | Serverless code — fetch ICS, expand recurrences, compute layout, geocode + fetch sun times |
| `plugin/src/shared.liquid` | The `main` template for all four view sizes (`full`/`half_*`/`quadrant`) |
| `plugin/src/settings.yml` | Custom fields (ICS URLs, time zone, time format, location, grid hours) |
| `plugin/.trmnlp.yml` | Local mock data for `trmnlp serve` |

## Notes & limits

- The Serverless VM allows **128 MB / 5 s** — parsing is pure standard library + `requests`
  (no `icalendar` dependency) and bounded to the 3-day window. Sunrise/sunset lookups
  (Open-Meteo) are best-effort with short timeouts; a slow/failed lookup just omits the sun
  lines rather than breaking the calendar.
- Modified single instances of a recurring series (`RECURRENCE-ID` overrides) and `VTIMEZONE`
  definitions with non-IANA `TZID`s are not fully resolved; standard IANA zone names work.
