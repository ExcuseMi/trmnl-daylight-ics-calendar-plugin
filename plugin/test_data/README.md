# Demo / test ICS feeds

Three synthetic calendars for exercising the plugin end-to-end (real fetch + parse +
recurrence expansion + layout), not just the static Liquid mock in `../.trmnlp.yml`.
Dates are anchored around 2026-07 so the near-term one-off events land inside a
default (3-day) view when tested around that date; the recurring events (`RRULE`)
stay useful indefinitely.

| File | Covers |
|------|--------|
| `work.ics` | `WEEKLY` (all weekdays and a single weekday), `MONTHLY`, `EXDATE` cancelling one instance, a multi-day all-day event, two same-calendar overlapping events (lane split) |
| `personal.ics` | Early-morning and late-night events (important-range expansion), a one-off appointment, a `YEARLY` all-day birthday |
| `family.ics` | Another weekday `WEEKLY` series, a single-day all-day holiday, a multi-day all-day event near the edge of a week-long view, and an event overlapping `personal.ics`'s Piano Lesson across *different* calendars (cross-calendar lane split + distinct hues) |

Combine all three as the plugin's **Calendar ICS URLs** (one per line) to see three
colors at once, or use just one to test a single feed in isolation.

`transform.py` only runs against a real HTTP(S) URL, not a local file path, so serve
this directory and point the plugin at it:

```bash
cd plugin/test_data
python3 -m http.server 8420
```

Then, from a machine that can reach that server (e.g. via a tunnel like `ngrok` or
`cloudflared` if testing against the real hosted TRMNL plugin), set the ICS URLs to:

```
http://<host>:8420/work.ics
http://<host>:8420/personal.ics
http://<host>:8420/family.ics
```

To sanity-check the ICS syntax and recurrence expansion without any of that, call the
parser directly:

```bash
cd plugin
python3 -c "
import sys; sys.path.insert(0, 'src')
import transform as T
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
tz = ZoneInfo('Europe/Brussels')
win_s = datetime(2026, 7, 4, tzinfo=tz)
win_e = win_s + timedelta(days=7)
occ = []
for i, f in enumerate(['test_data/work.ics', 'test_data/personal.ics', 'test_data/family.ics']):
    T._collect(open(f).read(), tz, win_s, win_e, occ, i)
for e in sorted(occ, key=lambda e: e['start']):
    print(e['start'], '->', e['end'], e['title'])
"
```
