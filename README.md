# Mr. Marlin Video Archive

A fan-made index of videos about Jeff Conine's baseball and racquetball career. The site only links to videos; it never hosts them.

**Live site:** https://lizsobel26.github.io/mr-marlin-archive/

## How it fits together

| Piece | What it does |
|---|---|
| `index.html` | The public site. Plain HTML, no build step. Reads the two JSON files below. |
| `data/videos.json` | The video list. Exported from the private management page on claude.ai, which is the source of truth. |
| `data/link-status.json` | Written by the link checker. The site hides videos whose links look dead. |
| `scripts/check_links.py` | Checks every link (YouTube, Internet Archive, and ordinary web pages). |
| `.github/workflows/check-links.yml` | Runs the checker on the 1st of every month and whenever `videos.json` changes. Opens a "Broken video links" issue when it finds any, and closes it once they're fixed. |

## Updating the site

Add, approve or remove videos on the management page, then ask Claude to "publish the Mr. Marlin archive". That exports the list to `data/videos.json` and pushes it; GitHub Pages updates within a minute or two, and the link check runs automatically.

Visitors suggest videos through the "Suggest a video" GitHub issue form.

## Running locally

```bash
python3 -m unittest discover -s tests
python3 scripts/check_links.py
python3 -m http.server 8000
```
