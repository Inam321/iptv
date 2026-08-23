#!/usr/bin/env python3
"""
verify.py - test every stream in build/ and publish only the working ones.

Two-stage check per stream:
  1. Fetch the HLS manifest (or the head of a raw stream)
     -> catches dead servers, 403, 404, DNS failures, timeouts
  2. Fetch an actual video segment
     -> catches "manifest loads fine but there is no video", which is the
        usual cause of a channel appearing in the list but refusing to play

After testing, duplicates are collapsed: one working stream per channel,
picking the best one. Output goes to docs/ for GitHub Pages.

Safety: if a playlist ends up with zero working channels but the previously
published version had some, the old file is kept. That prevents a transient
network problem from wiping a good playlist.

USAGE
    python scripts/verify.py
    python scripts/verify.py --workers 40 --timeout 8
    python scripts/verify.py --only music.m3u
"""

import argparse
import concurrent.futures as cf
import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Many stream hosts have broken or self-signed certificates. We are checking
# video availability, not authenticating anything sensitive.
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

GOOD_HOSTS = ("akamaized.net", "cloudfront.net", "amagi.tv", "jsrdn.com",
              "wiseplayout.com", "live247stream.com", "dai.google.com",
              "fastly.net", "cdn77.com", "aryzap.com")


def log(m):
    print(m, flush=True)


class Entry:
    __slots__ = ("extinf", "opts", "url", "name", "tvg_id", "group")

    def __init__(self, extinf, opts, url):
        self.extinf = extinf
        self.opts = opts
        self.url = url
        self.name = extinf.split(",", 1)[1].strip() if "," in extinf else url
        m = re.search(r'tvg-id="([^"]*)"', extinf)
        self.tvg_id = m.group(1) if m else self.name
        g = re.search(r'group-title="([^"]*)"', extinf)
        self.group = g.group(1) if g else ""

    @property
    def channel_key(self):
        """Group sub-feeds of the same channel together."""
        return self.tvg_id.split("@")[0] or self.name

    @property
    def host(self):
        return re.sub(r"^https?://", "", self.url).split("/")[0]

    def headers(self):
        h = {"User-Agent": DEFAULT_UA}
        for o in self.opts:
            m = re.search(r"http-user-agent=(.+)$", o, re.I)
            if m:
                h["User-Agent"] = m.group(1).strip()
            m = re.search(r"http-referrer=(.+)$", o, re.I)
            if m:
                h["Referer"] = m.group(1).strip()
        return h

    def rank(self):
        s = 0.0
        if any(g in self.host for g in GOOD_HOSTS):
            s += 40
        if re.match(r"^\d+\.\d+\.\d+\.\d+", self.host):
            s -= 30
        if self.url.startswith("https"):
            s += 10
        q = re.search(r"\((\d+)p\)", self.name)
        if q:
            s += int(q.group(1)) / 100.0
        return s


def parse_m3u(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out, extinf, opts = [], None, []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line == "#EXTM3U":
            continue
        if line.startswith("#EXTINF"):
            extinf, opts = line, []
        elif line.startswith("#"):
            if extinf:
                opts.append(line)
        elif line.startswith(("http://", "https://")):
            if extinf:
                out.append(Entry(extinf, opts[:], line))
            extinf, opts = None, []
    return out


def http_get(url, headers, timeout, max_bytes=200_000, byte_range=None):
    h = dict(headers)
    if byte_range:
        h["Range"] = byte_range
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.status, r.read(max_bytes)


def pick_next(manifest_text, manifest_url, depth=0):
    lines = [l.strip() for l in manifest_text.split("\n") if l.strip()]
    cand = [l for l in lines if not l.startswith("#")]
    if not cand:
        return None
    target = cand[0]
    abs_url = urllib.parse.urljoin(manifest_url, target)
    if depth == 0 and re.search(r"\.m3u8?(\?|$)", target, re.I):
        return ("manifest", abs_url)
    return ("segment", abs_url)


def check(entry, timeout):
    t0 = time.time()
    hdrs = entry.headers()
    try:
        status, body = http_get(entry.url, hdrs, timeout)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", time.time() - t0
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))[:40]
        return False, f"unreachable ({reason})", time.time() - t0
    except Exception as e:                          # noqa: BLE001
        return False, type(e).__name__, time.time() - t0

    if status not in (200, 206):
        return False, f"HTTP {status}", time.time() - t0
    if not body:
        return False, "empty response", time.time() - t0

    if not body[:64].lstrip().startswith(b"#EXTM3U"):
        ok = len(body) > 8192
        return ok, "stream data" if ok else "too little data", time.time() - t0

    nxt = pick_next(body.decode("utf-8", "replace"), entry.url)
    if not nxt:
        return False, "manifest has no segments", time.time() - t0

    kind, url2 = nxt
    try:
        if kind == "manifest":
            s2, b2 = http_get(url2, hdrs, timeout)
            if s2 not in (200, 206) or not b2.lstrip().startswith(b"#EXTM3U"):
                return False, "variant manifest failed", time.time() - t0
            n2 = pick_next(b2.decode("utf-8", "replace"), url2, depth=1)
            if not n2:
                return False, "no segments in variant", time.time() - t0
            url2 = n2[1]
        s3, b3 = http_get(url2, hdrs, timeout, 32_768, "bytes=0-32767")
        if s3 not in (200, 206):
            return False, f"segment HTTP {s3}", time.time() - t0
        if len(b3) < 1024:
            return False, "segment empty", time.time() - t0
        return True, "playing", time.time() - t0
    except urllib.error.HTTPError as e:
        return False, f"segment HTTP {e.code}", time.time() - t0
    except Exception as e:                          # noqa: BLE001
        return False, f"segment {type(e).__name__}", time.time() - t0


def count_entries(path):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for l in f if l.startswith("#EXTINF"))
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--indir", default="build")
    ap.add_argument("--outdir", default="docs")
    ap.add_argument("--reportdir", default="reports")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=8)
    ap.add_argument("--only", help="verify a single playlist file")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    opts = cfg.get("options", {})
    keep_after = int(opts.get("max_streams_per_channel_after_verify", 1))

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.reportdir, exist_ok=True)

    playlists = cfg["playlists"]
    if args.only:
        playlists = [p for p in playlists if p["file"] == args.only]
        if not playlists:
            log(f"no playlist named {args.only}")
            return 1

    status = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "playlists": []}
    grand_tested = grand_ok = 0

    for pl in playlists:
        src = os.path.join(args.indir, pl["file"])
        dst = os.path.join(args.outdir, pl["file"])
        if not os.path.isfile(src):
            log(f"!! missing {src}, skipping")
            continue

        with open(src, encoding="utf-8", errors="replace") as f:
            entries = parse_m3u(f.read())

        log(f"\n{pl['title']}  ({pl['file']})")
        log(f"  testing {len(entries)} streams ...")

        results = []
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(check, e, args.timeout): e for e in entries}
            for fut in cf.as_completed(futs):
                e = futs[fut]
                try:
                    ok, reason, el = fut.result()
                except Exception as exc:            # noqa: BLE001
                    ok, reason, el = False, f"crash {exc}"[:40], 0.0
                results.append((e, ok, reason, el))

        # one working stream per channel, best ranked
        by_chan = defaultdict(list)
        for e, ok, _, _ in results:
            if ok:
                by_chan[e.channel_key].append(e)
        chosen = []
        for _, group in by_chan.items():
            group.sort(key=lambda x: x.rank(), reverse=True)
            chosen.extend(group[:keep_after])
        chosen.sort(key=lambda e: (e.group, e.name.lower()))

        tested, n_ok = len(results), len(chosen)
        grand_tested += tested
        grand_ok += n_ok
        elapsed = time.time() - t0

        prev = count_entries(dst)
        if n_ok == 0 and prev > 0:
            log(f"  !! 0 working but {prev} previously published - "
                f"keeping the existing file")
            wrote = prev
            kept_old = True
        else:
            header = (f"#EXTM3U\n"
                      f"# {pl['title']}\n"
                      f"# {n_ok} verified working channels\n"
                      f"# updated {status['updated_at']}\n"
                      f"# generated from https://github.com/iptv-org/iptv\n")
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(header)
                for e in chosen:
                    f.write(e.extinf + "\n")
                    for o in e.opts:
                        f.write(o + "\n")
                    f.write(e.url + "\n")
            wrote = n_ok
            kept_old = False

        rep = os.path.join(args.reportdir,
                           pl["file"].replace(".m3u", "-report.csv"))
        with open(rep, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["channel", "tvg_id", "group", "working", "reason",
                        "seconds", "host", "url"])
            for e, ok, reason, el in sorted(
                    results, key=lambda x: (not x[1], x[0].name.lower())):
                w.writerow([e.name, e.tvg_id, e.group, "YES" if ok else "no",
                            reason, f"{el:.1f}", e.host, e.url])

        reasons = Counter(r for _, ok, r, _ in results if not ok)
        pct = n_ok / tested * 100 if tested else 0
        log(f"  {n_ok} working channels from {tested} streams "
            f"({pct:.0f}%) in {elapsed:.0f}s")
        if reasons:
            top = "  ".join(f"{r}={c}" for r, c in reasons.most_common(3))
            log(f"  top failures: {top}")

        status["playlists"].append({
            "file": pl["file"],
            "title": pl["title"],
            "streams_tested": tested,
            "channels_working": wrote,
            "kept_previous": kept_old,
            "top_failures": dict(reasons.most_common(5)),
        })

    with open(os.path.join(args.outdir, "status.json"), "w",
              encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    lines = ["# Playlist status", "",
             f"Last updated: **{status['updated_at']}**", "",
             "| Playlist | Streams tested | Working channels |",
             "|---|---:|---:|"]
    for p in status["playlists"]:
        note = " *(kept previous)*" if p["kept_previous"] else ""
        lines.append(f"| {p['title']} | {p['streams_tested']} | "
                     f"{p['channels_working']}{note} |")
    lines += ["", f"Total: **{grand_ok}** working channels "
                  f"from {grand_tested} streams tested."]
    with open("STATUS.md", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    log(f"\n{'=' * 60}")
    log(f"  {grand_ok} working channels from {grand_tested} streams tested")
    log(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
