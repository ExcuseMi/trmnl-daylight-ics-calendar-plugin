# TRMNL: Daylight & Weather Calendar

A [TRMNL](https://usetrmnl.com) private plugin that shows 1 day to a week of any ICS calendar
feed as a time-grid, with sunrise/sunset and daily weather on the timeline. The grid auto-scales
so the hours that matter (daylight and your meetings) get more room and quiet hours shrink out
of the way, with no fixed "business hours" window to configure.

Runs entirely on TRMNL **[Serverless](https://help.trmnl.com/en/articles/14130649-serverless)**,
no server to host, no middleman service. `plugin/src/transform.py`'s `run()` fetches the ICS
link(s), expands recurring events for the window, and returns a pre-computed native layout
(percent-of-screen heights) to the Liquid template.

## What it shows

- 1 to 7 day columns (your choice), drawn as a real hour-grid (not an image), with daylight and
  meeting hours automatically given more vertical space than the quiet hours around them.
- All-day events as chips, timed events as blocks sized by duration, overlapping events split
  into side-by-side lanes.
- A red line for the current time, plus orange/purple lines for sunrise/sunset when a location
  is configured, and a daily weather icon + high/low.
- One color per configured calendar (cycled if you add more than the palette covers), so you can
  combine as many ICS feeds as you like and still tell them apart at a glance.
- Recurring events (`DAILY` / `WEEKLY` incl. `BYDAY` / `MONTHLY` / `YEARLY`, with `INTERVAL`,
  `COUNT`, `UNTIL`, `EXDATE`) expanded into the window.
- Language (day/month names) auto-detected from your TRMNL account locale, with 24h/12h time
  format as a setting.
- Graceful states: an `error` banner if every feed fails to fetch.

## Setup

1. In TRMNL: **Plugins → Private Plugins → New**, name it, **Save**.
2. Push this repo with `trmnlp push` (see below); it uploads `settings.yml`, the `.liquid`
   templates, and `transform.py` in one go.
3. Fill in the plugin's custom fields:
   - **Calendar ICS URLs**: one per line to combine several calendars, each in its own color.
     Any ICS source works, including Nextcloud, Google Calendar, Outlook, and Apple Calendar,
     all of which have a private/secret ICS link tucked away in their calendar settings.
     `webcal://` links are handled automatically.
   - **Time Zone**: leave blank to use your TRMNL account's own time zone, or set one explicitly.
   - **Time Format**: 24-hour or 12-hour (AM/PM).
   - **Location**: a city name or `lat,lon`, for sunrise/sunset and daily weather. Leave blank
     to hide sun times and weather, and emphasize hours by meetings alone.
   - **Temperature Unit**: Celsius or Fahrenheit (requires Location above).
   - **Days to Show**: 1, 2, 3, 5 days, or a full week.

## Local layout development

`run()` doesn't execute locally, but you can iterate on the Liquid with mock data:

```bash
cd plugin
trmnlp serve      # http://127.0.0.1:4567
trmnlp build      # writes static HTML to _build/
trmnlp push       # uploads settings.yml + src/* to the TRMNL plugin
```

Mock data lives in `plugin/.trmnlp.yml` and mirrors the shape `_layout_native()` returns;
regenerate it by calling `transform.py`'s internals directly with representative input rather
than hand-editing the percentages.

## Files

| Path | Purpose |
|------|---------|
| `plugin/src/transform.py` | Serverless code: fetch ICS, expand recurrences, compute layout, geocode + fetch sun times |
| `plugin/src/shared.liquid` | The `main` template for all four view sizes (`full`/`half_*`/`quadrant`) |
| `plugin/src/settings.yml` | Custom fields (ICS URLs, time zone, time format, location, days to show) |
| `plugin/.trmnlp.yml` | Local mock data for `trmnlp serve` |

## Notes & limits

- The Serverless VM allows **128 MB / 5 s**; parsing is pure standard library + `requests`
  (no `icalendar` dependency) and bounded to the configured day window. Sunrise/sunset and
  weather lookups (Open-Meteo) are best-effort with short timeouts; a slow/failed lookup just
  omits sun times and weather rather than breaking the calendar.
- Modified single instances of a recurring series (`RECURRENCE-ID` overrides) and `VTIMEZONE`
  definitions with non-IANA `TZID`s are not fully resolved; standard IANA zone names work.
