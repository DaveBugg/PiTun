# Screenshots

Drop UI screenshots here and they will be picked up by the project README.

## Suggested set (one PNG each, 1280×800-ish)

| File | What to capture |
|---|---|
| `dashboard.png` | Dashboard with Service Status tiles, MetricsCharts, Recent Events feed |
| `nodes.png` | Nodes page with a few nodes (different protocols), latency / online state |
| `routing.png` | Routing rules — drag handle visible, mix of geoip / domain / mac rules |
| `subscriptions.png` | Subscriptions page with one synced subscription showing node count |
| `circles.png` | Node Circles page, one circle with current_index highlighted |
| `dns.png` | DNS rules + DNS query log |
| `devices.png` | Devices page with the LAN-discovery results |
| `settings.png` | Settings page |

## Reference them from the README

Standard inline image:

```markdown
![Dashboard](docs/screenshots/dashboard.png)
```

For tall full-page captures (Dashboard, Settings — anything that
scrolls a lot), use an HTML wrapper to render a thumbnail in the
README column and let users click through to the full size:

```html
<a href="docs/screenshots/dashboard.png">
  <img src="docs/screenshots/dashboard.png" width="600" alt="Dashboard">
</a>
```

## Capture guidelines

- **Format**: PNG. JPG mangles crisp UI text and icons.
- **Width**: 1280–1440 px. Don't snap at 4K — GitHub scales the README
  column down to ~870 px regardless, so the extra pixels are pure
  weight with zero visible benefit.
- **File size**: under 500 KB each. Tall full-page captures of the
  Dashboard easily hit 2–3 MB raw. Run them through
  [`oxipng -o4`](https://github.com/shssoichiro/oxipng) or
  [squoosh.app](https://squoosh.app/) (lossless OxiPNG mode) — 60–70 %
  shrink with no visible quality loss.
- **Tall vs. cropped**: a single tall full-page screenshot of the
  Dashboard works fine. For other pages, 16:10 cropped to the relevant
  area is easier to skim than scroll-everything. Mix and match.
