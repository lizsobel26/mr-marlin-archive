#!/usr/bin/env python3
"""Check every video link in data/videos.json.

Writes data/link-status.json (read by the website) and broken-links.md (used as the
GitHub issue body). Each link gets one status:
  ok       the video is still there
  broken   the host says it's gone (deleted, private, 404)
  moved    the page now redirects somewhere unrelated, usually a sign the video was pulled
  unknown  we couldn't tell (timeout, bot protection); never shown to visitors as broken
"""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_PATH = ROOT / "data" / "videos.json"
STATUS_PATH = ROOT / "data" / "link-status.json"
REPORT_PATH = ROOT / "broken-links.md"

TIMEOUT_S = 25  # WHY: archive.org metadata for big multi-game items can take 10+ seconds; shorter caused false "unknown"s
WORKERS = 6  # WHY: finishes ~200 links in a couple of minutes without hitting any one host hard enough to get rate-limited
MAX_BODY_BYTES = 30_000_000  # WHY: archive.org metadata for 30-game collections runs several MB; cap guards against runaway downloads
# WHY: MLB.com and several TV sites answer 403/406 to non-browser user agents
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# WHY: these codes usually mean "bot protection or rate limit", not "the video is gone"
INCONCLUSIVE_CODES = {401, 403, 406, 429}
SOFT_404 = re.compile(r"<title>[^<]*(page not found|404|video unavailable)", re.I)


def fetch(url):
    """Return (status_code, final_url, body_text). Network errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read(MAX_BODY_BYTES).decode("utf-8", "replace")
            return resp.status, resp.geturl(), body
    except urllib.error.HTTPError as e:
        return e.code, url, ""


def youtube_id(url):
    u = urllib.parse.urlparse(url)
    host = u.hostname or ""
    if host.endswith("youtu.be"):
        return u.path.strip("/") or None
    if host.endswith("youtube.com"):
        return urllib.parse.parse_qs(u.query).get("v", [None])[0]
    return None


def check_youtube(url, get):
    vid = youtube_id(url)
    if not vid:
        return "broken", "Not a valid YouTube video link"
    watch = "https://www.youtube.com/watch?v=" + vid
    status, _, _ = get("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(watch, safe=""))
    if status == 200:
        return "ok", ""
    if status in (400, 404):
        return "broken", "YouTube says this video no longer exists"
    # WHY: oEmbed answers 401 both for private videos and for live videos with embedding turned off,
    # so the watch page has to settle it
    _, _, page = get(watch + "&hl=en")
    m = re.search(r'"playabilityStatus":\{"status":"(\w+)"(?:,"reason":"([^"]*)")?', page)
    if not m:
        return "unknown", "YouTube returned %s" % status
    state, reason = m.group(1), m.group(2) or ""
    if state == "OK":
        return "ok", ""
    # WHY: LOGIN_REQUIRED also covers YouTube's bot check (common from cloud servers like GitHub's)
    # and age-restricted videos, which still exist; only a private video is really gone
    if state == "LOGIN_REQUIRED" and "private" not in reason.lower():
        return "unknown", reason or "YouTube asked us to sign in"
    # WHY: region blocks depend on where the checker runs, not on whether the video exists
    if state == "UNPLAYABLE" and "country" in reason.lower():
        return "unknown", reason
    if state in ("ERROR", "UNPLAYABLE", "LOGIN_REQUIRED"):
        return "broken", reason or "YouTube won't play this video"
    return "unknown", "YouTube reported %s" % state


def check_archive(url, get):
    parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(url).path.split("/") if p]
    if len(parts) < 2 or parts[0] != "details":
        return "unknown", "Not an Internet Archive item link"
    item, file_name = parts[1], "/".join(parts[2:])
    status, _, body = get("https://archive.org/metadata/" + urllib.parse.quote(item))
    if status != 200:
        return "unknown", "Internet Archive returned %s" % status
    try:
        meta = json.loads(body or "{}")
    except ValueError:
        return "unknown", "Internet Archive sent an unreadable answer"
    if not meta or meta.get("is_dark"):
        return "broken", "Removed from the Internet Archive"
    if file_name and file_name not in {f.get("name") for f in meta.get("files", [])}:
        return "broken", "That file is no longer in the Internet Archive item"
    return "ok", ""


def _identifier(url):
    """The most specific piece of a URL: an id in the query, or the last path segment."""
    u = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(u.query)
    for key in ("id", "v", "clip"):
        if key in q:
            return q[key][0]
    segments = [s for s in u.path.split("/") if s]
    return segments[-1] if segments else ""


def moved_elsewhere(original, final):
    if original.rstrip("/") == final.rstrip("/"):
        return False
    final_path = urllib.parse.urlparse(final).path.strip("/")
    if not final_path and urllib.parse.urlparse(original).path.strip("/"):
        return True  # WHY: sent to a site's home page, the classic sign of a deleted page
    ident = _identifier(original)
    # WHY: short identifiers ("video", "2003") show up in unrelated URLs and would hide real moves
    return len(ident) >= 6 and ident.lower() not in final.lower()


def check_generic(url, get):
    status, final, body = get(url)
    if status in (404, 410):
        return "broken", "Page not found (%s)" % status
    if status in INCONCLUSIVE_CODES or status >= 500:
        return "unknown", "Site returned %s" % status
    if status >= 400:
        return "broken", "Site returned %s" % status
    if SOFT_404.search(body[:20000]):
        return "broken", "The page says the video can't be found"
    if moved_elsewhere(url, final):
        return "moved", "Now redirects to " + final
    return "ok", ""


def check(url, get=fetch):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    try:
        if host.endswith("youtube.com") or host.endswith("youtu.be"):
            return check_youtube(url, get)
        if host.endswith("archive.org"):
            return check_archive(url, get)
        return check_generic(url, get)
    except Exception as e:  # WHY: one flaky host must not stop the whole run
        return "unknown", "Couldn't reach the site (%s)" % type(e).__name__


def build_report(videos, results):
    bad = [v for v in videos if results[v["id"]]["status"] in ("broken", "moved")]
    if not bad:
        return 0, "All links are working.\n"
    lines = [
        "The monthly link check found **%d** video link(s) that look dead. "
        "Remove or replace them in the archive's management page.\n" % len(bad),
        "| Video | Date | Problem |",
        "|---|---|---|",
    ]
    for v in bad:
        r = results[v["id"]]
        title = v["title"].replace("|", "/")
        lines.append("| [%s](%s) | %s | %s |" % (title, v["url"], v.get("date") or "undated", r["detail"].replace("|", "/")))
    return len(bad), "\n".join(lines) + "\n"


def main():
    videos = json.loads(VIDEOS_PATH.read_text())
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outcomes = list(pool.map(lambda v: check(v["url"]), videos))
    results = {v["id"]: {"status": s, "detail": d, "url": v["url"]} for v, (s, d) in zip(videos, outcomes)}
    STATUS_PATH.write_text(json.dumps(
        {"checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "results": results},
        indent=1, ensure_ascii=False) + "\n")
    bad_count, report = build_report(videos, results)
    REPORT_PATH.write_text(report)
    counts = {}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Checked %d links: %s" % (len(videos), ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    return bad_count


if __name__ == "__main__":
    main()
    sys.exit(0)
