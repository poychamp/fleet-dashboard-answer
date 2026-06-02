# Fleet Dashboard

A single Python script that reads `fleet_status.csv` and produces one self-contained
`fleet_dashboard.html` a fleet manager can open in any browser with no setup.

## Run

```
python fleet_dashboard.py
```

- Needs `fleet_status.csv` sitting next to the script (the script finds both files relative to itself, so the working directory does not matter).
- Python 3, standard library only. No installs.
- Writes `fleet_dashboard.html` in the same folder. Runs in well under a second.

## What it follows

- Python standard library only (`csv`, `datetime`, `html`). No pandas, folium, or requests.
- One script, one output file.
- The HTML is fully self-contained. The map is an inline SVG and the zoom/pan is plain inline JavaScript, so there are no external files, no CDN, and no tile server.

## What is in the dashboard

- **Map** - an inline SVG of Australia with every device plotted at its GPS location, colour-coded by status. Zoom and pan to read dense areas.
- **Device list** - status, battery, how long ago each device was last seen, location, and any data problems.
- **Summary** - a count per status, including an unknown bucket.
- **Data issues** - a panel that lists every row with a problem, so nothing is silently dropped.
- **Linked map and table** - click a dot to jump to its table row, or click a row to drop a red pin on its map location. Either way the matching circle pops to the front so you can tell exactly which device you picked.

## My Approach

### How I used AI

I built this with Claude Code and worked in steps rather than asking for everything at once.

First I had it read the challenge README and then the CSV before writing any code. That early analysis shows the file has 35 rows, not 30, and 5 of them are deliberately broken. There is a row missing its name, battery, and coordinates, a `maintenance` status that is not in the documented set, a battery of 150, a latitude of `not_a_lat`, and a row with a -5 battery and a `last_seen` dated in 2027. Knowing that up front shaped the whole design around validating every field instead of trusting the data. So any row with missing or invalid coordinates is kept off the map and shown in the Data issues panel instead.

Then I had it write the script. The first working version came together in a few minutes. I read through the code to make sure I understood it and that the validation did what I wanted. Claude 4.8 Ultrathink is amazing. I had planned to set the typography and colour palette myself, but the styling in that first version was already clean, so I kept it.

After that I iterated on the UI a piece at a time. I made the device table readable, because the Notes column was stretching the layout, so I fixed its width. I added zoom and pan controls to the map. Then I linked the table and the map both ways with pin markers.

The one real bug I worked through: clicking a row in a dense area dropped the marker on the wrong device. Several trackers sit at almost the same coordinates (Perth CBD has 5, greater Sydney has around 15), so a click in Sydney highlighted the violet "unknown" dot instead of the idle one I actually picked. The fix was to bring the selected device's circle to the front and enlarge it, so the correct one is always on top of the pile.

I also checked the browser console on the rendered page to make sure there are no JavaScript errors.