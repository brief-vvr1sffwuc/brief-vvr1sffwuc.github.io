"""The briefing page itself: turning a state and the news into HTML. It uses only Python's standard
library, because GitHub's servers run this same file to refresh the news between the Mac's
morning runs (see refresh_site.py and the README).

The Mac's daily run makes the state: prices' moves, alerts, official announcements, the market
mood and any AI summaries (briefing.gather), and publishes it as state.json next to the page. The
refresher downloads fresh news, puts it with that state, and rebuilds the page with page().
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:                      # inside the package on the Mac; a plain file next to this one on GitHub
    from . import mood_core
except ImportError:       # pragma: no cover
    import mood_core

UTC_OFFSET = 0      # seconds east of UTC for the times shown (set from the state: your Mac's time zone)

TAGS = {"positive": "pos", "neutral": "neu", "negative": "neg"}
PER_STOCK = 6       # headlines shown per stock
# Law firms' adverts for shareholder lawsuits fill the press-release wires; they aren't news.
ADVERT = re.compile(r"investor alert|shareholder alert|investor action|class action|law firm|law offices|"
                    r"reminds (?:investors|shareholders)|encourages (?:investors|shareholders)|"
                    r"lead plaintiff|securities fraud|investors? (?:with|who) (?:suffered )?losses|deadline", re.I)

# Opinion and stock-tip pieces: about two thirds of the feed. They're kept, folded under the news.
OPINION_PUBLISHERS = ("motley fool", "zacks", "seeking alpha", "investorplace", "24/7 wall")
OPINION_TITLE = re.compile(
    r"^prediction\b|\bprediction:|should you (?:buy|sell)|is (?:it|now) (?:too late|time)|a buy\b|stocks? to (?:buy|sell|own|watch)|"
    r"\bbetter (?:buy|stock)|\bvs\.?\s|here'?s why|here'?s what|could (?:soar|double|make you)|millionaire|"
    r"\bbuy(?:ing)? (?:the dip|now|before)|top (?:\d+ )?(?:stock|pick)|if you(?:'d| had)? invest|my (?:top|favorite)|"
    r"wall street|price target|\bbull(?:ish)?\b|\bbear(?:ish)?\b|\?$", re.I)
PRESS = ("newswire", "business wire", "accesswire")


def kind(row) -> str:
    """news, announcement (the company's own press release) or opinion."""
    publisher = (row["publisher"] or "").lower()
    if any(p in publisher for p in PRESS):
        return "announcement"
    if any(p in publisher for p in OPINION_PUBLISHERS) or OPINION_TITLE.search(row["title"] or ""):
        return "opinion"
    return "news"


def _money(match: re.Match) -> str:
    value = float(match.group(1).replace(",", ""))
    if value >= 1e9:
        return f"${value / 1e9:.1f} billion"
    if value >= 1e6:
        return f"${value / 1e6:.1f} million"
    return f"${value:,.0f}"


def _short_day(match: re.Match) -> str:
    try:
        return datetime.strptime(match.group(0), "%Y-%m-%d").strftime("%d %b").lstrip("0")
    except ValueError:
        return match.group(0)


def friendly(feed: str, summary: str, name: str) -> str:
    """An alert's summary in plainer words: no tier prefix, short dates, round money, and a
    sentence in place of each feed's shorthand."""
    text = re.sub(r"^(?:Watch|Prepare|Act): ", "", summary)
    if feed == "Cloud spending":
        m = re.match(r"^[A-Z]+ FY(\d{4}) Q(\d): (.*)$", text)
        if m:
            text = f"{name}, results for quarter {m.group(2)} of its {m.group(1)} financial year: {m.group(3)}"
    elif feed == "TSMC monthly sales":
        m = re.match(r"^TSMC (\w+ \d{4}) revenue (NT\$[\d.,]+bn) \(([+-]\d+)% on a year ago\).*: (up|steady|down)$", text)
        if m:
            change = int(m.group(3))
            verdict = {"up": "ahead of", "steady": "in line with", "down": "behind"}[m.group(4)]
            text = (f"TSMC's sales in {m.group(1)} were {m.group(2)}, {'up' if change >= 0 else 'down'} {abs(change)}% on a "
                    f"year earlier. That's {verdict} what TSMC told investors to expect for the quarter.")
    elif feed == "Insider buying":
        m = re.match(r"^(\d+) insiders bought (\$[\d,]+) between (\S+) and (\S+): (.*)$", text)
        if m:
            people = ", ".join(" ".join(w.capitalize() if w.isupper() else w for w in person.split())
                               for person in m.group(5).split(", "))
            when = m.group(3) if m.group(3) == m.group(4) else f"{m.group(3)} and {m.group(4)}"
            text = (f"{m.group(1)} people inside the company bought {m.group(2)} of its shares "
                    f"{'on' if m.group(3) == m.group(4) else 'between'} {when}: {people}.")
    text = re.sub(r"\$(\d{1,3}(?:,\d{3})+)(?!\.\d)", _money, text)
    return re.sub(r"\b20\d\d-\d\d-\d\d\b", _short_day, text)


CSS = """
:root { --bg:#f6f4ef; --fg:#1c1c1e; --muted:#6a6a70; --line:#e4e1d8; --card:#ffffff; --soft:#f0ede5;
        --pos:#1a7f4b; --neg:#b3261e; --neu:#77777d; --accent:#2b5c8a; --warn:#b26a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#131315; --fg:#ededf0; --muted:#9c9ca3; --line:#2b2b30; --card:#1c1c20; --soft:#242429;
          --pos:#5fc48a; --neg:#f0857d; --neu:#9c9ca3; --accent:#8ab4e0; --warn:#e0a84a; } }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }
body { background: var(--bg); color: var(--fg); margin: 0; padding: 0 16px 70px;
       font: 17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif; }
main { max-width: 760px; margin: 0 auto; }
header.top { padding: 26px 0 6px; }
h1 { font-size: 30px; line-height: 1.15; margin: 0; letter-spacing: -0.02em; }
h2 { font-size: 21px; margin: 38px 0 4px; letter-spacing: -0.01em; scroll-margin-top: 60px; }
h3 { font-size: 17px; margin: 0; }
p { margin: 8px 0; }
.sub, .muted { color: var(--muted); }
.sub { font-size: 15px; margin: 4px 0 0; }
.small { font-size: 14px; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
nav { position: sticky; top: 0; z-index: 5; background: var(--bg); padding: 10px 0; margin: 8px 0 0;
      display: flex; gap: 8px; overflow-x: auto; border-bottom: 1px solid var(--line); }
nav a { flex: none; font-size: 14px; font-weight: 600; padding: 6px 12px; border-radius: 999px;
        background: var(--soft); color: var(--fg); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; margin: 12px 0; }
.glance ul { margin: 6px 0 0; padding: 0; list-style: none; }
.glance li { padding: 9px 0 9px 30px; border-top: 1px solid var(--line); position: relative; }
.glance li:first-child { border-top: 0; }
.glance li::before { content: attr(data-icon); position: absolute; left: 0; top: 8px; }
.tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.tile .big { font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }
.tile .name { font-weight: 600; }
.up { color: var(--pos); } .down { color: var(--neg); }
.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.gauge { position: relative; height: 14px; border-radius: 999px; margin: 14px 0 6px;
         background: linear-gradient(90deg, #2e7d5b 0%, #8fbf9f 20%, #cfcabc 20%, #cfcabc 80%, #e0a07a 80%, #b3402e 100%); }
.gauge i { position: absolute; top: -5px; width: 6px; height: 24px; margin-left: -3px; border-radius: 3px;
           background: var(--fg); border: 1px solid var(--card); }
.scale { display: flex; justify-content: space-between; font-size: 13px; color: var(--muted); }
.alert { border-left: 5px solid var(--warn); }
.chip { display: inline-block; font-size: 12px; font-weight: 700; padding: 1px 8px; border-radius: 999px;
        border: 1px solid currentColor; white-space: nowrap; }
.pos { color: var(--pos); } .neg { color: var(--neg); } .neu { color: var(--neu); } .warn { color: var(--warn); }
.row { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
.movers { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.movers div.line { display: flex; justify-content: space-between; gap: 8px; padding: 5px 0; border-top: 1px solid var(--line); }
.movers div.line:first-of-type { border-top: 0; }
details.stock { padding: 0; }
details.stock > summary { list-style: none; cursor: pointer; padding: 14px 16px; }
details.stock > summary::-webkit-details-marker { display: none; }
details.stock > summary .name { font-weight: 700; }
details.stock > summary .more { float: right; color: var(--muted); font-size: 14px; }
details.stock[open] > summary .more::after { content: "Hide"; }
details.stock:not([open]) > summary .more::after { content: "Show"; }
.tone { display: flex; height: 6px; border-radius: 3px; overflow: hidden; margin-top: 10px; background: var(--soft); }
.tone span { display: block; height: 100%; }
.tone .p { background: var(--pos); } .tone .u { background: var(--neu); opacity: .45; } .tone .n { background: var(--neg); }
ul.news { list-style: none; margin: 0; padding: 0 16px 8px; }
ul.news li { padding: 11px 0; border-top: 1px solid var(--line); }
ul.news .title { font-weight: 600; display: block; margin: 3px 0 2px; }
.why { color: var(--muted); font-size: 14.5px; line-height: 1.45; display: block; }
details.help { margin: 6px 0 0; font-size: 14.5px; color: var(--muted); }
details.help summary { cursor: pointer; color: var(--accent); font-weight: 600; }
details.help p { margin: 6px 0; }
dl { margin: 6px 0; } dt { font-weight: 700; color: var(--fg); margin-top: 8px; } dd { margin: 0; }
footer { margin-top: 36px; font-size: 14px; color: var(--muted); }
button { font: inherit; color: inherit; }
.form { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
.form select, .form input, textarea { font: inherit; color: var(--fg); background: var(--bg); border: 1px solid var(--line);
  border-radius: 10px; padding: 9px 10px; width: 100%; min-width: 0; }
.form select:first-child, .form input[type=text], .form .add { grid-column: 1 / -1; }
.x { background: none; border: 0; color: var(--muted); font-size: 18px; cursor: pointer; padding: 0 0 0 8px; }
.totals { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 10px 0; }
.totals div { background: var(--soft); border-radius: 10px; padding: 8px 10px; } .totals b { display: block; font-size: 20px; }
.caution { border-left: 4px solid var(--warn); padding: 6px 10px; margin: 8px 0; font-size: 15px; }
.add { background: var(--accent); color: #fff; border: 0; border-radius: 999px; padding: 8px 16px; font-weight: 700; cursor: pointer; }
@media (prefers-color-scheme: dark) { .add { color: #10161d; } }
.star { float: right; background: none; border: 0; font-size: 22px; line-height: 1; padding: 0 0 0 10px; cursor: pointer; color: var(--muted); }
.star.on { color: #d9a400; }
.mine div.line { display: flex; justify-content: space-between; gap: 10px; padding: 9px 0; border-top: 1px solid var(--line); }
.mine div.line:first-child { border-top: 0; }
.picker { display: none; max-height: 340px; overflow-y: auto; margin-top: 10px; border-top: 1px solid var(--line); }
.picker.open { display: block; }
.picker label { display: flex; gap: 10px; align-items: center; padding: 9px 0; border-bottom: 1px solid var(--line); }
.picker input { width: 20px; height: 20px; }
.official { margin: 0 16px 10px; } .official b { font-size: 13px; color: var(--muted); letter-spacing: .03em; text-transform: uppercase; }
.official ul { list-style: none; margin: 4px 0 0; padding: 0; } .official li { padding: 6px 0; border-top: 1px solid var(--line); }
.inshort { margin: 0 16px 10px; padding: 10px 12px; background: var(--soft); border-radius: 10px; }
.inshort b { display: block; font-size: 13px; color: var(--muted); letter-spacing: .03em; text-transform: uppercase; }
details.fold { margin: 0 16px 12px; } details.fold > summary { cursor: pointer; color: var(--accent); font-weight: 600; font-size: 15px; padding: 6px 0; }
details.fold ul.news { padding: 0; }
@media (max-width: 520px) { .tiles, .movers { grid-template-columns: 1fr; } h1 { font-size: 26px; } }
"""


def _pct(x: float | None) -> str:
    if x is None:
        return '<span class="muted">-</span>'
    return f'<span class="num {"up" if x > 0 else "down" if x < 0 else ""}">{x:+.1%}</span>'


def _local(utc_text: str) -> str:
    try:
        moment = datetime.strptime(utc_text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return utc_text[:16]
    return (moment + timedelta(seconds=UTC_OFFSET)).strftime("%a %d %b, %H:%M")


def _link(title: str, url: str | None) -> str:
    text = html.escape(title)
    if url and url.startswith(("https://", "http://")):
        return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{text}</a>'
    return text


LAYERS = {"accelerators": "AI chip maker", "chip_equipment": "Chip-making equipment", "cloud_spender": "Big cloud company",
          "data_centers": "Data-centre builder or owner", "foundry": "Chip factory", "memory_storage": "Memory and storage",
          "neocloud": "AI cloud provider", "networking": "Networking gear", "optical": "Optical links",
          "power_cooling": "Power and cooling", "power_supply": "Electricity supplier", "servers": "Server maker"}
MEANING = {"Cloud spending": "A big cloud company reported how much it plans to spend on data centres. That money goes to "
                             "the chip, server, power and network companies on your list.",
           "TSMC monthly sales": "TSMC makes most of the world's advanced AI chips. Its monthly sales show how strong chip "
                                 "demand is.",
           "Insider buying": "Several of a company's own directors or executives bought its shares with their own money.",
           "Export controls": "A new US rule on which chips or chip-making tools may be sold abroad, mainly to China."}


SCRIPT = """
(function () {
  var KEY = 'briefing.mine', mine = [];
  try { mine = JSON.parse(localStorage.getItem(KEY) || '[]').filter(function (t) { return STOCKS[t]; }); }
  catch (e) { document.getElementById('mine-note').textContent = "This browser can't remember your choice, so it resets each time."; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(mine)); } catch (e) {} }
  function pct(x) {
    if (x === null || x === undefined) return '<span class="muted">-</span>';
    return '<span class="num ' + (x > 0 ? 'up' : x < 0 ? 'down' : '') + '">' + (x > 0 ? '+' : '') + (x * 100).toFixed(1) + '%</span>';
  }
  function esc(t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
  function draw() {
    var list = document.getElementById('mine-list'), html = '';
    mine.forEach(function (t) {
      var s = STOCKS[t], card = document.getElementById('stock-' + t);
      html += '<div class="line"><span><b>' + esc(s.name) + '</b> <span class="muted small">' + esc(t) + '</span><br>' +
        '<span class="small muted">' + (s.news ? '<a href="#stock-' + t + '">' + esc(s.news) + '</a>' : 'no headline news') +
        '</span></span><span class="small" style="text-align:right">' + pct(s.d1) + '<br><span class="muted">5 days </span>' + pct(s.d5) + '</span></div>';
      if (card) { card.open = true; }
    });
    list.innerHTML = html;
    document.getElementById('mine-empty').style.display = mine.length ? 'none' : '';
    var first = document.querySelector('details.stock');
    mine.slice().reverse().forEach(function (t) {
      var card = document.getElementById('stock-' + t);
      if (card && first && card.parentNode) { card.parentNode.insertBefore(card, document.querySelector('details.stock')); }
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-star]'), function (b) {
      var on = mine.indexOf(b.getAttribute('data-star')) >= 0;
      b.textContent = on ? '★' : '☆'; b.className = 'star' + (on ? ' on' : '');
      b.setAttribute('aria-label', on ? 'Remove from my stocks' : 'Add to my stocks');
    });
    Array.prototype.forEach.call(document.querySelectorAll('#mine-picker input'), function (i) { i.checked = mine.indexOf(i.value) >= 0; });
  }
  function toggle(t) { var i = mine.indexOf(t); if (i >= 0) { mine.splice(i, 1); } else { mine.push(t); } save(); draw(); }
  var picker = document.getElementById('mine-picker');
  picker.innerHTML = Object.keys(STOCKS).sort(function (a, b) { return STOCKS[a].name.localeCompare(STOCKS[b].name); }).map(function (t) {
    return '<label><input type="checkbox" value="' + t + '"><span>' + esc(STOCKS[t].name) + ' <span class="muted small">' + esc(t) + ' · ' + esc(STOCKS[t].what) + '</span></span></label>';
  }).join('');
  picker.addEventListener('change', function (ev) { if (ev.target.value) { toggle(ev.target.value); } });
  document.getElementById('mine-add').addEventListener('click', function () {
    picker.classList.toggle('open');
    this.textContent = picker.classList.contains('open') ? 'Done' : '+ Add or remove companies';
  });
  document.addEventListener('click', function (ev) {
    var t = ev.target.getAttribute && ev.target.getAttribute('data-star');
    if (t) { ev.preventDefault(); ev.stopPropagation(); toggle(t); }
  });
  // ---- holdings and decisions (kept in this browser only)
  function load(key) { try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch (e) { return []; } }
  function store(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) {} }
  var holds = load('briefing.holdings').filter(function (h) { return STOCKS[h.t]; });
  var journal = load('briefing.journal');
  function money(x) { return (x < 0 ? '-$' : '$') + Math.abs(x).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  function options(id) {
    document.getElementById(id).innerHTML = Object.keys(STOCKS).sort(function (a, b) { return STOCKS[a].name.localeCompare(STOCKS[b].name); })
      .map(function (t) { return '<option value="' + t + '">' + esc(STOCKS[t].name) + ' (' + esc(t) + ')</option>'; }).join('');
  }
  function drawHoldings() {
    var total = 0, before = 0, paid = 0, html = '', byType = {}, warn = '';
    holds.forEach(function (h) { var s = STOCKS[h.t]; if (s.close) { total += h.n * s.close; } });
    holds.forEach(function (h, i) {
      var s = STOCKS[h.t];
      if (!s.close) { html += '<div class="line"><span><b>' + esc(s.name) + '</b><br><span class="small muted">no price yet</span></span><button class="x" data-drop-hold="' + i + '" aria-label="Remove">×</button></div>'; return; }
      var value = h.n * s.close, was = s.d1 === null || s.d1 === undefined ? value : value / (1 + s.d1);
      before += was; paid += h.n * h.p; byType[s.what] = (byType[s.what] || 0) + value;
      html += '<div class="line"><span><b>' + esc(s.name) + '</b> <span class="muted small">' + h.n + ' shares · ' + (100 * value / total).toFixed(0) + '% of your holdings</span><br>' +
        '<span class="small">Worth ' + money(value) + ' · since you bought ' + pct(h.p ? s.close / h.p - 1 : null) + ' (' + money(value - h.n * h.p) + ')</span></span>' +
        '<span class="small" style="text-align:right;white-space:nowrap">' + pct(s.d1) + '<button class="x" data-drop-hold="' + i + '" aria-label="Remove">×</button></span></div>';
      if (value / total > 0.3 && holds.length > 1) { warn += '<div class="caution">' + esc(s.name) + ' is ' + (100 * value / total).toFixed(0) + '% of your holdings. A big fall in one stock would hit you hard.</div>'; }
    });
    Object.keys(byType).forEach(function (k) {
      if (byType[k] / total > 0.5 && Object.keys(byType).length > 1) { warn += '<div class="caution">' + (100 * byType[k] / total).toFixed(0) + '% of your holdings are in one kind of company (' + esc(k.toLowerCase()) + '). They tend to rise and fall together.</div>'; }
    });
    document.getElementById('hold-list').innerHTML = html;
    document.getElementById('hold-warn').innerHTML = warn;
    document.getElementById('hold-total').innerHTML = total ? '<div class="totals"><div><span class="small muted">Worth now</span><b>' + money(total) + '</b></div>' +
      '<div><span class="small muted">On ' + esc(MARKET.label || '') + '</span><b>' + pct(before ? total / before - 1 : null) + '</b><span class="small muted">' + money(total - before) + ' · big tech ' + pct(MARKET.qqq_d1) + '</span></div>' +
      '<div><span class="small muted">You paid</span><b>' + money(paid) + '</b></div><div><span class="small muted">Gain or loss so far</span><b>' + pct(paid ? total / paid - 1 : null) + '</b><span class="small muted">' + money(total - paid) + '</span></div></div>' : '';
  }
  function drawJournal() {
    document.getElementById('journal-list').innerHTML = journal.map(function (j, i) {
      var s = STOCKS[j.t], since = '';
      if (s && s.close && j.p) {
        since = '<br><span class="small">Since then: ' + esc(s.name) + ' ' + pct(s.close / j.p - 1) + (j.q && MARKET.qqq ? ' · big tech ' + pct(MARKET.qqq / j.q - 1) : '') + '</span>';
      }
      return '<div class="line"><span><b>' + esc(j.a) + ' ' + esc(s ? s.name : j.t) + '</b> <span class="muted small">' + esc(j.d) + '</span><br>' +
        '<span class="small">' + esc(j.note || '') + '</span>' + since + '</span><button class="x" data-drop-journal="' + i + '" aria-label="Remove">×</button></div>';
    }).join('') || '<p class="muted small">No decisions written down yet.</p>';
  }
  function drawBackup() {
    document.getElementById('backup-text').value = JSON.stringify({ mine: mine, holdings: holds, journal: journal });
  }
  function drawComing() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-coming]'), function (row) {
      var t = row.getAttribute('data-coming'), owned = holds.some(function (h) { return h.t === t; });
      if ((owned || mine.indexOf(t) >= 0) && row.firstChild.textContent.indexOf('★') < 0) { row.firstChild.textContent = '★ ' + row.firstChild.textContent; }
    });
  }
  function redraw() { draw(); drawHoldings(); drawJournal(); drawBackup(); drawComing(); }
  options('hold-ticker'); options('journal-ticker');
  document.getElementById('hold-add').addEventListener('click', function () {
    var t = document.getElementById('hold-ticker').value, n = parseFloat(document.getElementById('hold-shares').value),
      p = parseFloat(document.getElementById('hold-cost').value);
    if (!STOCKS[t] || !(n > 0) || !(p > 0)) { return; }
    holds.push({ t: t, n: n, p: p }); store('briefing.holdings', holds);
    if (mine.indexOf(t) < 0) { mine.push(t); save(); }
    document.getElementById('hold-shares').value = ''; document.getElementById('hold-cost').value = '';
    redraw();
  });
  document.getElementById('journal-add').addEventListener('click', function () {
    var t = document.getElementById('journal-ticker').value, note = document.getElementById('journal-note').value.trim();
    if (!STOCKS[t] || !note) { return; }
    journal.unshift({ d: new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }), t: t, a: document.getElementById('journal-action').value, note: note.slice(0, 300),
      p: STOCKS[t].close || null, q: MARKET.qqq || null });
    store('briefing.journal', journal); document.getElementById('journal-note').value = ''; redraw();
  });
  document.addEventListener('click', function (ev) {
    var a = ev.target.getAttribute && ev.target.getAttribute('data-drop-hold'), b = ev.target.getAttribute && ev.target.getAttribute('data-drop-journal');
    if (a !== null && a !== undefined) { holds.splice(parseInt(a, 10), 1); store('briefing.holdings', holds); redraw(); }
    if (b !== null && b !== undefined) { journal.splice(parseInt(b, 10), 1); store('briefing.journal', journal); redraw(); }
  });
  document.getElementById('backup-load').addEventListener('click', function () {
    var note = document.getElementById('backup-note');
    try {
      var data = JSON.parse(document.getElementById('backup-text').value);
      mine = (data.mine || []).filter(function (t) { return STOCKS[t]; });
      holds = (data.holdings || []).filter(function (h) { return STOCKS[h.t] && h.n > 0 && h.p > 0; });
      journal = (data.journal || []).filter(function (j) { return j && j.t && j.a; });
      save(); store('briefing.holdings', holds); store('briefing.journal', journal); redraw();
      note.textContent = 'Loaded.';
    } catch (e) { note.textContent = "That text isn't a backup from this page."; }
  });
  var toggleMine = toggle;
  toggle = function (t) { toggleMine(t); drawBackup(); drawComing(); };
  redraw();
})();
"""


def _day(text: str) -> str:
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%a %d %b").replace(" 0", " ")
    except ValueError:
        return text


def _moved(x: float | None) -> str:
    if x is None:
        return "didn't trade"
    return ("rose " if x > 0 else "fell " if x < 0 else "was flat at ") + f"{abs(x):.1%}"


def _tone(found: list) -> tuple[dict[str, int], str]:
    count = {t: sum(1 for r in found if r["sentiment"] == t) for t in TAGS}
    scored = sum(count.values())
    if not scored:
        return count, "no tone tags"
    if count["positive"] >= 0.6 * scored:
        return count, "mostly positive"
    if count["negative"] >= 0.5 * scored:
        return count, "mostly negative"
    return count, "mixed"


def page(state: dict, news_by: dict[str, list[dict]], adverts: int, newest: str | None,
         published: bool = False, updated: str | None = None) -> tuple[str, dict]:
    """The page from a state (everything except the news: gather() on the Mac makes it, and it
    travels as state.json) and the news rows. Returns the page and counts."""
    global UTC_OFFSET
    UTC_OFFSET = int(state.get("utc_offset", 0))
    moves = {t: {(int(k) if k.isdigit() else k): v for k, v in m.items()} for t, m in state["moves"].items()}
    companies = state["companies"]
    name_of = {c["ticker"]: c["name"] for c in companies}
    alerts = state["alerts"]
    mood_now = state.get("mood")
    short = state.get("short") or {}
    filed = state.get("filed") or {}
    e = html.escape
    last_day = _day(state["last_day"]) if state.get("last_day") else "-"
    ranked = sorted(((moves[c["ticker"]][1], c) for c in companies
                     if c["ticker"] in moves and moves[c["ticker"]].get(1) is not None), key=lambda x: x[0])
    shown = {c["ticker"] for c in companies if c["ticker"] in news_by or c["ticker"] in filed}
    busiest = sorted(((t, news_by.get(t, [])) for t in shown),
                     key=lambda kv: (-len(filed.get(kv[0], [])) - sum(1 for r in kv[1] if kind(r) != "opinion"),
                                     -len(kv[1]), kv[0]))
    most = max(news_by.items(), key=lambda kv: len(kv[1]), default=None)
    today = state.get("today") or ""
    coming = [c for c in state.get("coming") or [] if c["day"] >= today]

    # ---- at a glance
    glance = []
    qqq, smh = moves.get("QQQ"), moves.get("SMH")
    if qqq and smh:
        glance.append(("📈", f"On {last_day}, chip stocks {_moved(smh[1])} and big tech stocks {_moved(qqq[1])}."))
    if mood_now:
        m = mood_now
        feel = {"Fear": "investors are nervous", "Greed": "investors are very confident", "Normal": "nothing unusual"}[m['label']]
        glance.append(("🧭", f"The market's mood is <b>{m["label"]}</b> ({m["score"]:.0f} of 100): {feel}."))
    if alerts:
        glance.append(("🔔", f"<b>{len(alerts)} alert{'s' if len(alerts) != 1 else ''}</b> in the last "
                             f"{state['alert_days']} days. The newest: {e(alerts[0]['feed'].lower())}, "
                             f"{e(name_of.get(alerts[0]['who'], alerts[0]['who']))} ({_day(alerts[0]['day'])})."))
    else:
        glance.append(("🔔", f"No alerts in the last {state['alert_days']} days."))
    if ranked and ranked[-1][0] > 0 and ranked[0][0] < 0:
        up, down = ranked[-1], ranked[0]
        glance.append(("↕️", f"Biggest riser on your list: {e(up[1]['name'])} (+{up[0]:.1%}). "
                             f"Biggest faller: {e(down[1]['name'])} ({down[0]:.1%})."))
    if coming:
        first = coming[0]
        when = "today" if first["day"] == today else "on " + _day(first["day"])
        more = f", and {len(coming) - 1} more in the next two weeks" if len(coming) > 1 else ""
        glance.append(("🗓️", f"Coming up: {e(first['what'])} {when}{more}."))
    n_filed = sum(len(v) for v in filed.values())
    if n_filed:
        glance.append(("📄", f"<b>{n_filed} official announcement{'s' if n_filed != 1 else ''}</b> from {len(filed)} of your "
                             f"companies in the last {state['filing_days']} days."))
    if most:
        ticker, found = most
        glance.append(("📰", f"Most written about: {e(name_of.get(ticker, ticker))}, {len(found)} articles, "
                             f"{_tone(found)[1]}."))
    parts = ["<header class='top'><h1>Your morning briefing</h1>"
             f"<p class='sub'>{e(updated or state.get('made', ''))} · prices up to {last_day}"
             f"{' · news up to ' + e(_local(newest)) if newest else ''}</p></header>",
             "<nav><a href='#glance'>At a glance</a><a href='#coming'>Coming up</a><a href='#mine'>My stocks</a><a href='#market'>Market</a><a href='#alerts'>Alerts</a>"
             "<a href='#movers'>Movers</a><a href='#news'>News</a><a href='#words'>Words explained</a></nav>",
             "<h2 id='glance'>At a glance</h2><div class='card glance'><ul>"
             + "".join(f"<li data-icon='{icon}'>{text}</li>" for icon, text in glance) + "</ul></div>",
             "<p class='small muted'>This page helps you stay informed. It doesn't tell you to buy or sell: none of the "
             "ideas tested so far beat the market once the news was public.</p>"]

    # ---- coming up
    parts.append("<h2 id='coming'>Coming up</h2><p class='sub'>Dates in the next two weeks that often move these stocks.</p>"
                 "<div class='card mine'>")
    if not coming:
        parts.append("<p class='muted'>Nothing scheduled in the next two weeks.</p>")
    for c in coming:
        when = "Today" if c["day"] == today else _day(c["day"])
        star = f" data-coming='{e(c['ticker'])}'" if c.get("ticker") else ""
        parts.append(f"<div class='line'{star}><span>{e(c['what'])}</span><span class='small muted num'>{when}</span></div>")
    parts.append("</div><details class='help'><summary>Why do these dates matter?</summary><p>A company's results day is when "
                 "its share price usually moves most, up or down, and suppliers often move with their big customers. "
                 "Results dates are estimates until the company confirms them, and they can change. Knowing a date is "
                 "coming is a reason to expect bigger swings, not a reason to buy or sell.</p></details>")

    # ---- my stocks (chosen in the page with the + button; remembered by this browser)
    parts.append("<h2 id='mine'>My stocks</h2><p class='sub'>The companies you own or care most about, shown first.</p>"
                 "<div class='card'><div id='mine-list' class='mine'></div>"
                 "<p id='mine-empty' class='muted'>None chosen yet. Tap the button to pick your companies.</p>"
                 "<button class='add' id='mine-add' type='button'>+ Add or remove companies</button>"
                 "<div class='picker' id='mine-picker'></div>"
                 "<p class='small muted' id='mine-note'>Your choice is remembered on this device only.</p></div>"
                 "<div class='card'><h3>My holdings</h3><p class='small muted'>What you own. It stays on this device and is "
                 "never uploaded.</p><div id='hold-total'></div><div id='hold-list' class='mine'></div><div id='hold-warn'></div>"
                 "<div class='form'><select id='hold-ticker' aria-label='Company'></select>"
                 "<input id='hold-shares' type='number' inputmode='decimal' min='0' step='any' placeholder='Shares'>"
                 "<input id='hold-cost' type='number' inputmode='decimal' min='0' step='any' placeholder='Price paid ($)'>"
                 "<button class='add' id='hold-add' type='button'>+ Add holding</button></div></div>"
                 "<div class='card'><h3>My decisions</h3><p class='small muted'>Each time you buy, sell or decide to wait, "
                 "write one line on why. Later, this shows how the decision turned out. It stays on this device.</p>"
                 "<div id='journal-list' class='mine'></div>"
                 "<div class='form'><select id='journal-ticker' aria-label='Company'></select>"
                 "<select id='journal-action' aria-label='What you did'><option>Bought</option><option>Sold</option>"
                 "<option>Decided to wait</option></select>"
                 "<input id='journal-note' type='text' maxlength='300' placeholder='Why? One line.'>"
                 "<button class='add' id='journal-add' type='button'>+ Add decision</button></div>"
                 "<details class='help'><summary>Back up or move my data</summary><p>Your stocks, holdings and decisions "
                 "live only in this browser. Copy the text below somewhere safe now and then. To restore or move to another "
                 "device, paste it here and tap Load.</p><textarea id='backup-text' rows='4'></textarea>"
                 "<button class='add' id='backup-load' type='button'>Load</button> <span id='backup-note' class='small muted'>"
                 "</span></details></div>")

    # ---- the market
    parts.append("<h2 id='market'>The market</h2><p class='sub'>How the wider market moved, as background for your stocks.</p>"
                 "<div class='tiles'>")
    for ticker, label, what in (("SMH", "Chip stocks", "a fund of the biggest chip companies (SMH)"),
                                ("QQQ", "Big tech stocks", "the 100 biggest Nasdaq companies (QQQ)")):
        m = moves.get(ticker)
        if m:
            parts.append(f"<div class='card tile'><div class='name'>{label}</div><div class='big'>{_pct(m[1])}</div>"
                         f"<div class='small muted'>on {last_day} · {what}</div>"
                         f"<div class='small'>Last 5 days {_pct(m[5])} · last 20 days {_pct(m[20])}</div></div>")
    parts.append("</div>" + _mood_card(mood_now))

    # ---- alerts
    parts.append(f"<h2 id='alerts'>Alerts</h2><p class='sub'>Things that changed at the companies you follow, in the last "
                 f"{state['alert_days']} days. Newest first.</p>")
    if not alerts:
        parts.append("<div class='card'>Nothing new. That's normal: most days have no alert.</div>")
    for a in alerts:
        summary = friendly(a["feed"], a["summary"], name_of.get(a["who"], a["who"]))
        parts.append(f"<div class='card alert'><div class='row'><h3>{e(a['feed'])}: {e(name_of.get(a['who'], a['who']))}</h3>"
                     f"<span class='chip warn'>{e(a['tier'])}</span><span class='muted small'>{_day(a['day'])}</span></div>"
                     f"<p>{e(summary)}</p><p class='why'>{e(MEANING.get(a['feed'], ''))}</p></div>")
    parts.append("<details class='help'><summary>What do Watch, Prepare and Act mean?</summary>"
                 "<p><b>Watch:</b> worth knowing about; nothing to do. <b>Prepare:</b> a spending plan changed by a real amount, "
                 "so it's worth reading in full. <b>Act:</b> switched off, because no signal has passed its test yet.</p>"
                 "<p>To read a cloud-spending alert in full, run <code>uv run event-model alerts --show TICKER</code> on your Mac.</p>"
                 "</details>")

    # ---- movers
    if ranked:
        parts.append(f"<h2 id='movers'>Biggest movers</h2><p class='sub'>The stocks on your list that moved most on {last_day}."
                     "</p><div class='movers'>")
        for label, group in (("Rose most", [x for x in reversed(ranked[-5:]) if x[0] > 0]),
                             ("Fell most", [x for x in ranked[:5] if x[0] < 0])):
            lines = "".join(f"<div class='line'><span>{e(c['name'])} <span class='muted small'>{e(c['ticker'])}</span></span>"
                            f"{_pct(change)}</div>" for change, c in group) or "<div class='line muted'>None</div>"
            parts.append(f"<div class='card'><h3>{label}</h3>{lines}</div>")
        parts.append("</div>")

    # ---- news
    parts.append(f"<h2 id='news'>News about your stocks</h2><p class='sub'>Official announcements from the last "
                 f"{state['filing_days']} days, and articles from the last {state['news_days']} "
                 "days whose headline names the company. Tap a company to show or hide its articles, and the star to add it to "
                 "My stocks.</p>"
                 "<details class='help'><summary>What do the coloured tags mean?</summary><p>An AI read each article when it "
                 "was published and tagged its tone about the company: <span class='chip pos'>positive</span> "
                 "<span class='chip neu'>neutral</span> <span class='chip neg'>negative</span>. Real news and company "
                 "announcements come first. Opinion pieces and stock tips are folded underneath: there are many of them, "
                 "they lean positive, and they often explain a price move that already happened. Use all of it to "
                 "understand what's going on, not as a reason to trade.</p></details>")
    quiet = sorted(c["ticker"] for c in companies if c["ticker"] not in shown)

    def item(r) -> str:
        tag = TAGS.get(r["sentiment"])
        label = f"<span class='chip {tag}'>{e(r['sentiment'])}</span> " if tag else ""
        what = " · company announcement" if kind(r) == "announcement" else ""
        reason = (r["reasoning"] or "")
        reason = reason[:220].rsplit(" ", 1)[0] + "…" if len(reason) > 220 else reason
        return (f"<li>{label}<span class='muted small'>{e(r['publisher'] or '')}{what} · {e(_local(r['published_utc']))}</span>"
                f"<span class='title'>{_link(r['title'], r['url'])}</span>"
                + (f"<span class='why'>{e(reason)}</span>" if reason else "") + "</li>")

    page_data = {}
    for position, (ticker, found) in enumerate(busiest):
        c = next(c for c in companies if c["ticker"] == ticker)
        m = moves.get(ticker, {})
        count, words = _tone(found)
        scored = sum(count.values()) or 1
        bar = "".join(f"<span class='{cls}' style='width:{100 * count[t] / scored:.0f}%'></span>"
                      for t, cls in (("positive", "p"), ("neutral", "u"), ("negative", "n")))
        real = [r for r in found if kind(r) != "opinion"]
        opinions = [r for r in found if kind(r) == "opinion"]
        filings = filed.get(ticker, [])
        bits = ([f"{len(filings)} official"] if filings else []) + ([f"{len(real)} news"] if real else []) \
            + ([f"{len(opinions)} opinion" + ("s" if len(opinions) != 1 else "")] if opinions else [])
        mix = ", ".join(bits)
        page_data[ticker] = "; ".join(
            ([f"{len(filings)} official announcement{'s' if len(filings) != 1 else ''}"] if filings else [])
            + ([f"{len(found)} article{'s' if len(found) != 1 else ''}, {words}"] if found else []))
        opened = position < 2 or ticker in short
        parts.append(f"<details class='card stock' id='stock-{e(ticker)}' data-ticker='{e(ticker)}'{' open' if opened else ''}>"
                     f"<summary><button class='star' type='button' data-star='{e(ticker)}' aria-label='Add to my stocks'>☆</button>"
                     f"<span class='more'></span><span class='name'>{e(c['name'])}</span> <span class='muted small'>{e(ticker)} · "
                     f"{e(LAYERS.get(c['layer'], c['layer'].replace('_', ' ')))}</span>"
                     f"<div class='small'>{last_day}: {_pct(m.get(1))} · 5 days: {_pct(m.get(5))} · "
                     f"<span class='muted'>{mix}{'; ' + words if found else ''}</span></div>"
                     + (f"<div class='tone'>{bar}</div>" if found else "") + "</summary>")
        if filings:
            parts.append("<div class='official'><b>Official announcements</b><ul>" + "".join(
                f"<li><span class='muted small'>{_day(f['day'])}</span> {_link(f['what'], f['url'])}"
                + (f"<span class='why'>{e(f['short'])}</span>" if f.get("short") else "") + "</li>"
                for f in filings[:PER_STOCK]) + "</ul></div>")
        if ticker in short:
            parts.append(f"<div class='inshort'><b>In short (written by AI from the articles below)</b>{e(short[ticker])}</div>")
        if real:
            parts.append("<ul class='news'>" + "".join(item(r) for r in real[:PER_STOCK]))
            if len(real) > PER_STOCK:
                parts.append(f"<li class='muted small'>and {len(real) - PER_STOCK} more news articles</li>")
            parts.append("</ul>")
        if opinions:
            parts.append(f"<details class='fold'{'' if real else ' open'}><summary>{len(opinions)} opinion piece"
                         f"{'s' if len(opinions) != 1 else ''} and stock tips</summary><ul class='news'>"
                         + "".join(item(r) for r in opinions[:PER_STOCK])
                         + (f"<li class='muted small'>and {len(opinions) - PER_STOCK} more</li>" if len(opinions) > PER_STOCK else "")
                         + "</ul></details>")
        parts.append("</details>")
    if quiet:
        parts.append(f"<p class='small muted'>No announcements or headline news in this period for: {e(', '.join(quiet))}.</p>")
    if adverts:
        parts.append(f"<p class='small muted'>{adverts} law-firm adverts for shareholder lawsuits were left out.</p>")

    # ---- words explained
    parts.append("<h2 id='words'>Words explained</h2><div class='card'><dl>"
                 "<dt>Alert</dt><dd>A note that something changed at a company you follow. It's information, not advice.</dd>"
                 "<dt>Market mood</dt><dd>A score from 0 (extreme fear) to 100 (extreme greed). It combines how nervous option "
                 "traders are (the VIX) with how far big tech stocks have run above their usual level.</dd>"
                 "<dt>VIX</dt><dd>The \"fear index\". It rises when investors expect big swings in the stock market.</dd>"
                 "<dt>Chip stocks (SMH)</dt><dd>A fund holding the biggest chip companies, such as Nvidia and TSMC. It shows how "
                 "the chip industry did as a whole.</dd>"
                 "<dt>Big tech stocks (QQQ)</dt><dd>A fund holding the 100 biggest companies on the Nasdaq exchange.</dd>"
                 "<dt>Supplier</dt><dd>A company that sells to another. When Microsoft spends more on data centres, its suppliers "
                 "(chips, servers, power, cooling) get the orders.</dd>"
                 "<dt>Context only</dt><dd>Shown to help you understand the day. It was tested and didn't predict prices.</dd>"
                 "<dt>Official announcement</dt><dd>A filing the company must make with the US regulator (the SEC) when "
                 "something important happens: results, a big deal, a change of boss. It's the company's own statement, "
                 "so it's fact rather than opinion. Tap one to read the filing.</dd>"
                 "<dt>In short</dt><dd>A few sentences an AI wrote from that company's articles, for the companies you chose. "
                 "It can miss things; the articles are underneath.</dd>"
                 "</dl></div>")
    chosen = state.get('chosen') or []
    parts.append("<footer>" + ("" if published else
                                "<p>AI summaries are on for: " + (e(", ".join(chosen)) if chosen else "no companies yet")
                                + ". To choose, run on your Mac: <code>uv run event-model briefing --summaries NVDA MSFT</code></p>")
                 + "<p>Made by your event model on your Mac. It informs your own decisions; it isn't financial advice.</p></footer>")
    data = {c["ticker"]: {"name": c["name"], "what": LAYERS.get(c["layer"], c["layer"].replace("_", " ")),
                          "close": moves.get(c["ticker"], {}).get("close"),
                          "d1": moves.get(c["ticker"], {}).get(1), "d5": moves.get(c["ticker"], {}).get(5),
                          "news": page_data.get(c["ticker"], "")} for c in companies}
    market = {"day": state.get("last_day"), "label": last_day, "qqq": moves.get("QQQ", {}).get("close"),
              "qqq_d1": moves.get("QQQ", {}).get(1)}
    parts.append("<script>const STOCKS = " + json.dumps(data).replace("</", "<\\/") + "; const MARKET = "
                 + json.dumps(market) + ";" + SCRIPT + "</script>")

    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta name='color-scheme' content='light dark'><meta name='robots' content='noindex, nofollow'>"
            "<meta name='apple-mobile-web-app-title' content='Briefing'>"
            f"<title>Morning briefing</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")
    counts = {"alerts": len(alerts), "stocks": len(shown), "filings": n_filed, "articles": sum(len(v) for v in news_by.values()),
              "summaries": len(short)}
    return page, counts


def _mood_card(m: dict | None) -> str:
    if not m:
        return ""
    colour = "neg" if m["label"] == "Greed" else "pos" if m["label"] == "Fear" else "neu"
    plain = {"Fear": "Investors are nervous. Prices often swing more on days like this.",
             "Greed": "Investors are very confident. Prices have run up a long way.",
             "Normal": "Nothing unusual in how investors feel."}[m["label"]]
    return (f"<div class='card'><div class='row'><h3>Market mood: <span class='{colour}'>{html.escape(m['label'])}</span></h3>"
            f"<span class='muted small'>{m['score']:.0f} of 100 · {_day(m['day'])}</span></div>"
            f"<div class='gauge'><i style='left:{min(max(m['score'], 1), 99):.0f}%'></i></div>"
            "<div class='scale'><span>Extreme fear</span><span>Normal</span><span>Extreme greed</span></div>"
            f"<p>{plain}</p><details class='help'><summary>How is this worked out?</summary>"
            f"<p>The VIX fear index is {m['vix']:.1f}, higher than on {m['fear']:.0f}% of the last 3 years' days. Big tech stocks "
            f"are more stretched above their 200-day average than on {m['heat']:.0f}% of those days. The two are combined "
            f"into one score.</p><p>{html.escape(m['proof'])}</p></details></div>")


# ---------------------------------------------------------------- refreshing the news without the Mac
# GitHub's servers run this file on a schedule (see the workflow in the README): it downloads the
# news published since the last refresh, adds it to news.json, and rebuilds index.html from
# state.json. It needs only the MASSIVE_API_KEY secret.

NEWS_URL = "https://api.massive.com/v2/reference/news"
MAX_PAGES = 12


def named(title: str, ticker: str, names: dict[str, list[str]]) -> bool:
    """Does the headline name the company? (The same rule as news.CompanyNames.)"""
    words = sorted((a for a in names.get(ticker, []) if a), key=len, reverse=True)
    if words and re.search(r"(?<![\w])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w])", title, re.I):
        return True
    return bool(re.search(r"(?<![A-Za-z0-9])\$?" + re.escape(ticker) + r"(?![A-Za-z0-9])", title))


def article_rows(article: dict, watch: set[str]) -> list[dict]:
    """One row per watchlist stock an article is tagged with, in the shape the page uses."""
    insights = {i.get("ticker"): i for i in (article.get("insights") or []) if isinstance(i, dict)}
    rows = []
    for ticker in sorted(set(article.get("tickers") or []) & watch):
        ins = insights.get(ticker) or {}
        rows.append({"id": article.get("id"), "ticker": ticker, "title": article.get("title") or "",
                     "published_utc": article.get("published_utc") or "",
                     "publisher": (article.get("publisher") or {}).get("name"), "url": article.get("article_url"),
                     "sentiment": ins.get("sentiment"), "reasoning": (ins.get("sentiment_reasoning") or "")[:300] or None})
    return rows


def merge_rows(old: list[dict], new: list[dict], since_utc: str) -> list[dict]:
    """Both lists without repeats, only rows published since a time, newest first."""
    seen: dict[tuple, dict] = {}
    for r in old + new:
        if r.get("id") and r.get("published_utc", "") >= since_utc:
            seen[(r["id"], r["ticker"])] = r
    return sorted(seen.values(), key=lambda r: r["published_utc"], reverse=True)


def split_rows(rows: list[dict], names: dict[str, list[str]]) -> tuple[dict[str, list[dict]], int]:
    """The rows whose headline names their company, by ticker; and how many adverts were left out."""
    news_by: dict[str, list[dict]] = {}
    adverts = 0
    for r in rows:
        if not named(r["title"], r["ticker"], names):
            continue
        if ADVERT.search(r["title"]):
            adverts += 1
            continue
        news_by.setdefault(r["ticker"], []).append(r)
    return news_by, adverts


def _getter(key: str):
    def get(url: str) -> dict:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return get


def fetch_news(key: str, since_utc: str, watch: set[str], get=None, wait=None) -> list[dict]:
    """Every article published since a time, as rows for the watchlist stocks. At most 5 requests
    a minute (the free plan), so pages are 13 seconds apart."""
    get, wait = get or _getter(key), wait or time.sleep
    url = NEWS_URL + "?" + urllib.parse.urlencode({"published_utc.gte": since_utc, "order": "asc",
                                                   "sort": "published_utc", "limit": 1000})
    rows: list[dict] = []
    for page_number in range(MAX_PAGES):
        payload = get(url)
        for article in payload.get("results") or []:
            rows.extend(article_rows(article, watch))
        url = payload.get("next_url")
        if not url:
            break
        wait(13)
    return rows


PRICES_URL = "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/"
KEEP_DAYS = 25            # trading days of closes carried in the state
CLOSE_READY = (16, 10)    # New York time after which a day's closing prices are asked for


def moves_from_closes(closes: dict[str, list], calendar: str = "QQQ") -> tuple[str | None, dict[str, dict]]:
    """The latest trading day and each ticker's last close and change over 1, 5 and 20 trading
    days, from {ticker: [[day, close], ...]} (the same numbers briefing.changes gets from the database)."""
    days = [d for d, _ in closes.get(calendar, [])]
    if not days:
        return None, {}
    wanted = {n: days[-1 - n] for n in (0, 1, 5, 20) if len(days) > n}
    moves = {}
    for ticker, series in closes.items():
        by_day = dict((d, c) for d, c in series)
        last = by_day.get(wanted[0])
        if not last:
            continue
        moves[ticker] = {"close": last} | {
            str(n): (last / by_day[wanted[n]] - 1) if n in wanted and by_day.get(wanted[n]) else None for n in (1, 5, 20)}
    return days[-1], moves


def _new_york(now: datetime) -> datetime:
    """New York's clock time (US daylight time: second Sunday of March to first Sunday of November)."""
    year = now.year
    march = datetime(year, 3, 8, 7, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    november = datetime(year, 11, 1, 6, tzinfo=timezone.utc)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    return now + timedelta(hours=-4 if start <= now < end else -5)


def update_prices(state: dict, key: str, now: datetime, get, wait) -> int:
    """Add the closing prices of trading days since the state's last one (one request a day, for
    every stock at once), and work the moves out again. Returns how many days were added."""
    closes = state.get("closes") or {}
    last = state.get("last_day")
    if not closes or not last:
        return 0
    local = _new_york(now)
    final = local.date() if (local.hour, local.minute) >= CLOSE_READY else local.date() - timedelta(days=1)
    day = datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)
    added = 0
    while day <= final and added < 6:
        if day.weekday() < 5:
            payload = get(f"{PRICES_URL}{day.isoformat()}?adjusted=true")
            bars = {b.get("T"): b.get("c") for b in payload.get("results") or []}
            if bars.get("QQQ"):
                for ticker, series in closes.items():
                    if bars.get(ticker):
                        series.append([day.isoformat(), bars[ticker]])
                        del series[:-KEEP_DAYS]
                added += 1
            wait(13)
        day += timedelta(days=1)
    if added:
        state["last_day"], state["moves"] = moves_from_closes(closes)
    return added


NTFY_URL = "https://ntfy.sh/"


def push(topic: str | None, title: str, text: str, click: str | None = None, post=None) -> str | None:
    """A notification to your phone through ntfy (free; your phone's ntfy app is subscribed to a
    topic name only you know). Returns None if sent or switched off, else what went wrong."""
    if not topic:
        return None
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Tags": "chart_with_upwards_trend"}
    if click:
        headers["Click"] = click
    try:
        if post:
            post(NTFY_URL + topic, text, headers)
        else:
            request = urllib.request.Request(NTFY_URL + topic, data=text.encode("utf-8"), headers=headers, method="POST")
            urllib.request.urlopen(request, timeout=30).close()
    except Exception as e:
        return str(e)
    return None


def big_movers(state: dict) -> list[str]:
    """Watchlist stocks whose last daily move was at least the state's big-move size, biggest first."""
    names = {c["ticker"]: c["name"] for c in state["companies"]}
    limit = float(state.get("big_move", 0.05))
    found = [(m.get("1"), t) for t, m in state["moves"].items() if t in names and m.get("1") is not None
             and abs(m["1"]) >= limit]
    return [f"{names[t]} {change:+.1%}" for change, t in sorted(found, key=lambda x: -abs(x[0]))]


def notify_changes(state: dict, topic: str | None, post=None) -> list[str]:
    """Tell the phone what's new, each thing once (state["notified"] remembers): a new day's big
    movers, and dated events that are today or tomorrow. Returns what was sent."""
    if not topic:
        return []
    told = state.setdefault("notified", {})
    sent = []
    day, today = state.get("last_day"), state.get("today") or ""
    if day and told.get("prices") != day:
        movers = big_movers(state)
        if movers and told.get("prices") is not None:
            more = f" and {len(movers) - 6} more" if len(movers) > 6 else ""
            if not push(topic, f"Big moves on {_day(day)}", ", ".join(movers[:6]) + more + ".", state.get("site_url"), post):
                sent.append("movers")
        told["prices"] = day
    if today and told.get("coming") != today:
        tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        soon = [("Today: " if c["day"] == today else "Tomorrow: ") + c["what"] for c in state.get("coming") or []
                if c["day"] in (today, tomorrow)]
        if soon and told.get("coming") is not None:
            if not push(topic, "Coming up", ". ".join(soon[:6]) + ".", state.get("site_url"), post):
                sent.append("coming")
        told["coming"] = today
    return sent


FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="


def mood_dict(m, allowed: dict | None) -> dict:
    """A mood reading as the page shows it, with the sentence its test allows."""
    if allowed is None:
        proof = "Context only: not yet tested (uv run event-model mood --test)."
    elif m.label == "Fear" and allowed.get("Fear"):
        proof = "In the test, months like this were followed by higher Nasdaq-100 returns than other months."
    elif m.label == "Greed" and allowed.get("Greed"):
        proof = "In the test, months like this were followed by lower Nasdaq-100 returns than other months."
    else:
        proof = "Context only: in the test, the mood didn't predict the market's returns."
    return {"label": m.label, "score": m.score, "day": m.day, "vix": m.vix, "fear": m.fear, "heat": m.heat, "proof": proof}


def _text_getter(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "briefing-refresh"}), timeout=60) as resp:
        return resp.read().decode("utf-8")


def update_mood(state: dict, get_text=None) -> bool:
    """Work the market mood out again from FRED's latest VIX and Nasdaq-100 (free, no key). True
    if it moved to a newer day. FRED posts a day's values the next morning, US time."""
    get_text = get_text or _text_getter
    vix = mood_core.parse_series(get_text(FRED_URL + "VIXCLS"))
    index = mood_core.parse_series(get_text(FRED_URL + "NASDAQ100"))
    readings = mood_core.compute(vix, index)
    if not readings or (state.get("mood") and readings[-1].day <= state["mood"].get("day", "")):
        return False
    state["mood"] = mood_dict(readings[-1], state.get("mood_allowed"))
    return True


def refresh(folder: str, key: str, now: datetime | None = None, get=None, wait=None, get_text=None,
            topic: str | None = None, post=None) -> str:
    """Rebuild index.html in a folder from its state.json, with fresh news and any new day's
    closing prices. Returns a summary."""
    now = now or datetime.now(timezone.utc)
    with open(os.path.join(folder, "state.json"), encoding="utf-8") as f:
        state = json.load(f)
    try:
        with open(os.path.join(folder, "news.json"), encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = []
    get, wait = get or _getter(key), wait or time.sleep
    try:
        new_days = update_prices(state, key, now, get, wait)
    except Exception as e:           # prices are a bonus: the news refresh carries on without them
        new_days = 0
        print(f"(prices not refreshed: {e})")
    try:
        new_mood = update_mood(state, get_text)
    except Exception as e:           # so is the mood
        new_mood = False
        print(f"(mood not refreshed: {e})")
    local_now = now + timedelta(seconds=int(state.get("utc_offset", 0)))
    state["today"] = local_now.strftime("%Y-%m-%d")
    sent = notify_changes(state, topic, post)
    if new_days or new_mood or topic:
        with open(os.path.join(folder, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
    window = (now - timedelta(days=int(state.get("news_days", 3)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    since = max([r["published_utc"] for r in cached] + [window])
    watch = {c["ticker"] for c in state["companies"]}
    fresh = fetch_news(key, since, watch, get, wait)
    rows = merge_rows(cached, fresh, window)
    news_by, adverts = split_rows(rows, state.get("names") or {})
    newest = max((r["published_utc"] for r in rows), default=None)
    local = now + timedelta(seconds=int(state.get("utc_offset", 0)))
    state["today"] = local.strftime("%Y-%m-%d")
    updated = local.strftime("%A %d %B %Y").replace(" 0", " ") + ", news refreshed " + local.strftime("%H:%M")
    html_page, counts = page(state, news_by, adverts, newest, published=True, updated=updated)
    with open(os.path.join(folder, "news.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f)
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_page)
    return (f"{len(fresh)} new rows; {counts['articles']} articles about {counts['stocks']} stocks on the page; "
            f"{new_days} new day{'s' if new_days != 1 else ''} of prices (to {state.get('last_day')}); "
            f"mood {'updated' if new_mood else 'unchanged'} ({(state.get('mood') or {}).get('day')}); "
            f"phone told: {', '.join(sent) or 'nothing new'}")


if __name__ == "__main__":
    import sys
    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        sys.exit("MASSIVE_API_KEY isn't set (add it under the repository's Settings > Secrets and variables > Actions).")
    print(refresh(sys.argv[1] if len(sys.argv) > 1 else ".", api_key, topic=os.environ.get("NTFY_TOPIC") or None))
