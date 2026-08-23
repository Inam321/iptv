#!/usr/bin/env python3
"""
make_index.py - build docs/index.html from docs/status.json.

The page is generated statically so it works with JavaScript disabled;
JS only powers the copy-to-clipboard buttons.

USAGE
    python scripts/make_index.py
    python scripts/make_index.py --repo Inam321/iptv
"""

import argparse
import html
import json
import os
import sys

CSS = """
:root{
  --bg:#0A0A0F; --panel:#101019; --rule:#23232E;
  --ink:#C8C8D0; --bright:#F0F0F5; --dim:#6C6C7A;
  --cyan:#4FD8E8; --amber:#F5C518; --green:#52D273;
  --orange:#F5A623; --red:#FF5C5C;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:14px; line-height:1.6;
  padding:clamp(20px,5vw,64px) clamp(16px,4vw,32px);
}
.wrap{max-width:920px;margin:0 auto}

header{border-bottom:2px solid var(--cyan);padding-bottom:20px;margin-bottom:8px}
.eyebrow{
  font-size:11px;letter-spacing:.22em;text-transform:uppercase;
  color:var(--cyan);margin:0 0 10px
}
h1{
  font-family:'Space Mono',monospace;font-weight:700;
  font-size:clamp(26px,6vw,44px);line-height:1.05;
  margin:0;color:var(--bright);letter-spacing:-.02em
}
h1 em{font-style:normal;color:var(--amber)}
.sub{margin:14px 0 0;color:var(--dim);max-width:60ch}
.meta{
  margin-top:18px;font-size:12px;color:var(--dim);
  display:flex;flex-wrap:wrap;gap:8px 22px
}
.meta b{color:var(--ink);font-weight:500}

.rows{margin:34px 0 0;padding:0;list-style:none}
.row{
  border-bottom:1px solid var(--rule);
  padding:22px 0;
  animation:rise .5s cubic-bezier(.2,.7,.3,1) backwards;
}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.row{animation:none}}

.top{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:baseline}
.title{
  font-family:'Space Mono',monospace;font-weight:700;
  font-size:17px;color:var(--bright);margin:0;flex:1 1 auto
}
.count{font-size:12px;color:var(--dim);white-space:nowrap}
.count b{font-size:15px;font-weight:500}

/* the signature: a blocky teletext-style health bar built from real data */
.bar{
  margin:12px 0 14px;font-size:13px;letter-spacing:-1px;
  line-height:1;word-break:break-all
}
.bar .on{color:var(--lit)}
.bar .off{color:var(--rule)}
.bar .pct{
  letter-spacing:0;margin-left:10px;font-size:11px;color:var(--dim)
}

.url{display:flex;gap:8px;align-items:stretch}
.url code{
  flex:1 1 auto;background:var(--panel);border:1px solid var(--rule);
  padding:9px 12px;color:var(--amber);font-size:12.5px;
  overflow-x:auto;white-space:nowrap;border-radius:2px
}
button{
  font-family:inherit;font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  background:transparent;color:var(--cyan);border:1px solid var(--cyan);
  padding:0 16px;cursor:pointer;border-radius:2px;transition:background .15s,color .15s
}
button:hover,button:focus-visible{background:var(--cyan);color:var(--bg)}
button:focus-visible{outline:2px solid var(--amber);outline-offset:2px}
button.done{border-color:var(--green);color:var(--green)}
button.done:hover{background:var(--green)}

.note{
  margin-top:10px;font-size:11.5px;color:var(--dim)
}
.note .warn{color:var(--orange)}

footer{
  margin-top:40px;padding-top:20px;border-top:1px solid var(--rule);
  font-size:12px;color:var(--dim)
}
footer a{color:var(--cyan)}
footer p{margin:0 0 8px}

.how{margin-top:38px;border:1px solid var(--rule);padding:20px;border-radius:2px}
.how h2{
  font-family:'Space Mono',monospace;font-size:12px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--cyan);margin:0 0 14px;font-weight:700
}
.how ol{margin:0;padding-left:20px}
.how li{margin-bottom:6px}
.how code{color:var(--amber)}
"""

JS = """
document.querySelectorAll('button[data-url]').forEach(function(b){
  b.addEventListener('click', function(){
    navigator.clipboard.writeText(b.dataset.url).then(function(){
      var old = b.textContent;
      b.textContent = 'Copied';
      b.classList.add('done');
      setTimeout(function(){ b.textContent = old; b.classList.remove('done'); }, 1600);
    });
  });
});
"""


def bar(working, tested, width=34):
    """Teletext-style block bar. Returns (html, colour_var, pct)."""
    pct = (working / tested * 100) if tested else 0
    lit = round(width * pct / 100)
    if pct >= 45:
        colour = "var(--green)"
    elif pct >= 20:
        colour = "var(--orange)"
    else:
        colour = "var(--red)"
    return ("<span class='on'>" + "\u2588" * lit + "</span>"
            "<span class='off'>" + "\u2588" * (width - lit) + "</span>",
            colour, pct)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="docs/status.json")
    ap.add_argument("--out", default="docs/index.html")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = ap.parse_args()

    if not os.path.isfile(args.status):
        print(f"no {args.status} yet - run verify.py first", file=sys.stderr)
        return 1

    with open(args.status, encoding="utf-8") as f:
        st = json.load(f)

    if args.repo and "/" in args.repo:
        owner, name = args.repo.split("/", 1)
        base = f"https://{owner.lower()}.github.io/{name}"
        repo_url = f"https://github.com/{args.repo}"
    else:
        base = "."
        repo_url = "https://github.com/iptv-org/iptv"

    pls = st.get("playlists", [])
    total = sum(p["channels_working"] for p in pls)
    tested = sum(p["streams_tested"] for p in pls)

    rows = []
    for i, p in enumerate(pls):
        b, colour, pct = bar(p["channels_working"], p["streams_tested"])
        url = f"{base}/{p['file']}"
        warn = ("<span class='warn'>Previous version kept &mdash; "
                "verification returned nothing this run.</span>"
                if p.get("kept_previous") else "")
        fails = p.get("top_failures") or {}
        if fails and not warn:
            top = ", ".join(f"{k} ({v})" for k, v in list(fails.items())[:2])
            warn = f"Most common failures: {html.escape(top)}"
        rows.append(f"""
    <li class="row" style="--lit:{colour};animation-delay:{i * 45}ms">
      <div class="top">
        <h2 class="title">{html.escape(p['title'])}</h2>
        <span class="count"><b>{p['channels_working']}</b> working
          &middot; {p['streams_tested']} tested</span>
      </div>
      <div class="bar">{b}<span class="pct">{pct:.0f}%</span></div>
      <div class="url">
        <code>{html.escape(url)}</code>
        <button type="button" data-url="{html.escape(url, quote=True)}">Copy</button>
      </div>
      <p class="note">{warn}</p>
    </li>""")

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verified IPTV playlists</title>
<meta name="description" content="Automatically verified M3U playlists for
 India, Pakistan and international sports. Dead channels removed daily.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Space+Mono:wght@700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

  <header>
    <p class="eyebrow">Rebuilt and retested every day</p>
    <h1>Playlists that only<br>contain channels<br>that <em>actually play</em></h1>
    <p class="sub">Every stream below was fetched and checked for real video
      within the last 24 hours. Dead links are dropped, new channels are picked
      up automatically. Add a link once to your TV and leave it alone.</p>
    <div class="meta">
      <span>Updated <b>{html.escape(st.get('updated_at', 'unknown'))}</b></span>
      <span><b>{total}</b> working channels</span>
      <span><b>{tested}</b> streams tested</span>
    </div>
  </header>

  <ul class="rows">{''.join(rows)}
  </ul>

  <section class="how">
    <h2>Adding one to your TV</h2>
    <ol>
      <li><b>SS IPTV (VIDAA)</b> &mdash; gear icon, <code>Content</code>,
        <code>External playlists</code>, <code>Add</code>. Paste the link, save,
        then fully exit and reopen the app.</li>
      <li><b>Android TV</b> &mdash; in TiviMate, IPTV Smarters or similar, add a
        new playlist and choose the M3U URL option.</li>
      <li><b>VLC</b> &mdash; <code>Ctrl+N</code>, paste, play. Useful for testing
        a link before putting it on the TV.</li>
    </ol>
  </section>

  <footer>
    <p>Channel data comes from
      <a href="https://github.com/iptv-org/iptv">iptv-org</a>, which indexes
      publicly available free-to-air streams. This project only verifies and
      re-groups them; it hosts no video.</p>
    <p>Some streams are geo-restricted and will behave differently depending on
      where you are. Verification runs from GitHub's servers, so your results
      may vary slightly.</p>
    <p><a href="{html.escape(repo_url)}">Source and automation on GitHub</a></p>
  </footer>

</div>
<script>{JS}</script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"wrote {args.out}  ({len(pls)} playlists, {total} channels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
