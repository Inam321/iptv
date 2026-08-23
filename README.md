# Verified IPTV playlists

Automatically maintained M3U playlists for **India**, **Pakistan** and
**international sports**, containing only channels that were confirmed to be
streaming within the last 24 hours.

Built for [SS IPTV](https://ss-iptv.com) on VIDAA smart TVs and for Android TV
players such as TiviMate and IPTV Smarters. Add a link once; it stays current
on its own.

Channel data comes from [iptv-org](https://github.com/iptv-org/iptv), which
indexes publicly available free-to-air streams. **This project hosts no video.**
It verifies, filters and re-groups links that are already public.

---

## Playlist links

Replace `USERNAME/REPO` with your own once you have published.

| Playlist | URL |
|---|---|
| India + Pakistan News | `https://USERNAME.github.io/REPO/news.m3u` |
| India + Pakistan Music | `https://USERNAME.github.io/REPO/music.m3u` |
| India + Pakistan Movies | `https://USERNAME.github.io/REPO/movies.m3u` |
| India + Pakistan Entertainment | `https://USERNAME.github.io/REPO/entertainment.m3u` |
| India + Pakistan Kids | `https://USERNAME.github.io/REPO/kids.m3u` |
| Pakistan Religious | `https://USERNAME.github.io/REPO/religious-pk.m3u` |
| International Sports | `https://USERNAME.github.io/REPO/sports.m3u` |

Live status page: `https://USERNAME.github.io/REPO/`

These URLs never change. The contents behind them refresh daily.

---

## Setup

**1. Create the repository**

Create a new **public** repo on GitHub and upload every file from this project,
keeping the folder structure intact:

```
config.json
README.md
scripts/build.py
scripts/verify.py
scripts/make_index.py
.github/workflows/update.yml
```

The workflow file must sit at exactly `.github/workflows/update.yml`.

**2. Allow the workflow to commit**

Settings → Actions → General → Workflow permissions →
select **Read and write permissions** → Save.

Without this the run succeeds but cannot push its results.

**3. Run it once**

Actions tab → **Update playlists** → **Run workflow**.

The first run takes 20–40 minutes, mostly waiting on dead servers to time out.

**4. Turn on GitHub Pages**

Settings → Pages → Source: **Deploy from a branch** →
Branch `main`, folder **`/docs`** → Save.

Wait a minute or two, then open `https://USERNAME.github.io/REPO/`.

---

## Adding a playlist to your TV

**SS IPTV (VIDAA)**
Gear icon → `Content` → `External playlists` → `Add` → paste the URL → Save.
Exit the app completely with the Home button, then reopen it.

**Android TV** (TiviMate, IPTV Smarters, OTT Navigator)
Add playlist → M3U URL → paste.

**VLC** — `Ctrl+N`, paste, Play. Worth testing a link here before the TV.

Typing a long URL on a remote is unpleasant. Shorten it at
[is.gd](https://is.gd) first, or pair a Bluetooth keyboard.

---

## How it works

Three scripts run in sequence, every day.

**`scripts/build.py`** downloads the iptv-org API (channels, streams, logos,
feeds), selects channels matching each playlist's country and category rules
from `config.json`, attaches logos and EPG ids, and writes source playlists to
`build/`. Up to three streams per channel are kept at this stage as fallbacks.

**`scripts/verify.py`** tests every stream twice:

1. Fetch the HLS manifest — catches dead servers, 403, 404, timeouts, DNS
   failures.
2. Fetch an actual video segment — catches the common case where a manifest
   loads perfectly but no video exists behind it.

Only streams passing both are kept. Duplicates then collapse to one entry per
channel, choosing the best remaining stream by host quality, HTTPS and
resolution. Results are published to `docs/`, with per-channel CSV reports in
`reports/`.

**`scripts/make_index.py`** regenerates the status page from the run's results.

### Safety behaviour

If a playlist verifies to zero working channels but the published version had
some, the old file is kept. A transient network fault on GitHub's side cannot
wipe a working playlist.

### Requirements

Python 3.9 or newer. No third-party packages — standard library only.

---

## Running it locally

```bash
python scripts/build.py
python scripts/verify.py --workers 40 --timeout 8
python scripts/make_index.py --repo USERNAME/REPO
```

Verify a single playlist while testing:

```bash
python scripts/verify.py --only music.m3u
```

Local runs test from **your** connection, so results reflect what your TV will
actually receive. That is usually more accurate than the scheduled run.

---

## Changing what gets built

Everything is driven by `config.json`.

```json
{
  "file": "music.m3u",
  "title": "India + Pakistan Music",
  "countries": ["IN", "PK"],
  "categories": ["music"]
}
```

`countries` uses ISO 3166 codes; `null` means every country.
`categories` accepts: news, music, movies, sports, entertainment, kids,
animation, religious, documentary, business, comedy, cooking, culture,
education, family, general, legislative, lifestyle, outdoor, relax, science,
series, travel, weather.

Options:

| Option | Effect |
|---|---|
| `exclude_geo_blocked` | Drop streams iptv-org marks as region-locked |
| `exclude_bare_ip` | Drop streams on raw IP addresses, which die fastest |
| `exclude_not_24_7` | Drop part-time channels |
| `max_streams_per_channel_before_verify` | Fallbacks tested per channel |
| `max_streams_per_channel_after_verify` | Entries published per channel |

---

## Known limitations

**Verification runs from GitHub's servers in the US.** Geo-restricted streams
behave differently there than from Pakistan or India — some Indian channels
locked to India will fail in both places, but results will not match yours
exactly. Run the scripts locally for a list tuned to your connection.

**Scheduled workflows pause after 60 days of repository inactivity.** GitHub
emails you and you click a button to re-enable. Automated commits do not
reliably reset that timer.

**Pakistani coverage is thin, and that is not fixable here.** Geo, ARY and Hum
do not publish open stream URLs; they monetise through their own apps and
YouTube. For those channels, the official YouTube live streams and
[mjunoon.tv](https://mjunoon.tv) are better sources than any M3U playlist.

**Free streams decay constantly.** A 25–40% pass rate is normal. The point of
this project is that you never have to notice.
