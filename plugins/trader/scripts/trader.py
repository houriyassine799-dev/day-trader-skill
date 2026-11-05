#!/usr/bin/env python3
"""trader: gemeinsames Werkzeug fuer das Plugin "trader".

Nur Standardbibliothek (Python 3.9+). Spricht NIE selbst mit einem Broker oder
einer Daten-API: Claude holt die Daten ueber die MCP-Server aus .mcp.json und
gibt sie diesem Werkzeug als JSON-Datei. Das Werkzeug rechnet, prueft die
Regeln, fuehrt die Pipeline-Gates und schreibt das Journal.

Arbeitsordner: ./trader (oder $TRADER_HOME)
  trader-config.json   Konfiguration (Vorlage: trader-config.example.json)
  journal.csv          Trade-Journal, jede Aktion eine Zeile
  trades/<id>.json     ein Trade mit allen Pipeline-Schritten
  alerts.json          Preisalarme
  reports/             Time-Machine-Ergebnisse

Pipeline (Reihenfolge ist Pflicht):
  exit -> timemachine -> risk -> budget -> broker (preview) -> confirm ("Ja")
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import uuid

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("trader.py braucht Python 3.9 oder neuer (zoneinfo).")

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CONFIG = os.path.join(PLUGIN_ROOT, "trader-config.example.json")

PIPELINE = ["exit", "timemachine", "risk", "budget", "broker"]
JOURNAL_HEADER = ["Zeit", "Trade-ID", "Aktion", "Modus", "Ticker", "Setup", "Entry", "Stop",
                  "Target", "Größe", "Ergebnis", "Begründung"]
SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|passwort)", re.I)
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
YES_RE = re.compile(r"^\s*ja\s*[.!]?\s*$", re.I)

DEFAULTS = {
    "mode": "paper",
    "account_currency": "EUR",
    "account_budget": 10000,
    "max_loss_per_trade": {"value": 1, "unit": "%"},
    "max_daily_loss": {"value": 3, "unit": "%"},
    "max_open_positions": 3,
    "max_position_size": {"value": 25, "unit": "%"},
    "min_reward_risk": 2.0,
    "allow_short": False,
    "watchlist": [],
    "timezone": "Europe/Berlin",
    "trading_hours": {"start": "15:30", "end": "21:45"},
    "default_exchange": "NASDAQ",
    "exchanges": {
        "NASDAQ": {"timezone": "America/New_York", "open": "09:30", "close": "16:00", "currency": "USD"},
        "NYSE": {"timezone": "America/New_York", "open": "09:30", "close": "16:00", "currency": "USD"},
        "XETRA": {"timezone": "Europe/Berlin", "open": "09:00", "close": "17:30", "currency": "EUR"},
    },
    "broker": {"name": "Alpaca", "api": "Alpaca Paper API", "mcp_server": "alpaca"},
    "pipeline": {"max_age_minutes": 30, "confirm_within_minutes": 10},
    "timemachine": {"simulations": 10000, "horizon_bars": 78, "min_backtest_samples": 20,
                    "block_negative_expectancy": True},
    "news": {"reaction_window_minutes": 15, "min_move_pct": 2.0, "min_volume_ratio": 2.0},
    "entry": {"volume_multiple": 1.5, "volume_lookback": 20},
}


class TraderError(Exception):
    pass


# ---------------------------------------------------------------- Grundlagen

def home():
    return os.environ.get("TRADER_HOME") or os.path.join(os.getcwd(), "trader")


def path(*parts):
    return os.path.join(home(), *parts)


def now_utc(args=None):
    raw = getattr(args, "now", None) if args is not None else None
    if raw:
        return parse_time(raw)
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value, default_tz="Europe/Berlin"):
    """ISO-Zeit, 'YYYY-MM-DD HH:MM' (Berliner Zeit) oder Epoch in ms/s."""
    if isinstance(value, (int, float)):
        secs = value / 1000.0 if value > 1e11 else float(value)
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc)
    s = str(value).strip()
    if re.match(r"^\d{10,13}$", s):
        return parse_time(int(s))
    s = s.replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo(default_tz))
    return t.astimezone(dt.timezone.utc)


def iso(t, tz="Europe/Berlin"):
    return t.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S %Z")


def read_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)


def merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def money(x, cur="EUR"):
    return "%s %s" % ("{:,.2f}".format(x).replace(",", "X").replace(".", ",").replace("X", "."), cur)


def num(x, nd=2):
    return ("{:.%df}" % nd).format(x)


# ------------------------------------------------------------- Konfiguration

def find_secrets(obj, where=""):
    """Liefert Pfade, an denen ein Geheimnis im Klartext zu stehen scheint."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = "%s.%s" % (where, k) if where else k
            if SECRET_KEY_RE.search(k) and not k.endswith("_env"):
                # Jeder Wert unter api_key/secret/token/password ist verboten, auch einer,
                # der wie ein Variablenname aussieht (Alpaca-Keys sind GROSSBUCHSTABEN+Ziffern).
                if v not in (None, "", [], {}) and not isinstance(v, (dict, list)):
                    hits.append(here)
            if k.endswith("_env"):
                vals = v if isinstance(v, list) else [v]
                for item in vals:
                    if not (isinstance(item, str) and ENV_NAME_RE.match(item)):
                        hits.append(here)
            hits.extend(find_secrets(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_secrets(v, "%s[%d]" % (where, i)))
    return hits


def load_config(required=True):
    p = os.environ.get("TRADER_CONFIG") or path("trader-config.json")
    raw = read_json(p)
    if raw is None:
        if required:
            raise TraderError("Keine Konfiguration unter %s. Zuerst: trader.py init" % p)
        raw = {}
    secrets = find_secrets(raw)
    if secrets:
        raise TraderError("Die Konfiguration enthaelt offenbar Geheimnisse im Klartext (%s). "
                          "API-Keys gehoeren in Umgebungsvariablen; in der Config steht nur der "
                          "NAME der Variable (Feld *_env)." % ", ".join(secrets))
    cfg = merge(DEFAULTS, raw)
    if cfg["mode"] not in ("paper", "live"):
        raise TraderError('mode muss "paper" oder "live" sein, nicht %r.' % cfg["mode"])
    # Echtes Geld nur bei exakt "live"; alles andere ist paper.
    cfg["is_live"] = raw.get("mode") == "live"
    cfg["_path"] = p
    return cfg


def amount(spec, cfg, base=None):
    """{"value": 1, "unit": "%"} oder {"value": 50, "unit": "EUR"} oder Zahl (Kontowaehrung)."""
    base = cfg["account_budget"] if base is None else base
    if isinstance(spec, (int, float)):
        return float(spec)
    unit = str(spec.get("unit", cfg["account_currency"]))
    val = float(spec["value"])
    if unit == "%":
        return base * val / 100.0
    acc = cfg["account_currency"].upper()
    ok_units = {acc, "€"} if acc == "EUR" else {acc}
    if unit.upper() not in ok_units:
        raise TraderError("Einheit %s passt nicht zur Kontowaehrung %s." % (unit, cfg["account_currency"]))
    return val


def exchange(cfg, name):
    name = (name or cfg["default_exchange"]).upper()
    ex = cfg["exchanges"].get(name)
    if not ex:
        raise TraderError("Boerse %s fehlt in exchanges der Config." % name)
    return name, ex


def hm(s):
    h, m = s.split(":")
    return int(h), int(m)


def session_window(cfg, exname, day_local):
    _, ex = exchange(cfg, exname)
    tz = ZoneInfo(ex["timezone"])
    oh, om = hm(ex["open"])
    ch, cm = hm(ex["close"])
    o = dt.datetime(day_local.year, day_local.month, day_local.day, oh, om, tzinfo=tz)
    c = dt.datetime(day_local.year, day_local.month, day_local.day, ch, cm, tzinfo=tz)
    return o, c


def market_open(cfg, exname, t):
    _, ex = exchange(cfg, exname)
    local = t.astimezone(ZoneInfo(ex["timezone"]))
    if local.weekday() >= 5:
        return False
    o, c = session_window(cfg, exname, local)
    return o <= local < c


def in_trading_hours(cfg, t):
    local = t.astimezone(ZoneInfo(cfg["timezone"]))
    sh, sm = hm(cfg["trading_hours"]["start"])
    eh, em = hm(cfg["trading_hours"]["end"])
    mins = local.hour * 60 + local.minute
    return sh * 60 + sm <= mins < eh * 60 + em


def next_open(cfg, exname, t):
    _, ex = exchange(cfg, exname)
    local = t.astimezone(ZoneInfo(ex["timezone"]))
    for i in range(0, 8):
        day = local + dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        o, _ = session_window(cfg, exname, day)
        if o > local:
            return o
    raise TraderError("Keine naechste Eroeffnung gefunden.")


# ---------------------------------------------------------------- Journal

def journal(cfg, action, trade=None, result="", reason="", ticker="", setup="", t=None,
            size=None, entry=None, stop=None, target=None):
    p = path("journal.csv")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    new = not os.path.exists(p)
    t = t or dt.datetime.now(dt.timezone.utc)
    tr = trade or {}
    row = [
        iso(t, cfg.get("timezone", "Europe/Berlin")),
        tr.get("id", ""),
        action,
        "live" if cfg.get("is_live") else "paper",
        ticker or tr.get("ticker", ""),
        setup or tr.get("setup", ""),
        _fmt(entry if entry is not None else tr.get("entry")),
        _fmt(stop if stop is not None else tr.get("stop")),
        _fmt(target if target is not None else tr.get("target")),
        _fmt(size if size is not None else tr.get("qty")),
        result,
        reason,
    ]
    with open(p, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(JOURNAL_HEADER)
        w.writerow(row)


def _fmt(v):
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


# ----------------------------------------------------------------- Trades

def fingerprint(tr):
    core = {k: tr.get(k) for k in ("ticker", "side", "entry", "stop", "target", "exchange", "fx")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def trade_path(tid):
    if not re.match(r"^[A-Za-z0-9_-]+$", tid or ""):
        raise TraderError("Ungueltige Trade-ID.")
    return path("trades", tid + ".json")


def load_trade(tid):
    tr = read_json(trade_path(tid))
    if tr is None:
        raise TraderError("Trade %s nicht gefunden. Ein Trade beginnt immer mit /trader-exit." % tid)
    return tr


def save_trade(tr):
    write_json(trade_path(tr["id"]), tr)


def all_trades():
    d = path("trades")
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            out.append(read_json(os.path.join(d, f)))
    return out


def record_step(tr, step, ok, data, t, reasons=None):
    tr.setdefault("steps", {})[step] = {
        "status": "PASS" if ok else "BLOCK",
        "at": t.isoformat(),
        "fingerprint": fingerprint(tr),
        "reasons": reasons or [],
        "data": data,
    }
    # ein neuer Schritt macht alle spaeteren Schritte ungueltig
    if step in PIPELINE:
        for later in PIPELINE[PIPELINE.index(step) + 1:] + ["confirm"]:
            tr["steps"].pop(later, None)


def require_previous(tr, step, cfg, t):
    """Prueft, dass alle Schritte vor `step` PASS sind, zum Plan passen und frisch sind."""
    problems = []
    idx = PIPELINE.index(step) if step in PIPELINE else len(PIPELINE)
    fp = fingerprint(tr)
    max_age = dt.timedelta(minutes=cfg["pipeline"]["max_age_minutes"])
    for prev in PIPELINE[:idx]:
        s = tr.get("steps", {}).get(prev)
        if not s:
            problems.append("Schritt /trader-%s fehlt." % prev)
            continue
        if s["status"] != "PASS":
            problems.append("Schritt /trader-%s ist BLOCK: %s" % (prev, "; ".join(s["reasons"])))
        if s["fingerprint"] != fp:
            problems.append("Der Plan wurde nach /trader-%s geaendert. Pipeline neu durchlaufen." % prev)
        if t - parse_time(s["at"]) > max_age:
            problems.append("/trader-%s ist aelter als %d Minuten. Neu pruefen." % (prev, max_age.seconds // 60))
    return problems


def per_share_risk(tr):
    return abs(tr["entry"] - tr["stop"]) * tr["fx"]


# --------------------------------------------------------------- Bars & Indikatoren

def load_bars(p):
    """Akzeptiert eine Liste von Bars oder {"bars": [...]} bzw. {TICKER: [...]}.
    Felder: t (ISO oder Epoch), o, h, l, c, v (auch open/high/low/close/volume/timestamp)."""
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    return raw


def norm_bars(bars):
    out = []
    for b in bars:
        g = lambda *ks: next((b[k] for k in ks if k in b), None)  # noqa: E731
        out.append({
            "t": parse_time(g("t", "timestamp", "time", "date")),
            "o": float(g("o", "open")), "h": float(g("h", "high")),
            "l": float(g("l", "low")), "c": float(g("c", "close")),
            "v": float(g("v", "volume") or 0),
        })
    out.sort(key=lambda x: x["t"])
    return out


def bars_by_ticker(raw):
    if isinstance(raw, list):
        return {"?": norm_bars(raw)}
    if "bars" in raw and isinstance(raw["bars"], list):
        return {raw.get("ticker", "?"): norm_bars(raw["bars"])}
    if "bars" in raw and isinstance(raw["bars"], dict):
        raw = raw["bars"]
    return {k.upper(): norm_bars(v) for k, v in raw.items() if isinstance(v, list) and v}


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(bars, n=14):
    if len(bars) < 2:
        return None
    trs = []
    for prev, b in zip(bars[:-1], bars[1:]):
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev["c"]), abs(b["l"] - prev["c"])))
    trs = trs[-n:]
    return sum(trs) / len(trs)


def avg_volume(bars, lookback):
    vols = [b["v"] for b in bars[-lookback:]]
    return sum(vols) / len(vols) if vols else 0.0


def session_bars(cfg, exname, bars):
    """Bars der letzten Sitzung (Tag des letzten Bars), ab Eroeffnung."""
    if not bars:
        return []
    _, ex = exchange(cfg, exname)
    tz = ZoneInfo(ex["timezone"])
    day = bars[-1]["t"].astimezone(tz).date()
    o, c = session_window(cfg, exname, dt.datetime(day.year, day.month, day.day, tzinfo=tz))
    return [b for b in bars if o <= b["t"].astimezone(tz) < c]


def opening_range(cfg, exname, sbars, minutes):
    if not sbars:
        return None
    _, ex = exchange(cfg, exname)
    tz = ZoneInfo(ex["timezone"])
    first = sbars[0]["t"].astimezone(tz)
    o, _ = session_window(cfg, exname, first)
    end = o + dt.timedelta(minutes=minutes)
    rng = [b for b in sbars if b["t"].astimezone(tz) < end]
    rest = [b for b in sbars if b["t"].astimezone(tz) >= end]
    if not rng or not rest:
        return None
    return {"high": max(b["h"] for b in rng), "low": min(b["l"] for b in rng), "end": end, "after": rest}


def is_reversal_up(prev, b):
    body = abs(b["c"] - b["o"])
    rng = b["h"] - b["l"]
    if rng <= 0:
        return False
    lower = min(b["o"], b["c"]) - b["l"]
    hammer = lower >= 2 * body and b["c"] >= b["l"] + 0.66 * rng
    engulf = (prev["c"] < prev["o"] and b["c"] > b["o"] and b["c"] >= prev["o"] and b["o"] <= prev["c"])
    return hammer or engulf


def find_flag(bars, bull=True, pole_min_pct=1.5, max_pole=8, flag_min=3, flag_max=10):
    """Einfache Flaggen-Erkennung: Mast >= pole_min_pct %, danach eine Konsolidierung,
    die weniger als 50 % des Masts zuruecknimmt, bei sinkendem Volumen. Signal, wenn der
    letzte Bar ueber (unter) der Flagge schliesst."""
    n = len(bars)
    last = bars[-1]
    for flag_len in range(flag_min, flag_max + 1):
        f_end = n - 1
        f_start = f_end - flag_len
        if f_start < 2:
            break
        flag = bars[f_start:f_end]
        for pole_len in range(2, max_pole + 1):
            p_start = f_start - pole_len
            if p_start < 0:
                break
            pole = bars[p_start:f_start]
            if bull:
                move = (pole[-1]["h"] - pole[0]["l"]) / pole[0]["l"] * 100
                top, bottom = max(b["h"] for b in flag), min(b["l"] for b in flag)
                retrace = (pole[-1]["h"] - bottom) / max(pole[-1]["h"] - pole[0]["l"], 1e-9)
                trigger = last["c"] > top
            else:
                move = (pole[0]["h"] - pole[-1]["l"]) / pole[0]["h"] * 100
                top, bottom = max(b["h"] for b in flag), min(b["l"] for b in flag)
                retrace = (top - pole[-1]["l"]) / max(pole[0]["h"] - pole[-1]["l"], 1e-9)
                trigger = last["c"] < bottom
            vol_ok = avg_volume(flag, len(flag)) < avg_volume(pole, len(pole))
            if move >= pole_min_pct and retrace < 0.5 and vol_ok and trigger:
                return {"pole_pct": move, "flag_high": top, "flag_low": bottom, "flag_bars": flag_len,
                        "retrace": retrace}
    return None


def scan_ticker(cfg, exname, ticker, bars, or_minutes=(5, 15, 30), lookback=20):
    hits = []
    if len(bars) < 3:
        return hits
    last = bars[-1]
    vol_ratio = last["v"] / avg_volume(bars[:-1], lookback) if avg_volume(bars[:-1], lookback) else 0.0
    sbars = session_bars(cfg, exname, bars)
    link = "https://www.tradingview.com/chart/?symbol=%s" % ticker
    for m in or_minutes:
        orng = opening_range(cfg, exname, sbars, m)
        if not orng:
            continue
        width = orng["high"] - orng["low"]
        if last["c"] > orng["high"]:
            dist = (last["c"] - orng["high"]) / orng["high"] * 100
            hits.append(("ORB-%d long" % m, dist, {"level": orng["high"], "range_low": orng["low"], "range_high": orng["high"], "width": width}))
        elif last["c"] < orng["low"]:
            dist = (orng["low"] - last["c"]) / orng["low"] * 100
            hits.append(("ORB-%d short" % m, dist, {"level": orng["low"], "range_low": orng["low"], "range_high": orng["high"], "width": width}))
    bf = find_flag(bars, bull=True)
    if bf:
        hits.append(("Bull-Flag", bf["pole_pct"] / 2, {"level": bf["flag_high"], **bf}))
    bf = find_flag(bars, bull=False)
    if bf:
        hits.append(("Bear-Flag", bf["pole_pct"] / 2, {"level": bf["flag_low"], **bf}))
    closes = [b["c"] for b in bars]
    r_prev = rsi(closes[:-1])
    r_now = rsi(closes)
    if r_prev is not None and min(r_prev, r_now) < 30 and is_reversal_up(bars[-2], last):
        hits.append(("Oversold-Bounce", (30 - min(r_prev, r_now)) / 3, {"level": last["h"], "rsi": min(r_prev, r_now)}))
    out = []
    for setup, strength, info in hits:
        # Score 0-100: Abstand/Musterstaerke (max 50) + Volumen (max 50)
        score = min(50.0, strength * 10.0) + min(50.0, max(0.0, (vol_ratio - 1.0)) * 25.0)
        out.append({"ticker": ticker, "setup": setup, "price": last["c"], "time": iso(last["t"], cfg["timezone"]),
                    "volume_ratio": round(vol_ratio, 2), "score": round(score), "chart": link,
                    "rsi": round(r_now, 1) if r_now is not None else None,
                    "info": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in info.items()
                             if not isinstance(v, (list, dt.datetime))}})
    return out


# ------------------------------------------------------------ Monte Carlo

def returns_from_bars(bars):
    rets = []
    for a, b in zip(bars[:-1], bars[1:]):
        if a["c"] > 0 and b["c"] > 0:
            rets.append(math.log(b["c"] / a["c"]))
    return rets


def simulate(entry, stop, target, side, rets, sims=10000, horizon=78, seed=None, method="gbm"):
    """Simuliert `sims` Sitzungen ab dem Entry. Jeder Pfad endet am Stop, am Ziel oder
    nach `horizon` Bars (Schluss zum letzten Kurs). Ergebnis je Pfad in R (Vielfache
    des Risikos). Ein Pfad, der ueber den Stop hinaus springt, wird am simulierten Kurs
    geschlossen (Slippage-Effekt)."""
    if len(rets) < 20:
        raise TraderError("Zu wenige Renditen (%d) fuer eine Simulation; mind. 20 Bars noetig." % len(rets))
    rng = random.Random(seed)
    sigma = statistics.pstdev(rets)
    risk = abs(entry - stop)
    long = side == "long"
    outcomes, kinds = [], {"target": 0, "stop": 0, "time": 0}
    for _ in range(sims):
        p = entry
        kind = "time"
        for _ in range(horizon):
            r = rng.gauss(0.0, sigma) if method == "gbm" else rng.choice(rets)
            p *= math.exp(r)
            if (long and p <= stop) or (not long and p >= stop):
                kind = "stop"
                break
            if (long and p >= target) or (not long and p <= target):
                kind = "target"
                p = target  # Limit-Order fuellt am Ziel
                break
        kinds[kind] += 1
        pnl = (p - entry) if long else (entry - p)
        outcomes.append(pnl / risk)
    return outcomes, kinds, sigma


def backtest_outcomes(items, side_default="long"):
    """items: [{"date", "r"}] oder [{"date","entry","stop","target","side","bars":[...]}].
    Bei Bars wird konservativ gerechnet: trifft ein Bar Stop UND Ziel, zaehlt der Stop."""
    out = []
    for it in items:
        if "r" in it:
            out.append((it.get("date", ""), float(it["r"])))
            continue
        e, s, tg = float(it["entry"]), float(it["stop"]), float(it["target"])
        long = it.get("side", side_default) == "long"
        risk = abs(e - s)
        res = None
        bars = norm_bars(it["bars"])
        for b in bars:
            hit_stop = b["l"] <= s if long else b["h"] >= s
            hit_tgt = b["h"] >= tg if long else b["l"] <= tg
            if hit_stop:
                res = -1.0
                break
            if hit_tgt:
                res = abs(tg - e) / risk
                break
        if res is None and bars:
            last = bars[-1]["c"]
            res = ((last - e) if long else (e - last)) / risk
        if res is not None:
            out.append((it.get("date", ""), res))
    return out


def summarize(rs):
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    srt = sorted(rs)
    q = lambda p: srt[min(len(srt) - 1, max(0, int(p * len(srt))))]  # noqa: E731
    return {
        "n": len(rs),
        "win_rate": len(wins) / len(rs),
        "avg_win_r": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_r": sum(losses) / len(losses) if losses else 0.0,
        "expectancy_r": sum(rs) / len(rs),
        "worst_r": srt[0],
        "p05_r": q(0.05),
        "median_r": q(0.5),
        "best_r": srt[-1],
    }


def histogram(rs, bins=12, width=40):
    if not rs:
        return ""
    lo, hi = min(rs), max(rs)
    if hi == lo:
        hi = lo + 1
    step = (hi - lo) / bins
    counts = [0] * bins
    for r in rs:
        counts[min(bins - 1, int((r - lo) / step))] += 1
    top = max(counts)
    lines = []
    for i, c in enumerate(counts):
        a = lo + i * step
        bar = "#" * int(round(c / top * width)) if top else ""
        lines.append("%7.2fR .. %6.2fR | %-*s %5.1f%%" % (a, a + step, width, bar, 100.0 * c / len(rs)))
    return "\n".join(lines)


# ------------------------------------------------------------ Risiko-Logik

def account_state(cfg, t, exclude=None):
    """Offene Positionen, offenes Risiko und realisierter Verlust heute."""
    tz = ZoneInfo(cfg["timezone"])
    today = t.astimezone(tz).date()
    open_pos, open_risk, exposure, realized = 0, 0.0, 0.0, 0.0
    for tr in all_trades():
        if tr["id"] == exclude:
            continue
        if tr.get("status") == "open":
            open_pos += 1
            q = tr.get("filled_qty") or tr.get("qty") or 0
            open_risk += q * per_share_risk(tr)
            exposure += q * (tr.get("filled_price") or tr["entry"]) * tr["fx"]
        if tr.get("status") == "closed" and tr.get("closed_at"):
            if parse_time(tr["closed_at"]).astimezone(tz).date() == today:
                realized += tr.get("result", 0.0)
    return {"open_positions": open_pos, "open_risk": open_risk, "exposure": exposure,
            "realized_today": realized}


def risk_check(cfg, tr, t, qty=None):
    """Prueft alle Limits. Ohne qty: welche Stueckzahl die Limits hoechstens erlauben.
    Mit qty (finaler Re-Check vor dem Broker): diese Stueckzahl."""
    reasons, notes = [], []
    st = account_state(cfg, t, exclude=tr["id"])
    cur = cfg["account_currency"]
    rps = per_share_risk(tr)
    max_loss = amount(cfg["max_loss_per_trade"], cfg)
    max_daily = amount(cfg["max_daily_loss"], cfg)
    max_pos_value = amount(cfg["max_position_size"], cfg)
    price_acc = tr["entry"] * tr["fx"]

    if rps <= 0:
        reasons.append("Kein Abstand zwischen Entry und Stop.")
    if tr["side"] == "short" and not cfg["allow_short"]:
        reasons.append("Short-Trades sind in der Config nicht erlaubt (allow_short: false).")
    if st["open_positions"] >= int(cfg["max_open_positions"]):
        reasons.append("Schon %d offene Positionen, Limit max_open_positions = %d."
                       % (st["open_positions"], cfg["max_open_positions"]))
    used = -min(st["realized_today"], 0.0) + st["open_risk"]
    left = max_daily - used
    if left <= 0:
        reasons.append("Tagesverlust-Limit erreicht: realisiert %s, offenes Risiko %s, Limit %s."
                       % (money(-min(st["realized_today"], 0), cur), money(st["open_risk"], cur), money(max_daily, cur)))
    if not in_trading_hours(cfg, t):
        reasons.append("Ausserhalb der trading_hours (%s-%s %s)." % (
            cfg["trading_hours"]["start"], cfg["trading_hours"]["end"], cfg["timezone"]))
    if not market_open(cfg, tr["exchange"], t):
        reasons.append("Die Boerse %s ist jetzt geschlossen (Feiertage zusaetzlich beim Broker pruefen)." % tr["exchange"])

    q_loss = math.floor(max_loss / rps) if rps > 0 else 0
    q_pos = math.floor(max_pos_value / price_acc) if price_acc > 0 else 0
    q_day = math.floor(max(left, 0.0) / rps) if rps > 0 else 0
    max_qty = max(0, min(q_loss, q_pos, q_day))
    if qty is None:
        if max_qty < 1 and not reasons:
            reasons.append("Die Limits erlauben nicht einmal 1 Stueck (Risiko je Stueck %s)." % money(rps, cur))
        check_qty = max_qty
    else:
        check_qty = qty
        if qty < 1:
            reasons.append("Stueckzahl muss mindestens 1 sein.")
        if qty * rps > max_loss + 1e-9:
            reasons.append("Verlust am Stop %s > max_loss_per_trade %s." % (money(qty * rps, cur), money(max_loss, cur)))
        if qty * price_acc > max_pos_value + 1e-9:
            reasons.append("Positionsgroesse %s > max_position_size %s." % (money(qty * price_acc, cur), money(max_pos_value, cur)))
        if qty * rps > left + 1e-9:
            reasons.append("Mit diesem Trade waere das Tagesverlust-Limit ueberschritten (frei: %s)." % money(max(left, 0), cur))
    rr = abs(tr["target"] - tr["entry"]) / abs(tr["entry"] - tr["stop"]) if rps > 0 else 0
    if rr + 1e-9 < float(cfg["min_reward_risk"]):
        reasons.append("Chance-Risiko %.2f:1 < Minimum %.2f:1." % (rr, cfg["min_reward_risk"]))
    data = {"max_qty": max_qty, "q_by_max_loss": q_loss, "q_by_position_size": q_pos, "q_by_daily_loss": q_day,
            "risk_per_share": rps, "max_loss_per_trade": max_loss, "max_daily_loss": max_daily,
            "daily_loss_left": left, "max_position_value": max_pos_value, "checked_qty": check_qty,
            "reward_risk": rr, **st}
    return (not reasons), reasons, notes, data


# ------------------------------------------------------------------ Commands

def cmd_init(args):
    dest = path("trader-config.json")
    if os.path.exists(dest) and not args.force:
        print("Config existiert schon: %s (--force zum Ueberschreiben)" % dest)
        return 0
    os.makedirs(home(), exist_ok=True)
    with open(EXAMPLE_CONFIG, encoding="utf-8") as fh:
        text = fh.read()
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("Config angelegt: %s (mode: paper). Bitte Budget und Limits anpassen." % dest)
    return 0


def cmd_config(args):
    cfg = load_config()
    shown = {k: v for k, v in cfg.items() if not k.startswith("_")}
    print("Config: %s" % cfg["_path"])
    print("MODUS: %s" % ("LIVE (echtes Geld)" if cfg["is_live"] else "PAPER (Standard)"))
    cur = cfg["account_currency"]
    print("Max. Verlust je Trade: %s | Max. Tagesverlust: %s | Max. Positionsgroesse: %s | Max. offene Positionen: %d"
          % (money(amount(cfg["max_loss_per_trade"], cfg), cur), money(amount(cfg["max_daily_loss"], cfg), cur),
             money(amount(cfg["max_position_size"], cfg), cur), cfg["max_open_positions"]))
    if args.json:
        print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0


def cmd_env(args):
    """Zeigt nur, OB Variablen gesetzt sind. Werte werden nie ausgegeben."""
    names = args.names or ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "POLYGON_API_KEY", "FINNHUB_API_KEY",
                           "BENZINGA_API_KEY", "SEC_EDGAR_USER_AGENT", "QUIVER_API_KEY"]
    for n in names:
        print("%-24s %s" % (n, "gesetzt" if os.environ.get(n) else "fehlt"))
    return 0


def cmd_scan(args):
    cfg = load_config()
    raw = load_bars(args.bars)
    rows = []
    for tk, bars in bars_by_ticker(raw).items():
        rows.extend(scan_ticker(cfg, args.exchange, tk if tk != "?" else (args.ticker or "?"), bars,
                                lookback=cfg["entry"]["volume_lookback"]))
    rows.sort(key=lambda r: -r["score"])
    if args.min_score:
        rows = [r for r in rows if r["score"] >= args.min_score]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print("| Ticker | Setup | Kurs | Volumen vs. Ø | Score | Chart |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            print("| %s | %s | %s | %.2fx | %d | %s |" % (r["ticker"], r["setup"], num(r["price"]),
                                                       r["volume_ratio"], r["score"], r["chart"]))
        if not rows:
            print("(keine Setups gefunden)")
    if args.journal:
        journal(cfg, "scan", result="%d Treffer" % len(rows), ticker=",".join(sorted({r["ticker"] for r in rows}))[:200],
                reason="Datei %s" % os.path.basename(args.bars), t=now_utc(args))
    return 0


def cmd_entry(args):
    cfg = load_config()
    bars = list(bars_by_ticker(load_bars(args.bars)).values())[0]
    t = now_utc(args)
    # nur abgeschlossene Bars: ein Bar mit Startzeit + Intervall > jetzt laeuft noch
    closed = [b for b in bars if b["t"] + dt.timedelta(minutes=args.interval) <= t]
    if len(closed) < 2:
        print("WARTEN: noch kein abgeschlossener Bar.")
        return 0
    last = closed[-1]
    lookback = cfg["entry"]["volume_lookback"]
    mult = args.volume_multiple or cfg["entry"]["volume_multiple"]
    avg = avg_volume(closed[:-1], lookback)
    vol_ok = avg > 0 and last["v"] > mult * avg
    if args.direction == "above":
        price_ok = last["c"] > args.level
        cond = "Close %s > %s" % (num(last["c"]), num(args.level))
    else:
        price_ok = last["c"] < args.level
        cond = "Close %s < %s" % (num(last["c"]), num(args.level))
    vcond = "Volumen %d > %.1f x Ø %d" % (last["v"], mult, avg)
    bar_end = last["t"] + dt.timedelta(minutes=args.interval)
    if price_ok and vol_ok:
        print("AUSGELOEST um %s: %s und %s. Kurs %s." % (iso(bar_end, cfg["timezone"]), cond, vcond, num(last["c"])))
        print("Es wurde KEINE Order ausgeloest. Naechster Schritt: /trader-exit.")
        status = "ausgeloest"
    else:
        missing = [] if price_ok else ["Kurs: %s nicht erfuellt" % cond]
        if not vol_ok:
            missing.append("Volumen: %s nicht erfuellt" % vcond)
        print("WARTEN (Bar bis %s): %s" % (iso(bar_end, cfg["timezone"]), "; ".join(missing)))
        status = "wartet"
    if args.journal:
        journal(cfg, "entry-check", result=status, ticker=args.ticker or "", setup=args.setup or "",
                entry=last["c"], reason="%s; %s" % (cond, vcond), t=t)
    return 0


def cmd_exit(args):
    cfg = load_config()
    t = now_utc(args)
    exname, ex = exchange(cfg, args.exchange)
    quote_cur = args.currency or ex.get("currency", cfg["account_currency"])
    fx = args.fx
    if quote_cur.upper() == cfg["account_currency"].upper():
        fx = 1.0
    elif not fx:
        raise TraderError("Kurs in %s, Konto in %s: --fx (%s je 1 %s) angeben, mit Datum der Quelle."
                          % (quote_cur, cfg["account_currency"], cfg["account_currency"], quote_cur))
    entry = args.entry
    stop = args.stop
    method = "manuell"
    if stop is None:
        if not args.bars:
            raise TraderError("Kein --stop: dann --bars und --stop-mode (range|atr) angeben.")
        bars = list(bars_by_ticker(load_bars(args.bars)).values())[0]
        if args.stop_mode == "atr":
            a = atr(bars, 14)
            stop = entry - args.atr_mult * a if args.side == "long" else entry + args.atr_mult * a
            method = "ATR(14)=%s x %.2f" % (num(a, 4), args.atr_mult)
        else:
            orng = opening_range(cfg, exname, session_bars(cfg, exname, bars), args.or_minutes)
            if not orng:
                raise TraderError("Keine Opening Range in den Bars gefunden.")
            stop = orng["low"] - args.buffer if args.side == "long" else orng["high"] + args.buffer
            method = "Range-%s der ersten %d Min. %s Puffer %s" % (
                "Low" if args.side == "long" else "High", args.or_minutes, "-" if args.side == "long" else "+", num(args.buffer))
    stop = round(stop, 2)
    risk = abs(entry - stop)
    rr_min = float(cfg["min_reward_risk"])
    target = args.target
    if target is None:
        target = entry + rr_min * risk if args.side == "long" else entry - rr_min * risk
    target = round(target, 2)
    tr = {"id": dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
          "ticker": args.ticker.upper(), "setup": args.setup, "side": args.side, "entry": entry,
          "stop": stop, "target": target, "exchange": exname, "quote_currency": quote_cur, "fx": fx,
          "fx_source": args.fx_source or "", "data_basis": args.data_basis or "", "reason": args.reason or "",
          "stop_method": method, "flat_by": args.flat_by or "", "created_at": t.isoformat(), "status": "planned",
          "mode": "live" if cfg["is_live"] else "paper"}
    reasons = []
    if args.side == "long" and not stop < entry:
        reasons.append("Long: Stop muss unter dem Entry liegen.")
    if args.side == "short" and not stop > entry:
        reasons.append("Short: Stop muss ueber dem Entry liegen.")
    if args.side == "long" and not target > entry:
        reasons.append("Long: Ziel muss ueber dem Entry liegen.")
    if args.side == "short" and not target < entry:
        reasons.append("Short: Ziel muss unter dem Entry liegen.")
    rr = abs(target - entry) / risk if risk > 0 else 0.0
    if rr + 1e-9 < rr_min:
        reasons.append("Chance-Risiko %.2f:1 liegt unter dem Minimum %.2f:1." % (rr, rr_min))
    ok = not reasons
    record_step(tr, "exit", ok, {"reward_risk": rr, "risk_per_share_quote": risk, "stop_method": method}, t, reasons)
    save_trade(tr)
    print("Trade-ID: %s" % tr["id"])
    print("%s %s %s | Entry %s | Stop %s (%s) | Ziel %s | Risiko/Stueck %s %s | CRV %.2f:1"
          % (tr["ticker"], args.side.upper(), args.setup, num(entry), num(stop), method, num(target), num(risk), quote_cur, rr))
    if tr["flat_by"]:
        print("Spaetestens glattstellen: %s" % tr["flat_by"])
    print("EXIT-PLAN: %s" % ("PASS" if ok else "BLOCK - " + " ".join(reasons)))
    journal(cfg, "exit-plan", tr, result="PASS" if ok else "BLOCK",
            reason="; ".join(reasons) or (args.reason or method), t=t)
    return 0 if ok else 2


def cmd_timemachine(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    problems = require_previous(tr, "timemachine", cfg, t)
    if problems:
        raise TraderError("Blockiert: " + " ".join(problems))
    tmc = cfg["timemachine"]
    bars = list(bars_by_ticker(load_bars(args.returns_bars)).values())[0]
    rets = returns_from_bars(bars)
    sims = args.sims or tmc["simulations"]
    horizon = args.horizon or tmc["horizon_bars"]
    outcomes, kinds, sigma = simulate(tr["entry"], tr["stop"], tr["target"], tr["side"], rets, sims=sims,
                                      horizon=horizon, seed=args.seed, method=args.method)
    mc = summarize(outcomes)
    bt_items = read_json(args.backtest, []) if args.backtest else []
    bt = backtest_outcomes(bt_items, tr["side"])
    bts = summarize([r for _, r in bt])
    data_from, data_to = bars[0]["t"], bars[-1]["t"]
    reasons, warnings = [], []
    if tmc["block_negative_expectancy"] and mc["expectancy_r"] <= 0:
        reasons.append("Monte-Carlo-Erwartungswert %.2fR <= 0." % mc["expectancy_r"])
    if bts["n"] >= tmc["min_backtest_samples"] and tmc["block_negative_expectancy"] and bts["expectancy_r"] <= 0:
        reasons.append("Backtest-Erwartungswert %.2fR <= 0 bei n=%d." % (bts["expectancy_r"], bts["n"]))
    if bts["n"] < tmc["min_backtest_samples"]:
        warnings.append("Backtest-Stichprobe nur n=%d (< %d): statistisch kaum aussagekraeftig."
                        % (bts["n"], tmc["min_backtest_samples"]))
    rps_acc = per_share_risk(tr)
    cur = cfg["account_currency"]
    print("TIME MACHINE %s %s %s (Trade %s)" % (tr["ticker"], tr["side"], tr["setup"], tr["id"]))
    print("Datengrundlage: %d Bars von %s bis %s, Volatilitaet je Bar %.4f%% (%s), Seed %s"
          % (len(bars), iso(data_from, cfg["timezone"]), iso(data_to, cfg["timezone"]), sigma * 100, args.method, args.seed))
    print("\n1) Monte Carlo: %d Sitzungen, Horizont %d Bars" % (sims, horizon))
    print("   Ziel zuerst %.1f%% | Stop zuerst %.1f%% | offen bis Horizont %.1f%%"
          % (100.0 * kinds["target"] / sims, 100.0 * kinds["stop"] / sims, 100.0 * kinds["time"] / sims))
    print("   Trefferquote %.1f%% | Ø Gewinn %+.2fR | Ø Verlust %+.2fR | Erwartungswert %+.3fR (%s je Stueck)"
          % (mc["win_rate"] * 100, mc["avg_win_r"], mc["avg_loss_r"], mc["expectancy_r"], money(mc["expectancy_r"] * rps_acc, cur)))
    print("   Schlimmster Fall %+.2fR (%s je Stueck), 5%%-Quantil %+.2fR" % (mc["worst_r"], money(mc["worst_r"] * rps_acc, cur), mc["p05_r"]))
    print(histogram(outcomes))
    print("\n2) Backtest: %d historische Auftreten" % bts["n"])
    if bts["n"]:
        print("   Trefferquote %.1f%% | Ø Gewinn %+.2fR | Ø Verlust %+.2fR | Erwartungswert %+.3fR | schlimmster Fall %+.2fR"
              % (bts["win_rate"] * 100, bts["avg_win_r"], bts["avg_loss_r"], bts["expectancy_r"], bts["worst_r"]))
        dates = [d for d, _ in bt if d]
        if dates:
            print("   Zeitraum: %s bis %s" % (min(dates), max(dates)))
        print(histogram([r for _, r in bt], bins=8))
    for w in warnings:
        print("WARNUNG: " + w)
    print("\nVergangenheit ist keine Garantie. Die Simulation kennt keine Nachrichten, Handelsstopps oder "
          "Gaps jenseits der Daten; Stichprobe: %d Simulationen, %d historische Trades." % (sims, bts["n"]))
    ok = not reasons
    print("TIME MACHINE: %s" % ("PASS" if ok else "BLOCK - " + " ".join(reasons)))
    report = {"trade": tr["id"], "monte_carlo": mc, "kinds": kinds, "sigma": sigma, "sims": sims, "horizon": horizon,
              "seed": args.seed, "method": args.method, "backtest": bts, "backtest_rows": bt,
              "data_from": data_from.isoformat(), "data_to": data_to.isoformat(), "bars": len(bars),
              "warnings": warnings, "mc_outcomes_sample": outcomes[:2000]}
    write_json(path("reports", "%s-timemachine.json" % tr["id"]), report)
    record_step(tr, "timemachine", ok, {k: report[k] for k in ("monte_carlo", "kinds", "backtest", "sims", "warnings")}, t, reasons)
    save_trade(tr)
    journal(cfg, "timemachine", tr, result="PASS" if ok else "BLOCK",
            reason="MC E=%+.3fR WR=%.0f%% n=%d; BT E=%s n=%d" % (
                mc["expectancy_r"], mc["win_rate"] * 100, sims,
                ("%+.3fR" % bts["expectancy_r"]) if bts["n"] else "-", bts["n"]), t=t)
    return 0 if ok else 2


def cmd_risk(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    problems = require_previous(tr, "risk", cfg, t)
    if problems:
        record_step(tr, "risk", False, {}, t, problems)
        save_trade(tr)
        journal(cfg, "risk", tr, result="BLOCK", reason="; ".join(problems), t=t)
        print("RISK: BLOCK\n- " + "\n- ".join(problems))
        return 2
    ok, reasons, _, data = risk_check(cfg, tr, t)
    cur = cfg["account_currency"]
    print("RISK CHECK %s (%s, Modus %s)" % (tr["id"], tr["ticker"], "live" if cfg["is_live"] else "paper"))
    print("  Risiko je Stueck: %s" % money(data["risk_per_share"], cur))
    print("  max_loss_per_trade %s -> max. %d Stueck" % (money(data["max_loss_per_trade"], cur), data["q_by_max_loss"]))
    print("  max_position_size %s -> max. %d Stueck" % (money(data["max_position_value"], cur), data["q_by_position_size"]))
    print("  Tagesverlust frei %s von %s -> max. %d Stueck" % (money(max(data["daily_loss_left"], 0), cur), money(data["max_daily_loss"], cur), data["q_by_daily_loss"]))
    print("  offene Positionen %d von max. %d | CRV %.2f:1" % (data["open_positions"], cfg["max_open_positions"], data["reward_risk"]))
    print("RISK: %s" % ("PASS, erlaubt bis %d Stueck" % data["max_qty"] if ok else "BLOCK\n- " + "\n- ".join(reasons)))
    record_step(tr, "risk", ok, data, t, reasons)
    save_trade(tr)
    journal(cfg, "risk", tr, result="PASS" if ok else "BLOCK",
            reason="; ".join(reasons) or "max %d Stueck" % data["max_qty"], t=t)
    return 0 if ok else 2


def cmd_budget(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    problems = require_previous(tr, "budget", cfg, t)
    cur = cfg["account_currency"]
    if problems:
        record_step(tr, "budget", False, {}, t, problems)
        save_trade(tr)
        journal(cfg, "budget", tr, result="BLOCK", reason="; ".join(problems), t=t)
        print("BUDGET: BLOCK\n- " + "\n- ".join(problems))
        return 2
    rdata = tr["steps"]["risk"]["data"]
    max_loss = amount(cfg["max_loss_per_trade"], cfg)
    rps = per_share_risk(tr)
    raw_qty = max_loss / rps
    qty = math.floor(raw_qty)
    lines = ["Stueckzahl = max_loss_per_trade / (Entry - Stop)",
             "          = %s / |%s - %s| %s" % (money(max_loss, cur), num(tr["entry"]), num(tr["stop"]),
                                                  ("x FX %s" % tr["fx"]) if tr["fx"] != 1 else ""),
             "          = %s / %s = %.3f -> abgerundet %d" % (money(max_loss, cur), money(rps, cur), raw_qty, qty)]
    caps = []
    if qty > rdata["max_qty"]:
        caps.append("begrenzt durch /trader-risk auf %d (Positionsgroesse/Tageslimit)" % rdata["max_qty"])
        qty = rdata["max_qty"]
    st = account_state(cfg, t, exclude=tr["id"])
    available = cfg["account_budget"] - st["exposure"] + min(st["realized_today"], 0.0)
    if args.cash is not None:
        available = min(available, args.cash)
    price_acc = tr["entry"] * tr["fx"]
    q_cash = math.floor(available / price_acc) if price_acc > 0 else 0
    if qty > q_cash:
        caps.append("begrenzt durch verfuegbares Budget %s auf %d" % (money(available, cur), q_cash))
        qty = q_cash
    reasons = [] if qty >= 1 else ["Nach allen Grenzen bleibt weniger als 1 Stueck."]
    ok = not reasons
    tr["qty"] = qty if ok else 0
    for l in lines:
        print(l)
    for c in caps:
        print("  -> " + c)
    print("Positionswert: %d x %s = %s (verfuegbar %s)" % (qty, money(price_acc, cur), money(qty * price_acc, cur), money(available, cur)))
    print("Verlust am Stop: %d x %s = %s | Gewinn am Ziel: %s"
          % (qty, money(rps, cur), money(qty * rps, cur), money(qty * abs(tr["target"] - tr["entry"]) * tr["fx"], cur)))
    print("BUDGET: %s" % ("PASS, %d Stueck" % qty if ok else "BLOCK - " + " ".join(reasons)))
    record_step(tr, "budget", ok, {"qty": qty, "raw_qty": raw_qty, "available": available, "caps": caps}, t, reasons)
    save_trade(tr)
    journal(cfg, "budget", tr, result="PASS" if ok else "BLOCK", reason="; ".join(caps + reasons) or "volle Groesse", t=t)
    return 0 if ok else 2


def order_payload(cfg, tr):
    return {
        "symbol": tr["ticker"], "side": "buy" if tr["side"] == "long" else "sell", "qty": str(tr["qty"]),
        "type": "market", "time_in_force": "day", "order_class": "bracket",
        "take_profit_limit_price": "%.2f" % tr["target"], "stop_loss_stop_price": "%.2f" % tr["stop"],
    }


def broker_gate(cfg, tr, t):
    problems = require_previous(tr, "broker", cfg, t)
    if not problems:
        ok, reasons, _, _ = risk_check(cfg, tr, t, qty=tr.get("qty", 0))
        problems += ["Finaler Risk-Re-Check: " + r for r in reasons]
    if tr.get("status") not in ("planned",):
        problems.append("Trade hat Status %s; nur geplante Trades koennen gesendet werden." % tr.get("status"))
    if tr.get("mode") != ("live" if cfg["is_live"] else "paper"):
        problems.append("Der Modus hat sich seit dem Exit-Plan geaendert. Pipeline neu durchlaufen.")
    return problems


def cmd_broker(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    problems = broker_gate(cfg, tr, t)
    cur = cfg["account_currency"]
    if problems:
        print("BROKER: BLOCKIERT, keine Order.\n- " + "\n- ".join(problems))
        journal(cfg, "broker-block", tr, result="BLOCK", reason="; ".join(problems), t=t)
        return 2
    payload = order_payload(cfg, tr)
    mode = "LIVE - ECHTES GELD" if cfg["is_live"] else "PAPER"
    print("=" * 60)
    print("ORDER-ZUSAMMENFASSUNG  (Modus: %s, Broker: %s)" % (mode, cfg["broker"].get("name")))
    print("  Ticker:    %s (%s)" % (tr["ticker"], tr["exchange"]))
    print("  Richtung:  %s" % ("KAUF (long)" if tr["side"] == "long" else "VERKAUF (short)"))
    print("  Stueck:    %d" % tr["qty"])
    print("  Entry:     ca. %s %s (Market-Order, Fuellkurs kann abweichen)" % (num(tr["entry"]), tr["quote_currency"]))
    print("  Stop:      %s %s" % (num(tr["stop"]), tr["quote_currency"]))
    print("  Target:    %s %s" % (num(tr["target"]), tr["quote_currency"]))
    print("  Max. Verlust am Stop: %s | Gewinn am Ziel: %s"
          % (money(tr["qty"] * per_share_risk(tr), cur), money(tr["qty"] * abs(tr["target"] - tr["entry"]) * tr["fx"], cur)))
    print("  Bracket-Order: Stop und Target werden direkt mit angelegt.")
    print("  Pipeline: exit PASS -> timemachine PASS -> risk PASS -> budget PASS -> Re-Check PASS")
    print("  Order-JSON: %s" % json.dumps(payload))
    print("=" * 60)
    print('Senden? Antworte mit "Ja" fuer genau diese Order. Alles andere bricht ab.')
    record_step(tr, "broker", True, {"payload": payload, "mode": mode}, t)
    save_trade(tr)
    journal(cfg, "broker-preview", tr, result="wartet auf Ja", reason="Zusammenfassung gezeigt", t=t)
    return 0


def cmd_confirm(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    problems = broker_gate(cfg, tr, t)
    prev = tr.get("steps", {}).get("broker")
    if not prev or prev["status"] != "PASS":
        problems.append("Keine Order-Zusammenfassung gezeigt (trader.py broker).")
    else:
        if prev["fingerprint"] != fingerprint(tr):
            problems.append("Die Order hat sich seit der Zusammenfassung geaendert.")
        if t - parse_time(prev["at"]) > dt.timedelta(minutes=cfg["pipeline"]["confirm_within_minutes"]):
            problems.append("Die Zusammenfassung ist aelter als %d Minuten. Neu anzeigen."
                            % cfg["pipeline"]["confirm_within_minutes"])
        if prev["data"].get("payload") != order_payload(cfg, tr):
            problems.append("Order-JSON weicht von der gezeigten Zusammenfassung ab.")
    if not YES_RE.match(args.reply or ""):
        problems.append('Antwort %r ist kein ausdrueckliches "Ja". Order nicht freigegeben.' % (args.reply or ""))
    if problems:
        print("NICHT FREIGEGEBEN.\n- " + "\n- ".join(problems))
        journal(cfg, "confirm", tr, result="abgelehnt", reason="; ".join(problems), t=t)
        return 2
    tr["steps"]["confirm"] = {"status": "PASS", "at": t.isoformat(), "fingerprint": fingerprint(tr),
                              "reply": args.reply, "reasons": [], "data": {}}
    save_trade(tr)
    print("FREIGEGEBEN (%s). Sende jetzt GENAU dieses JSON an place_stock_order:" % ("LIVE" if cfg["is_live"] else "PAPER"))
    print(json.dumps(order_payload(cfg, tr)))
    journal(cfg, "confirm", tr, result="Ja erhalten", reason="Antwort: %s" % args.reply.strip(), t=t)
    return 0


def cmd_placed(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    if args.status == "placed":
        c = tr.get("steps", {}).get("confirm")
        if not c or c["fingerprint"] != fingerprint(tr):
            raise TraderError("Kein gueltiges Ja fuer diese Order aufgezeichnet. Platzierung ist ein Regelverstoss.")
        tr["status"] = "open"
        tr["broker_id"] = args.broker_id
        tr["filled_qty"] = args.filled_qty or tr["qty"]
        tr["filled_price"] = args.filled_price
        tr["opened_at"] = t.isoformat()
    else:
        tr["status"] = "planned"
        tr["steps"].pop("confirm", None)
    save_trade(tr)
    print("Gespeichert: %s -> %s" % (tr["id"], tr["status"]))
    journal(cfg, "order-%s" % args.status, tr, result=args.status,
            reason=(args.note or "") + (" Broker-ID %s" % args.broker_id if args.broker_id else ""),
            entry=args.filled_price or tr["entry"], size=tr.get("filled_qty") or tr.get("qty"), t=t)
    return 0


def cmd_close(args):
    cfg = load_config()
    t = now_utc(args)
    tr = load_trade(args.trade)
    if tr.get("status") != "open":
        raise TraderError("Trade %s ist nicht offen." % tr["id"])
    q = tr.get("filled_qty") or tr["qty"]
    e = tr.get("filled_price") or tr["entry"]
    pnl_q = (args.exit_price - e) if tr["side"] == "long" else (e - args.exit_price)
    res = q * pnl_q * tr["fx"] - (args.fees or 0.0)
    r_mult = pnl_q / abs(tr["entry"] - tr["stop"])
    tr.update({"status": "closed", "closed_at": t.isoformat(), "exit_price": args.exit_price, "result": res,
               "result_r": r_mult, "exit_reason": args.reason or ""})
    save_trade(tr)
    cur = cfg["account_currency"]
    print("Geschlossen: %s %d @ %s -> %s (%+.2fR)" % (tr["ticker"], q, num(args.exit_price), money(res, cur), r_mult))
    journal(cfg, "close", tr, result="%s (%+.2fR)" % (money(res, cur), r_mult), reason=args.reason or "", t=t)
    return 0


def cmd_status(args):
    cfg = load_config()
    t = now_utc(args)
    st = account_state(cfg, t)
    cur = cfg["account_currency"]
    max_daily = amount(cfg["max_daily_loss"], cfg)
    print("Modus: %s | offene Positionen %d/%d | offenes Risiko %s | realisiert heute %s | Tageslimit %s"
          % ("LIVE" if cfg["is_live"] else "PAPER", st["open_positions"], cfg["max_open_positions"],
             money(st["open_risk"], cur), money(st["realized_today"], cur), money(max_daily, cur)))
    for tr in all_trades():
        if tr.get("status") in ("open", "planned"):
            done = [s for s in PIPELINE + ["confirm"] if tr.get("steps", {}).get(s, {}).get("status") == "PASS"]
            print("  %s %s %s %s qty=%s Schritte: %s" % (tr["id"], tr["status"], tr["ticker"], tr["setup"],
                                                        tr.get("filled_qty") or tr.get("qty", "-"), " > ".join(done) or "-"))
    return 0


def cmd_journal(args):
    cfg = load_config()
    if args.show:
        p = path("journal.csv")
        if not os.path.exists(p):
            print("(Journal leer)")
            return 0
        with open(p, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        for r in rows[:1] + rows[1:][-args.tail:]:
            print(" | ".join(r))
        return 0
    journal(cfg, args.action, result=args.result or "", reason=args.reason or "", ticker=args.ticker or "",
            setup=args.setup or "", t=now_utc(args), entry=args.entry, stop=args.stop, target=args.target, size=args.size)
    print("Journal: %s" % args.action)
    return 0


def cmd_news_react(args):
    cfg = load_config()
    nc = cfg["news"]
    bars = list(bars_by_ticker(load_bars(args.bars)).values())[0]
    t0 = parse_time(args.time)
    window = args.window or nc["reaction_window_minutes"]
    before = [b for b in bars if b["t"] < t0]
    after = [b for b in bars if t0 <= b["t"] < t0 + dt.timedelta(minutes=window)]
    if not before or not after:
        print("Keine Bars um %s; Reaktion nicht messbar." % iso(t0, cfg["timezone"]))
        return 0
    ref = before[-1]["c"]
    ext = max(after, key=lambda b: abs(b["c"] - ref))
    move = (ext["c"] - ref) / ref * 100
    avg = avg_volume(before, cfg["entry"]["volume_lookback"])
    vr = (max(b["v"] for b in after) / avg) if avg else 0.0
    moving = abs(move) >= (args.min_move or nc["min_move_pct"]) and vr >= (args.min_volume_ratio or nc["min_volume_ratio"])
    print("Kursreaktion %s in %d Min.: %+.2f%% (von %s auf %s), Volumenspitze %.1fx Ø -> %s"
          % (args.ticker or "", window, move, num(ref), num(ext["c"]), vr, "KURSBEWEGEND" if moving else "keine deutliche Reaktion"))
    if args.journal:
        journal(cfg, "news", result="kursbewegend" if moving else "neutral", ticker=args.ticker or "",
                reason="%s | %+.2f%% / %.1fx Vol" % ((args.headline or "")[:120], move, vr), t=now_utc(args))
    return 0


def cmd_alert(args):
    cfg = load_config()
    t = now_utc(args)
    p = path("alerts.json")
    alerts = read_json(p, [])
    if args.action == "add":
        if args.kind == "open":
            exname, _ = exchange(cfg, args.exchange)
            when = next_open(cfg, exname, t) - dt.timedelta(minutes=args.before or 0)
            a = {"id": uuid.uuid4().hex[:8], "kind": "open", "exchange": exname, "at": when.isoformat(), "fired": False,
                 "note": args.note or ""}
            print("Alarm %s: Boersenoeffnung %s um %s" % (a["id"], exname, iso(when, cfg["timezone"])))
        else:
            if args.level is None or not args.ticker:
                raise TraderError("--ticker und --level noetig.")
            direction = args.direction or {"stop": "below", "target": "above"}.get(args.kind, "above")
            a = {"id": uuid.uuid4().hex[:8], "kind": args.kind, "ticker": args.ticker.upper(), "level": args.level,
                 "direction": direction, "fired": False, "note": args.note or "", "trade": args.trade or ""}
            print("Alarm %s: %s %s %s %s" % (a["id"], a["ticker"], a["kind"], "<=" if direction == "below" else ">=", num(args.level)))
        alerts.append(a)
        write_json(p, alerts)
        journal(cfg, "alert-add", result=a["kind"], ticker=a.get("ticker", ""), reason=a.get("note", ""), t=t)
    elif args.action == "from-trade":
        tr = load_trade(args.trade)
        long = tr["side"] == "long"
        for kind, lvl, d in (("stop", tr["stop"], "below" if long else "above"), ("target", tr["target"], "above" if long else "below")):
            alerts.append({"id": uuid.uuid4().hex[:8], "kind": kind, "ticker": tr["ticker"], "level": lvl,
                           "direction": d, "fired": False, "note": "Trade %s" % tr["id"], "trade": tr["id"]})
            print("Alarm: %s %s %s" % (tr["ticker"], kind, num(lvl)))
        write_json(p, alerts)
        journal(cfg, "alert-add", tr, result="stop+target", reason="aus Trade", t=t)
    elif args.action == "list":
        for a in alerts:
            if a.get("fired") and not args.all:
                continue
            desc = a.get("at") and iso(parse_time(a["at"]), cfg["timezone"]) or "%s %s %s" % (a.get("ticker"), a.get("direction"), a.get("level"))
            print("%s %-7s %s %s %s" % (a["id"], a["kind"], desc, "(ausgeloest)" if a.get("fired") else "", a.get("note", "")))
    elif args.action == "check":
        prices = json.loads(args.prices) if args.prices else {}
        rang = 0
        for a in alerts:
            if a.get("fired"):
                continue
            hit = False
            if a["kind"] == "open":
                hit = t >= parse_time(a["at"])
                msg = "Boersenoeffnung %s" % a["exchange"]
            else:
                px = prices.get(a["ticker"])
                if px is None:
                    continue
                hit = px <= a["level"] if a["direction"] == "below" else px >= a["level"]
                msg = "%s %s: Kurs %s %s %s" % (a["ticker"], a["kind"], num(px), "<=" if a["direction"] == "below" else ">=", num(a["level"]))
            if hit:
                a["fired"] = True
                a["fired_at"] = t.isoformat()
                rang += 1
                print("ALARM: %s (%s) %s" % (msg, iso(t, cfg["timezone"]), a.get("note", "")))
                journal(cfg, "alert", result=a["kind"], ticker=a.get("ticker", ""), reason=msg, t=t)
        write_json(p, alerts)
        if not rang:
            print("Kein Alarm ausgeloest.")
    elif args.action == "remove":
        alerts = [a for a in alerts if a["id"] != args.id]
        write_json(p, alerts)
        print("Entfernt: %s" % args.id)
    return 0


# --------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(prog="trader.py", description="Werkzeug des trader-Plugins (Paper Trading ist Standard).")
    p.add_argument("--now", help="Uhrzeit fixieren, z. B. '2026-11-05 16:15' (Berlin) oder ISO mit Zone")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="trader/trader-config.json aus der Vorlage anlegen")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("config", help="Config pruefen und anzeigen")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("env", help="pruefen, welche API-Key-Variablen gesetzt sind (ohne Werte)")
    s.add_argument("names", nargs="*")
    s.set_defaults(fn=cmd_env)

    s = sub.add_parser("scan", help="Setups in Bars finden (ORB 5/15/30, Bull/Bear-Flag, Oversold-Bounce)")
    s.add_argument("--bars", required=True, help="JSON: {TICKER: [bars]} oder [bars]")
    s.add_argument("--ticker")
    s.add_argument("--exchange")
    s.add_argument("--min-score", type=int, default=0)
    s.add_argument("--json", action="store_true")
    s.add_argument("--journal", action="store_true")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("entry", help="Einstiegsbedingung auf dem letzten abgeschlossenen Bar pruefen")
    s.add_argument("--bars", required=True)
    s.add_argument("--level", type=float, required=True)
    s.add_argument("--direction", choices=["above", "below"], default="above")
    s.add_argument("--interval", type=int, default=5, help="Bar-Laenge in Minuten (1 oder 5)")
    s.add_argument("--volume-multiple", type=float)
    s.add_argument("--ticker")
    s.add_argument("--setup")
    s.add_argument("--journal", action="store_true")
    s.set_defaults(fn=cmd_entry)

    s = sub.add_parser("exit", help="Exit-Plan anlegen: startet einen Trade (Schritt 1)")
    s.add_argument("--ticker", required=True)
    s.add_argument("--setup", required=True)
    s.add_argument("--side", choices=["long", "short"], default="long")
    s.add_argument("--entry", type=float, required=True)
    s.add_argument("--stop", type=float)
    s.add_argument("--target", type=float)
    s.add_argument("--bars", help="Bars fuer --stop-mode")
    s.add_argument("--stop-mode", choices=["range", "atr"], default="range")
    s.add_argument("--or-minutes", type=int, default=15)
    s.add_argument("--atr-mult", type=float, default=1.5)
    s.add_argument("--buffer", type=float, default=0.0)
    s.add_argument("--exchange")
    s.add_argument("--currency")
    s.add_argument("--fx", type=float, help="Kontowaehrung je 1 Kurswaehrung, z. B. 0.92 (EUR je USD)")
    s.add_argument("--fx-source")
    s.add_argument("--flat-by")
    s.add_argument("--data-basis", help="Quelle und Zeitstempel der Kursdaten")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_exit)

    s = sub.add_parser("timemachine", help="Monte Carlo + Backtest (Schritt 2)")
    s.add_argument("trade")
    s.add_argument("--returns-bars", required=True, help="historische Bars des Tickers fuer die Volatilitaet")
    s.add_argument("--backtest", help="JSON-Liste historischer Auftreten des Setups")
    s.add_argument("--sims", type=int)
    s.add_argument("--horizon", type=int)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--method", choices=["gbm", "bootstrap"], default="gbm")
    s.set_defaults(fn=cmd_timemachine)

    s = sub.add_parser("risk", help="Limits pruefen (Schritt 3)")
    s.add_argument("trade")
    s.set_defaults(fn=cmd_risk)

    s = sub.add_parser("budget", help="Positionsgroesse berechnen (Schritt 4)")
    s.add_argument("trade")
    s.add_argument("--cash", type=float, help="verfuegbares Kapital laut Broker (Kontowaehrung)")
    s.set_defaults(fn=cmd_budget)

    s = sub.add_parser("broker", help="Order-Zusammenfassung zeigen (Schritt 5), sendet nichts")
    s.add_argument("trade")
    s.set_defaults(fn=cmd_broker)

    s = sub.add_parser("confirm", help='die Antwort des Nutzers pruefen; nur "Ja" gibt frei')
    s.add_argument("trade")
    s.add_argument("--reply", required=True, help="Antwort des Nutzers, woertlich")
    s.set_defaults(fn=cmd_confirm)

    s = sub.add_parser("placed", help="Ergebnis der Broker-Antwort festhalten")
    s.add_argument("trade")
    s.add_argument("--status", choices=["placed", "rejected", "failed", "denied"], default="placed")
    s.add_argument("--broker-id")
    s.add_argument("--filled-qty", type=int)
    s.add_argument("--filled-price", type=float)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_placed)

    s = sub.add_parser("close", help="Position geschlossen: Ergebnis ins Journal")
    s.add_argument("trade")
    s.add_argument("--exit-price", type=float, required=True)
    s.add_argument("--fees", type=float)
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_close)

    s = sub.add_parser("status", help="offene/geplante Trades und Tageslimits")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("journal", help="Zeile ins Journal schreiben oder anzeigen")
    s.add_argument("--show", action="store_true")
    s.add_argument("--tail", type=int, default=20)
    s.add_argument("--action", default="notiz")
    s.add_argument("--ticker")
    s.add_argument("--setup")
    s.add_argument("--entry", type=float)
    s.add_argument("--stop", type=float)
    s.add_argument("--target", type=float)
    s.add_argument("--size")
    s.add_argument("--result")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_journal)

    s = sub.add_parser("news-react", help="Kursreaktion nach einer Meldung messen")
    s.add_argument("--bars", required=True)
    s.add_argument("--time", required=True, help="Zeitpunkt der Meldung")
    s.add_argument("--ticker")
    s.add_argument("--headline")
    s.add_argument("--window", type=int)
    s.add_argument("--min-move", type=float)
    s.add_argument("--min-volume-ratio", type=float)
    s.add_argument("--journal", action="store_true")
    s.set_defaults(fn=cmd_news_react)

    s = sub.add_parser("alert", help="Preisalarme")
    s.add_argument("action", choices=["add", "from-trade", "list", "check", "remove"])
    s.add_argument("--kind", choices=["price", "stop", "target", "open"], default="price")
    s.add_argument("--ticker")
    s.add_argument("--level", type=float)
    s.add_argument("--direction", choices=["above", "below"])
    s.add_argument("--exchange")
    s.add_argument("--before", type=int, help="Minuten vor der Eroeffnung")
    s.add_argument("--trade")
    s.add_argument("--prices", help='JSON, z. B. {"TSLA": 361.2}')
    s.add_argument("--id")
    s.add_argument("--note")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_alert)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except TraderError as e:
        print("FEHLER: %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
