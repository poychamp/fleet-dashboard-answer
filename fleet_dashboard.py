"""Fleet Dashboard generator for the SolidGPS challenge.

Reads ``fleet_status.csv`` and writes a single, self-contained
``fleet_dashboard.html`` a fleet manager can open in any browser with no setup.

Design notes:
- Python standard library only (csv, datetime, html). No pandas/folium/requests.
- The map is an inline SVG of Australia drawn in pure Python: device GPS
  coordinates are projected onto a simplified coastline outline. No tile server,
  no CDN, no JavaScript, so the output is genuinely self-contained.
- The CSV is deliberately dirty (missing fields, an unknown status, an
  impossible battery, a non-numeric latitude, a future timestamp). Every row is
  validated; bad values are flagged in a "Data issues" panel instead of crashing
  the script or silently corrupting the dashboard.
"""

import csv
import html
import os
from datetime import datetime

# --- Configuration -----------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
INPUT_CSV = os.path.join(HERE, "fleet_status.csv")
OUTPUT_HTML = os.path.join(HERE, "fleet_dashboard.html")

# Known statuses -> (display label, colour). Anything else is treated as
# "unknown" so a typo or a new status code shows up loudly rather than vanishing.
STATUS_META = {
    "active": ("Active", "#16a34a"),       # green  - working normally
    "idle": ("Idle", "#f59e0b"),           # amber  - on but not moving
    "offline": ("Offline", "#6b7280"),     # grey   - no signal
    "low_battery": ("Low Battery", "#dc2626"),  # red - needs attention
}
UNKNOWN_LABEL = "Unknown"
UNKNOWN_COLOUR = "#9333ea"  # purple - status we did not expect / invalid row

# Australia sits roughly inside this lat/lon box; valid coordinates outside it
# are rejected (e.g. swapped or garbage numbers) so they never skew the map.
LAT_RANGE = (-60.0, 0.0)
LON_RANGE = (100.0, 160.0)

# Projection bounding box (a little wider than the mainland for breathing room).
BBOX = {"lon_min": 112.0, "lon_max": 155.0, "lat_min": -44.0, "lat_max": -9.5}
MAP_W, MAP_H, MARGIN = 900, 720, 24

# Simplified Australia outline (lon, lat) — coarse but recognisable, drawn with
# the same projection as the device points so everything lines up.
MAINLAND = [
    (142.8, -10.7), (141.5, -13.5), (140.8, -17.5), (139.0, -17.3),
    (137.9, -16.0), (135.8, -14.9), (136.5, -12.2), (132.6, -11.3),
    (130.6, -12.4), (129.0, -14.8), (127.0, -13.9), (124.5, -16.4),
    (122.2, -18.1), (121.0, -19.6), (119.0, -20.4), (114.9, -21.8),
    (113.4, -26.1), (114.6, -28.8), (115.0, -33.3), (118.0, -34.9),
    (123.6, -33.9), (129.0, -31.6), (132.3, -31.9), (134.2, -32.6),
    (135.9, -34.9), (137.8, -35.2), (139.8, -37.5), (143.5, -38.9),
    (146.4, -38.9), (148.5, -37.5), (150.0, -37.6), (151.6, -33.0),
    (153.6, -28.7), (153.1, -25.3), (149.9, -22.4), (148.5, -20.1),
    (146.3, -18.9), (145.5, -16.3), (144.5, -14.3), (143.6, -12.5),
    (142.8, -10.7),
]
TASMANIA = [
    (144.7, -40.7), (146.6, -41.2), (148.3, -40.9), (148.3, -42.9),
    (147.0, -43.6), (145.5, -43.2), (145.0, -42.2), (144.7, -40.7),
]


# --- Parsing & validation ----------------------------------------------------

class Device:
    """One CSV row, parsed and validated. ``issues`` holds human-readable
    problems; an empty list means the row was clean."""

    def __init__(self, device_id):
        self.device_id = device_id or "(no id)"
        self.name = ""
        self.raw_status = ""
        self.status_key = None      # a key in STATUS_META, or None when unknown
        self.battery = None         # int 0-100, or None when missing/invalid
        self.lat = None
        self.lon = None
        self.last_seen = None       # datetime, or None when unparseable
        self.location = ""
        self.issues = []

    @property
    def status_label(self):
        if self.status_key:
            return STATUS_META[self.status_key][0]
        return UNKNOWN_LABEL

    @property
    def status_colour(self):
        if self.status_key:
            return STATUS_META[self.status_key][1]
        return UNKNOWN_COLOUR

    @property
    def mappable(self):
        return self.lat is not None and self.lon is not None


def _parse_battery(raw, device):
    raw = (raw or "").strip()
    if raw == "":
        device.issues.append("battery missing")
        return None
    try:
        value = int(float(raw))
    except ValueError:
        device.issues.append("battery not a number (%s)" % raw)
        return None
    if value < 0 or value > 100:
        device.issues.append("battery out of range (%s)" % raw)
        return None
    return value


def _parse_coords(raw_lat, raw_lon, device):
    raw_lat, raw_lon = (raw_lat or "").strip(), (raw_lon or "").strip()
    if raw_lat == "" or raw_lon == "":
        device.issues.append("missing coordinates (not shown on map)")
        return None, None
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except ValueError:
        device.issues.append("invalid coordinates (not shown on map)")
        return None, None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        device.issues.append("coordinates outside Australia (not shown on map)")
        return None, None
    return lat, lon


def _parse_last_seen(raw, device, now):
    raw = (raw or "").strip()
    if raw == "":
        device.issues.append("last_seen missing")
        return None
    try:
        seen = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        device.issues.append("last_seen unparseable (%s)" % raw)
        return None
    if seen > now:
        device.issues.append("last_seen is in the future (%s)" % raw)
    return seen


def parse_devices(path, now):
    """Read the CSV and return a list of validated Device objects."""
    devices = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            device = Device((row.get("device_id") or "").strip())

            device.name = (row.get("name") or "").strip()
            if not device.name:
                device.issues.append("name missing")

            device.raw_status = (row.get("status") or "").strip()
            key = device.raw_status.lower()
            if key in STATUS_META:
                device.status_key = key
            else:
                label = device.raw_status or "(blank)"
                device.issues.append("unknown status (%s)" % label)

            device.location = (row.get("location") or "").strip()
            device.battery = _parse_battery(row.get("battery_pct"), device)
            device.lat, device.lon = _parse_coords(row.get("lat"), row.get("lon"), device)
            device.last_seen = _parse_last_seen(row.get("last_seen"), device, now)
            devices.append(device)
    return devices


# --- Helpers -----------------------------------------------------------------

def project(lon, lat):
    """Map a lon/lat pair to (x, y) inside the SVG viewport."""
    fx = (lon - BBOX["lon_min"]) / (BBOX["lon_max"] - BBOX["lon_min"])
    fy = (BBOX["lat_max"] - lat) / (BBOX["lat_max"] - BBOX["lat_min"])
    x = MARGIN + fx * (MAP_W - 2 * MARGIN)
    y = MARGIN + fy * (MAP_H - 2 * MARGIN)
    return round(x, 1), round(y, 1)


def humanize_ago(seen, now):
    """Render how long ago a timestamp was, e.g. '3h ago' / '12d ago'."""
    if seen is None:
        return "unknown"
    delta = now - seen
    secs = delta.total_seconds()
    if secs < 0:
        return "in the future"
    mins = secs / 60
    if mins < 60:
        return "%dm ago" % int(mins)
    hours = mins / 60
    if hours < 24:
        return "%dh ago" % int(hours)
    return "%dd ago" % int(hours / 24)


def battery_cell(pct):
    if pct is None:
        return '<span class="muted">&mdash;</span>'
    colour = "#16a34a" if pct > 50 else "#f59e0b" if pct >= 20 else "#dc2626"
    return (
        '<div class="bat"><div class="bat-track">'
        '<div class="bat-fill" style="width:%d%%;background:%s"></div></div>'
        '<span class="bat-pct">%d%%</span></div>' % (pct, colour, pct)
    )


def esc(text):
    return html.escape(str(text), quote=True)


# --- HTML sections -----------------------------------------------------------

def build_svg(devices):
    parts = ['<svg id="fleetmap" viewBox="0 0 %d %d" class="map" role="img" '
             'aria-label="Map of fleet devices across Australia">' % (MAP_W, MAP_H)]

    for outline in (MAINLAND, TASMANIA):
        pts = " ".join("%s,%s" % project(lon, lat) for lon, lat in outline)
        parts.append('<polygon points="%s" class="land"/>' % pts)

    # Plot points last so they sit on top of the land.
    for d in devices:
        if not d.mappable:
            continue
        x, y = project(d.lon, d.lat)
        tip = "%s (%s) - %s - %s - battery %s - %s" % (
            d.device_id, d.name or "no name", d.status_label, d.location or "no location",
            ("%d%%" % d.battery) if d.battery is not None else "n/a",
            humanize_ago(d.last_seen, NOW),
        )
        flag = ' map-flag' if d.issues else ''
        parts.append(
            '<circle cx="%s" cy="%s" r="7" fill="%s" class="dot%s">'
            '<title>%s</title></circle>' % (x, y, d.status_colour, flag, esc(tip))
        )
    parts.append("</svg>")
    return "\n".join(parts)


def build_legend():
    items = []
    for _, (label, colour) in STATUS_META.items():
        items.append('<span class="leg"><i style="background:%s"></i>%s</span>'
                     % (colour, esc(label)))
    items.append('<span class="leg"><i style="background:%s"></i>%s</span>'
                 % (UNKNOWN_COLOUR, UNKNOWN_LABEL))
    return '<div class="legend">%s</div>' % "".join(items)


def build_summary(devices):
    counts = {key: 0 for key in STATUS_META}
    counts["unknown"] = 0
    for d in devices:
        counts[d.status_key or "unknown"] += 1

    cards = []
    order = list(STATUS_META.items()) + [("unknown", (UNKNOWN_LABEL, UNKNOWN_COLOUR))]
    for key, (label, colour) in order:
        cards.append(
            '<div class="card" style="border-top-color:%s">'
            '<div class="card-n">%d</div><div class="card-l">%s</div></div>'
            % (colour, counts.get(key, 0), esc(label))
        )
    return '<div class="cards">%s</div>' % "".join(cards)


def build_table(devices):
    rows = []
    for d in devices:
        issue_text = "; ".join(d.issues)
        issue_cell = ('<span class="issue">%s</span>' % esc(issue_text)) if issue_text \
            else '<span class="ok">&#10003;</span>'
        rows.append(
            "<tr>"
            "<td class='mono'>%s</td>"
            "<td>%s</td>"
            "<td><span class='badge' style='background:%s'>%s</span></td>"
            "<td>%s</td>"
            "<td>%s</td>"
            "<td>%s</td>"
            "<td>%s</td>"
            "</tr>" % (
                esc(d.device_id),
                esc(d.name) if d.name else '<span class="muted">no name</span>',
                d.status_colour, esc(d.status_label),
                battery_cell(d.battery),
                esc(humanize_ago(d.last_seen, NOW)),
                esc(d.location) if d.location else '<span class="muted">&mdash;</span>',
                issue_cell,
            )
        )
    return (
        '<table class="grid"><thead><tr>'
        "<th>Device</th><th>Name</th><th>Status</th><th>Battery</th>"
        "<th>Last seen</th><th>Location</th><th>Notes</th>"
        "</tr></thead><tbody>%s</tbody></table>" % "".join(rows)
    )


def build_issues(devices):
    flagged = [d for d in devices if d.issues]
    if not flagged:
        return '<p class="all-clear">No data issues detected.</p>'
    items = []
    for d in flagged:
        items.append("<li><span class='mono'>%s</span> &mdash; %s</li>"
                     % (esc(d.device_id), esc("; ".join(d.issues))))
    return (
        '<p class="issues-intro">%d of %d rows had problems. They are kept '
        "visible (not dropped) so nothing is silently lost:</p>"
        "<ul class='issues-list'>%s</ul>" % (len(flagged), len(devices), "".join(items))
    )


CSS = """
:root { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: #f1f5f9; color: #0f172a; }
header { background: #0f172a; color: #fff; padding: 20px 28px; }
header h1 { margin: 0; font-size: 22px; }
header p { margin: 4px 0 0; color: #94a3b8; font-size: 13px; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 28px 56px; }
section { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 24px;
          box-shadow: 0 1px 3px rgba(15,23,42,.08); }
section h2 { margin: 0 0 16px; font-size: 15px; text-transform: uppercase;
             letter-spacing: .04em; color: #475569; }
.cards { display: flex; flex-wrap: wrap; gap: 14px; }
.card { flex: 1 1 120px; background: #f8fafc; border-radius: 10px; padding: 16px;
        border-top: 4px solid #cbd5e1; text-align: center; }
.card-n { font-size: 30px; font-weight: 700; }
.card-l { font-size: 13px; color: #64748b; margin-top: 2px; }
.map-wrap { display: flex; flex-direction: column; align-items: center; }
.map { width: 100%; max-width: 760px; height: auto; background: #e2e8f0; border-radius: 10px; }
.land { fill: #cbd5e1; stroke: #94a3b8; stroke-width: 1.2; }
.dot { stroke: #fff; stroke-width: 2; }
.dot.map-flag { stroke: #0f172a; stroke-dasharray: 2 2; }
.zoom-controls { display: flex; gap: 8px; margin-bottom: 10px; align-self: flex-start; }
.zoom-controls button { width: 36px; height: 32px; border: 1px solid #cbd5e1; background: #fff;
                        border-radius: 8px; cursor: pointer; font-size: 16px; font-weight: 700; color: #0f172a; }
.zoom-controls button:hover { background: #f1f5f9; }
.zoom-controls .reset { width: auto; padding: 0 12px; font-size: 13px; font-weight: 600; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 14px; font-size: 13px; }
.leg { display: inline-flex; align-items: center; gap: 6px; color: #475569; }
.leg i { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
table.grid { width: 100%; border-collapse: collapse; font-size: 14px; }
.grid th { text-align: left; padding: 10px; border-bottom: 2px solid #e2e8f0;
           color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
.grid td { padding: 10px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
.grid tr:hover td { background: #f8fafc; }
.grid th:last-child, .grid td:last-child { width: 20%; word-break: break-word; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
.badge { color: #fff; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.bat { display: flex; align-items: center; gap: 8px; }
.bat-track { width: 70px; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden; }
.bat-fill { height: 100%; }
.bat-pct { font-size: 13px; color: #475569; min-width: 34px; }
.muted { color: #94a3b8; }
.ok { color: #16a34a; font-weight: 700; }
.issue { color: #b91c1c; font-size: 13px; }
.issues-intro { color: #475569; font-size: 14px; margin-top: 0; }
.issues-list { margin: 0; padding-left: 20px; line-height: 1.7; font-size: 14px; }
.all-clear { color: #16a34a; }
"""


ZOOM_CONTROLS = (
    "<div class='zoom-controls'>"
    "<button type='button' onclick='fleetZoom(0.8)' aria-label='Zoom in'>+</button>"
    "<button type='button' onclick='fleetZoom(1.25)' aria-label='Zoom out'>&minus;</button>"
    "<button type='button' class='reset' onclick='fleetReset()'>Reset</button>"
    "</div>"
)

# Zoom buttons + drag-to-pan (no wheel). Pure vanilla JS driving the SVG
# viewBox, clamped to the map bounds so you cannot drift into empty space.
ZOOM_JS = ("""
<script>
(function () {
  var BASE = [0, 0, __W__, __H__];
  var vb = BASE.slice();
  var svg = document.getElementById('fleetmap');
  if (!svg) return;
  function apply() { svg.setAttribute('viewBox', vb.join(' ')); }
  function clamp() {
    if (vb[0] < 0) vb[0] = 0;
    if (vb[1] < 0) vb[1] = 0;
    if (vb[0] + vb[2] > BASE[2]) vb[0] = BASE[2] - vb[2];
    if (vb[1] + vb[3] > BASE[3]) vb[1] = BASE[3] - vb[3];
  }
  window.fleetZoom = function (factor) {
    var minW = BASE[2] * 0.2, maxW = BASE[2];
    var nw = vb[2] * factor, nh = vb[3] * factor;
    if (nw > maxW) { nw = maxW; nh = BASE[3]; }
    if (nw < minW) { nw = minW; nh = minW * BASE[3] / BASE[2]; }
    var cx = vb[0] + vb[2] / 2, cy = vb[1] + vb[3] / 2;
    vb[2] = nw; vb[3] = nh; vb[0] = cx - nw / 2; vb[1] = cy - nh / 2;
    clamp(); apply();
  };
  window.fleetReset = function () { vb = BASE.slice(); apply(); };

  // drag to pan
  var dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  svg.style.cursor = 'grab';
  svg.addEventListener('pointerdown', function (e) {
    dragging = true; sx = e.clientX; sy = e.clientY; ox = vb[0]; oy = vb[1];
    svg.style.cursor = 'grabbing'; svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    var rect = svg.getBoundingClientRect();
    vb[0] = ox - (e.clientX - sx) * (vb[2] / rect.width);
    vb[1] = oy - (e.clientY - sy) * (vb[3] / rect.height);
    clamp(); apply();
  });
  function end() { dragging = false; svg.style.cursor = 'grab'; }
  svg.addEventListener('pointerup', end);
  svg.addEventListener('pointercancel', end);
})();
</script>
""").replace("__W__", str(MAP_W)).replace("__H__", str(MAP_H))


def build_html(devices, now):
    mapped = sum(1 for d in devices if d.mappable)
    flagged = sum(1 for d in devices if d.issues)
    subtitle = ("Generated %s &middot; %d devices &middot; %d on map &middot; %d with data issues"
                % (now.strftime("%d %b %Y %H:%M"), len(devices), mapped, flagged))
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Fleet Dashboard</title><style>%s</style></head><body>"
        "<header><h1>Fleet Dashboard</h1><p>%s</p></header><main>"
        "<section><h2>Summary</h2>%s</section>"
        "<section><h2>Map</h2><div class='map-wrap'>%s%s%s</div></section>"
        "<section><h2>Devices</h2>%s</section>"
        "<section><h2>Data issues</h2>%s</section>"
        "</main>%s</body></html>" % (
            CSS, subtitle,
            build_summary(devices),
            ZOOM_CONTROLS, build_svg(devices), build_legend(),
            build_table(devices),
            build_issues(devices),
            ZOOM_JS,
        )
    )


# --- Entry point -------------------------------------------------------------

NOW = datetime.now()


def main():
    devices = parse_devices(INPUT_CSV, NOW)
    html_out = build_html(devices, NOW)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html_out)
    flagged = sum(1 for d in devices if d.issues)
    print("Wrote %s (%d devices, %d flagged with data issues)."
          % (os.path.basename(OUTPUT_HTML), len(devices), flagged))


if __name__ == "__main__":
    main()
