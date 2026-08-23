#!/usr/bin/env python3
"""
build.py - generate categorised M3U playlists from the iptv-org API.

iptv-org publishes playlists by country OR by category, never both. This
builds the combinations defined in config.json, attaches logos and EPG ids,
removes duplicates, and writes the result to build/.

Nothing here checks whether a stream works - that is verify.py's job.

USAGE
    python scripts/build.py
    python scripts/build.py --config config.json --outdir build
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

API = "https://raw.githubusercontent.com/iptv-org/api/gh-pages"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Hosts we trust more. Used only to rank duplicate streams, never to exclude.
GOOD_HOSTS = (
    "akamaized.net", "cloudfront.net", "amagi.tv", "wiseplayout.com",
    "jsrdn.com", "live247stream.com", "yuppcdn.net", "dai.google.com",
    "5centscdn.com", "mcncdndigital.com", "bozztv.com", "aryzap.com",
    "fastly.net", "llnwd.net", "cdn77.com", "edgecastcdn.net",
    "googlevideo.com", "brightcove.com", "jwplayer.com", "wowza.com",
)


def log(msg):
    print(msg, flush=True)


def fetch_json(name, retries=3):
    url = f"{API}/{name}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:                      # noqa: BLE001
            last = e
            log(f"    retry {attempt + 1}/{retries} for {name}: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"could not fetch {name}: {last}")


def host_of(url):
    return re.sub(r"^https?://", "", url).split("/")[0]


def is_bare_ip(url):
    return bool(re.match(r"^\d+\.\d+\.\d+\.\d+", host_of(url)))


def quality_rank(q):
    if not q:
        return 0
    m = re.match(r"(\d+)", str(q))
    return int(m.group(1)) if m else 0


def stream_score(s):
    """Rank duplicate streams for the same channel. Higher is better."""
    url = s.get("url") or ""
    score = 0.0
    h = host_of(url)
    if is_bare_ip(url):
        score -= 30
    if any(g in h for g in GOOD_HOSTS):
        score += 40
    if url.startswith("https"):
        score += 10
    score += quality_rank(s.get("quality")) / 100.0
    label = (s.get("label") or "").lower()
    if "geo-blocked" in label:
        score -= 50
    if "not 24/7" in label:
        score -= 15
    return score


def escape_attr(v):
    return (v or "").replace('"', "'").replace("\n", " ").strip()


def build_tvg_id(stream, feeds_by_channel):
    """iptv-org convention: ChannelId.cc or ChannelId.cc@FeedId for sub-feeds."""
    cid = stream.get("channel") or ""
    feed = stream.get("feed")
    if not feed:
        return cid
    mains = feeds_by_channel.get(cid, {})
    if mains.get(feed) is True:          # main feed - no suffix
        return cid
    return f"{cid}@{feed}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--outdir", default="build")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    opts = cfg.get("options", {})

    log("Fetching iptv-org API data ...")
    channels = fetch_json("channels.json")
    streams = fetch_json("streams.json")
    logos = fetch_json("logos.json")
    feeds = fetch_json("feeds.json")
    log(f"  channels {len(channels):,}   streams {len(streams):,}   "
        f"logos {len(logos):,}   feeds {len(feeds):,}")

    # ---- channel metadata -------------------------------------------------
    meta = {}
    for c in channels:
        if c.get("closed") or c.get("is_nsfw"):
            continue
        meta[c["id"]] = {
            "name": c.get("name") or c["id"],
            "country": c.get("country") or "",
            "categories": set(c.get("categories") or []),
        }
    log(f"  usable channels (not closed / not nsfw): {len(meta):,}")

    # ---- best logo per channel -------------------------------------------
    def logo_rank(l):
        r = 0
        if l.get("in_use"):
            r += 100
        fmt = (l.get("format") or "").upper()
        if fmt in ("SVG", "PNG"):
            r += 20
        w = l.get("width") or 0
        r += min(int(w), 1000) / 1000.0
        return r

    best_logo = {}
    for l in logos:
        cid = l.get("channel")
        if not cid or not l.get("url"):
            continue
        if cid not in best_logo or logo_rank(l) > logo_rank(best_logo[cid]):
            best_logo[cid] = l
    log(f"  channels with a logo: {len(best_logo):,}")

    # ---- feed language + main-feed lookup --------------------------------
    feeds_by_channel = defaultdict(dict)
    lang_by = {}
    for fd in feeds:
        cid, fid = fd.get("channel"), fd.get("id")
        if not cid or not fid:
            continue
        feeds_by_channel[cid][fid] = bool(fd.get("is_main"))
        langs = fd.get("languages") or []
        if langs:
            lang_by[(cid, fid)] = langs[0]

    # ---- group streams by channel ----------------------------------------
    by_channel = defaultdict(list)
    for s in streams:
        cid = s.get("channel")
        if cid and cid in meta and s.get("url"):
            by_channel[cid].append(s)

    os.makedirs(args.outdir, exist_ok=True)
    keep_n = int(opts.get("max_streams_per_channel_before_verify", 3))
    summary = []

    for pl in cfg["playlists"]:
        wanted_cats = set(pl["categories"])
        wanted_countries = (set(x.upper() for x in pl["countries"])
                            if pl.get("countries") else None)

        rows = []
        seen_urls = set()
        n_channels = 0

        for cid, m in meta.items():
            if wanted_countries and m["country"] not in wanted_countries:
                continue
            if not (m["categories"] & wanted_cats):
                continue
            cand = by_channel.get(cid)
            if not cand:
                continue

            # rank and de-duplicate this channel's streams
            cand = sorted(cand, key=stream_score, reverse=True)
            picked = []
            for s in cand:
                url = s["url"]
                if url in seen_urls:
                    continue
                label = (s.get("label") or "").lower()
                if opts.get("exclude_geo_blocked") and "geo-blocked" in label:
                    continue
                if opts.get("exclude_not_24_7") and "not 24/7" in label:
                    continue
                if opts.get("exclude_bare_ip") and is_bare_ip(url):
                    continue
                seen_urls.add(url)
                picked.append(s)
                if len(picked) >= keep_n:
                    break
            if picked:
                n_channels += 1
                for s in picked:
                    rows.append((m, cid, s))

        # stable ordering: country, then channel name
        rows.sort(key=lambda r: (r[0]["country"], r[0]["name"].lower()))

        lines = ["#EXTM3U"]
        for m, cid, s in rows:
            country = m["country"]
            group = f"{country} {pl['categories'][0].title()}"
            title = s.get("title") or m["name"]
            if s.get("quality"):
                title += f" ({s['quality']})"
            if s.get("label"):
                title += f" [{s['label']}]"

            tvg_id = build_tvg_id(s, feeds_by_channel)
            logo = best_logo.get(cid, {}).get("url", "")
            lang = lang_by.get((cid, s.get("feed")), "")

            attrs = [
                f'tvg-id="{escape_attr(tvg_id)}"',
                f'tvg-name="{escape_attr(m["name"])}"',
            ]
            if logo:
                attrs.append(f'tvg-logo="{escape_attr(logo)}"')
            if country:
                attrs.append(f'tvg-country="{country}"')
            if lang:
                attrs.append(f'tvg-language="{lang}"')
            attrs.append(f'group-title="{escape_attr(group)}"')

            lines.append(f"#EXTINF:-1 {' '.join(attrs)},{title}")
            if s.get("user_agent"):
                lines.append(f"#EXTVLCOPT:http-user-agent={s['user_agent']}")
            if s.get("referrer"):
                lines.append(f"#EXTVLCOPT:http-referrer={s['referrer']}")
            lines.append(s["url"])

        path = os.path.join(args.outdir, pl["file"])
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

        per_country = defaultdict(int)
        for m, _, _ in rows:
            per_country[m["country"]] += 1
        detail = " ".join(f"{k}:{v}" for k, v in sorted(per_country.items()))
        log(f"  {pl['file']:<22} {n_channels:>4} channels  "
            f"{len(rows):>4} streams   {detail[:60]}")
        summary.append({"file": pl["file"], "title": pl["title"],
                        "channels": n_channels, "streams": len(rows)})

    with open(os.path.join(args.outdir, "_build.json"), "w",
              encoding="utf-8") as f:
        json.dump({"built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                   "playlists": summary}, f, indent=2)

    log(f"\nWrote {len(summary)} playlists to {args.outdir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
