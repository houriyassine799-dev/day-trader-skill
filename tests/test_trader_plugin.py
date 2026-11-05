"""Tests fuer das Plugin plugins/trader (offline, synthetische Daten)."""

import contextlib
import csv
import datetime as dt
import importlib.util
import io
import json
import os
import random
import re
import shutil
import tempfile
import unittest
from zoneinfo import ZoneInfo

from _load import ROOT

PLUGIN = os.path.join(ROOT, "plugins", "trader")
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-pelosi"]
NY = ZoneInfo("America/New_York")
NOW = "2026-11-05T10:16:00-05:00"  # Donnerstag, 16:16 Berlin


def load_tool():
    spec = importlib.util.spec_from_file_location("trader_plugin_tool", os.path.join(PLUGIN, "scripts", "trader.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_tool()


def history(days=20, seed=1):
    rnd = random.Random(seed)
    bars, p, d, n = [], 100.0, dt.date(2026, 10, 8), 0
    while n < days:
        if d.weekday() < 5:
            t = dt.datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)
            for _ in range(78):
                o = p
                p *= 1 + rnd.gauss(0, 0.002)
                bars.append({"t": t.isoformat(), "o": o, "h": max(o, p) * 1.001, "l": min(o, p) * 0.999,
                             "c": p, "v": 10000 + rnd.randint(0, 3000)})
                t += dt.timedelta(minutes=5)
            n += 1
        d += dt.timedelta(days=1)
    return bars


def today_orb():
    t = dt.datetime(2026, 11, 5, 9, 30, tzinfo=NY)
    seq = [(100, 100.8, 99.6, 100.5), (100.5, 100.9, 100.2, 100.6), (100.6, 100.7, 100.1, 100.3)]
    seq += [(100.3, 100.7, 100.2, 100.5)] * 5 + [(100.5, 101.6, 100.4, 101.4)]
    return [{"t": (t + dt.timedelta(minutes=5 * i)).isoformat(), "o": o, "h": h, "l": l, "c": c,
             "v": 30000 if i == 8 else 10000} for i, (o, h, l, c) in enumerate(seq)]


class Structure(unittest.TestCase):
    def test_manifest_and_marketplace(self):
        with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["name"], "trader")
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
            market = json.load(fh)
        entry = [p for p in market["plugins"] if p["name"] == "trader"]
        self.assertEqual(entry[0]["source"], "./plugins/trader")

    def test_eleven_skills_with_rules(self):
        found = sorted(os.listdir(os.path.join(PLUGIN, "skills")))
        self.assertEqual(found, sorted(SKILLS))
        for s in SKILLS:
            with open(os.path.join(PLUGIN, "skills", s, "SKILL.md"), encoding="utf-8") as fh:
                text = fh.read()
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            self.assertTrue(m, s)
            self.assertIn("name: %s\n" % s, m.group(1) + "\n")
            self.assertIn("description:", m.group(1))
            for must in ("## Inputs", "## Outputs", "## Regeln", "## Globale Regeln",
                         "keine Anlageberatung", '"Ja"', "journal.csv",
                         "/trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker"):
                self.assertIn(must, text, "%s: %s" % (s, must))

    def test_example_config_is_paper_and_has_no_secrets(self):
        with open(os.path.join(PLUGIN, "trader-config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["mode"], "paper")
        for k in ("account_budget", "max_loss_per_trade", "max_daily_loss", "max_open_positions",
                  "max_position_size", "watchlist", "trading_hours", "timezone", "broker", "exchanges"):
            self.assertIn(k, cfg)
        self.assertEqual(cfg["timezone"], "Europe/Berlin")
        self.assertEqual(T.find_secrets(cfg), [])

    def test_mcp_json_reads_keys_from_env_only(self):
        with open(os.path.join(PLUGIN, ".mcp.json"), encoding="utf-8") as fh:
            mcp = json.load(fh)
        self.assertIn("alpaca", mcp["mcpServers"])
        alpaca_env = mcp["mcpServers"]["alpaca"]["env"]
        self.assertEqual(alpaca_env["ALPACA_PAPER_TRADE"], "${ALPACA_PAPER_TRADE:-true}")
        for server in mcp["mcpServers"].values():
            for v in server.get("env", {}).values():
                self.assertRegex(v, r"^\$\{[A-Z_]+(:-[^}]*)?\}$")


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["TRADER_HOME"] = self.dir
        os.environ.pop("TRADER_CONFIG", None)
        self.run_cmd("init")
        self.write("hist.json", history())
        self.write("tsla.json", history()[-200:] + today_orb())

    def tearDown(self):
        shutil.rmtree(self.dir)
        os.environ.pop("TRADER_HOME", None)

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return os.path.join(self.dir, name)

    def set_config(self, **kw):
        p = os.path.join(self.dir, "trader-config.json")
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg.update(kw)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def run_cmd(self, *argv, now=NOW):
        out = io.StringIO()
        err = io.StringIO()
        args = (["--now", now] if now else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = T.main(args)
        return code, out.getvalue() + err.getvalue()

    def plan(self, **kw):
        argv = ["exit", "--ticker", "TSLA", "--setup", "ORB-15", "--entry", "101.4",
                "--bars", os.path.join(self.dir, "tsla.json"), "--fx", "0.87"]
        for k, v in kw.items():
            argv += ["--" + k.replace("_", "-"), str(v)]
        code, out = self.run_cmd(*argv)
        tid = re.search(r"Trade-ID: (\S+)", out).group(1)
        return code, out, tid

    def bt(self):
        return self.write("bt.json", [{"date": "2026-09-%02d" % (i + 1), "r": r}
                                      for i, r in enumerate([2.0, -1.0, 2.0, -1.0, 0.5] * 5)])

    def through_budget(self):
        _, _, tid = self.plan()
        code, out = self.run_cmd("timemachine", tid, "--returns-bars", os.path.join(self.dir, "hist.json"),
                                 "--backtest", self.bt())
        self.assertEqual(code, 0, out)
        self.assertIn("Vergangenheit ist keine Garantie", out)
        self.assertIn("10000 Simulationen", out)
        code, out = self.run_cmd("risk", tid)
        self.assertEqual(code, 0, out)
        code, out = self.run_cmd("budget", tid)
        self.assertEqual(code, 0, out)
        return tid

    def test_scan_finds_orb(self):
        code, out = self.run_cmd("scan", "--bars", self.write("scan.json", {"TSLA": history()[-200:] + today_orb()}))
        self.assertEqual(code, 0)
        self.assertIn("| TSLA | ORB-15 long | 101.40 |", out)

    def test_entry_triggers_without_order(self):
        code, out = self.run_cmd("entry", "--bars", os.path.join(self.dir, "tsla.json"), "--level", "100.9")
        self.assertIn("AUSGELOEST", out)
        self.assertIn("KEINE Order", out)
        # laufender Bar zaehlt nicht: um 10:12 ist der 10:10-Bar noch offen
        code, out = self.run_cmd("entry", "--bars", os.path.join(self.dir, "tsla.json"), "--level", "100.9",
                                 now="2026-11-05T10:12:00-05:00")
        self.assertIn("WARTEN", out)

    def test_exit_plan_uses_range_low_and_two_to_one(self):
        code, out, tid = self.plan()
        self.assertEqual(code, 0)
        tr = T.load_trade(tid)
        self.assertEqual(tr["stop"], 99.6)
        self.assertEqual(tr["target"], 105.0)
        self.assertIn("EXIT-PLAN: PASS", out)

    def test_exit_blocks_bad_reward_risk(self):
        code, out, _ = self.plan(target=102)
        self.assertEqual(code, 2)
        self.assertIn("Chance-Risiko", out)

    def test_full_pipeline_to_paper_order(self):
        tid = self.through_budget()
        tr = T.load_trade(tid)
        # 2500 EUR / (101.4 * 0.87) = 28.3 -> 28 (Positionsgroesse greift vor 100 EUR / 1.566 = 63)
        self.assertEqual(tr["qty"], 28)
        code, out = self.run_cmd("broker", tid)
        self.assertEqual(code, 0, out)
        self.assertIn("Modus: PAPER", out)
        self.assertIn('"order_class": "bracket"', out)
        self.assertIn('"stop_loss_stop_price": "99.60"', out)
        for reply in ("ok", "yes", "sure", "ja, aber 30 Stueck", ""):
            code, out = self.run_cmd("confirm", tid, "--reply", reply)
            self.assertEqual(code, 2, reply)
        code, out = self.run_cmd("confirm", tid, "--reply", "Ja")
        self.assertEqual(code, 0, out)
        self.assertIn("FREIGEGEBEN (PAPER)", out)
        code, out = self.run_cmd("placed", tid, "--broker-id", "x1", "--filled-price", "101.45")
        self.assertEqual(code, 0)
        code, out = self.run_cmd("close", tid, "--exit-price", "105")
        self.assertIn("+1.97R", out)
        with open(os.path.join(self.dir, "journal.csv"), encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["Zeit", "Trade-ID", "Aktion", "Modus", "Ticker", "Setup", "Entry", "Stop",
                                   "Target", "Größe", "Ergebnis", "Begründung"])
        actions = [r[2] for r in rows[1:]]
        for a in ("exit-plan", "timemachine", "risk", "budget", "broker-preview", "confirm", "order-placed", "close"):
            self.assertIn(a, actions)
        self.assertTrue(all(r[3] == "paper" for r in rows[1:]))

    def test_skipping_a_step_blocks(self):
        _, _, tid = self.plan()
        code, out = self.run_cmd("risk", tid)
        self.assertEqual(code, 2)
        self.assertIn("/trader-timemachine fehlt", out)
        code, out = self.run_cmd("broker", tid)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKIERT", out)

    def test_changed_plan_invalidates_later_steps(self):
        tid = self.through_budget()
        tr = T.load_trade(tid)
        tr["stop"] = 100.9
        T.save_trade(tr)
        code, out = self.run_cmd("broker", tid)
        self.assertEqual(code, 2)
        self.assertIn("geaendert", out)

    def test_stale_approvals_block(self):
        tid = self.through_budget()
        code, out = self.run_cmd("broker", tid, now="2026-11-05T11:00:00-05:00")
        self.assertEqual(code, 2)
        self.assertIn("aelter als 30 Minuten", out)

    def test_confirm_requires_preview_shown(self):
        tid = self.through_budget()
        code, out = self.run_cmd("confirm", tid, "--reply", "Ja")
        self.assertEqual(code, 2)
        self.assertIn("Keine Order-Zusammenfassung", out)

    def test_placed_without_yes_is_refused(self):
        tid = self.through_budget()
        self.run_cmd("broker", tid)
        code, out = self.run_cmd("placed", tid, "--broker-id", "x")
        self.assertEqual(code, 3)

    def test_risk_blocks_max_open_positions(self):
        self.set_config(max_open_positions=1)
        tid = self.through_budget()
        self.run_cmd("broker", tid)
        self.run_cmd("confirm", tid, "--reply", "ja")
        self.run_cmd("placed", tid, "--broker-id", "x")
        _, _, tid2 = self.plan()
        self.run_cmd("timemachine", tid2, "--returns-bars", os.path.join(self.dir, "hist.json"), "--backtest", self.bt())
        code, out = self.run_cmd("risk", tid2)
        self.assertEqual(code, 2)
        self.assertIn("max_open_positions", out)

    def test_risk_blocks_outside_hours_and_shorts(self):
        _, _, tid = self.plan()
        self.run_cmd("timemachine", tid, "--returns-bars", os.path.join(self.dir, "hist.json"), "--backtest", self.bt())
        code, out = self.run_cmd("risk", tid, now="2026-11-05T10:20:00-05:00")
        self.assertEqual(code, 0, out)
        code, out = self.run_cmd("risk", tid, now="2026-11-07T10:20:00-05:00")  # Samstag
        self.assertEqual(code, 2)
        code, out, _ = self.plan(side="short", stop=103, target=97)
        self.assertEqual(code, 0)

    def test_negative_expectancy_blocks(self):
        _, _, tid = self.plan()
        bad = self.write("bad.json", [{"date": "d%d" % i, "r": -1.0} for i in range(25)])
        code, out = self.run_cmd("timemachine", tid, "--returns-bars", os.path.join(self.dir, "hist.json"),
                                 "--backtest", bad)
        self.assertEqual(code, 2)
        self.assertIn("Backtest-Erwartungswert", out)

    def test_small_backtest_warns(self):
        _, _, tid = self.plan()
        small = self.write("small.json", [{"date": "d1", "r": 2.0}, {"date": "d2", "r": -1.0}])
        code, out = self.run_cmd("timemachine", tid, "--returns-bars", os.path.join(self.dir, "hist.json"),
                                 "--backtest", small)
        self.assertIn("WARNUNG: Backtest-Stichprobe nur n=2", out)

    def test_secret_in_config_is_refused(self):
        self.set_config(broker={"name": "Alpaca", "api_key": "PKABCDEF123456"})
        code, out = self.run_cmd("config")
        self.assertEqual(code, 3)
        self.assertIn("Klartext", out)
        self.assertNotIn("PKABCDEF123456", out)

    def test_live_only_when_exactly_live(self):
        self.set_config(mode="LIVE")
        code, out = self.run_cmd("config")
        self.assertEqual(code, 3)
        self.set_config(mode="live")
        code, out = self.run_cmd("config")
        self.assertIn("LIVE", out)
        os.remove(os.path.join(self.dir, "trader-config.json"))
        os.environ["TRADER_CONFIG"] = self.write("other.json", {"account_budget": 5000})
        try:
            cfg = T.load_config()
            self.assertFalse(cfg["is_live"])
        finally:
            os.environ.pop("TRADER_CONFIG")

    def test_mode_change_after_plan_blocks(self):
        tid = self.through_budget()
        self.set_config(mode="live")
        code, out = self.run_cmd("broker", tid)
        self.assertEqual(code, 2)
        self.assertIn("Modus", out)

    def test_env_never_prints_values(self):
        os.environ["ALPACA_API_KEY"] = "SUPERSECRET"
        try:
            code, out = self.run_cmd("env", now=None)
        finally:
            os.environ.pop("ALPACA_API_KEY")
        self.assertIn("ALPACA_API_KEY", out)
        self.assertNotIn("SUPERSECRET", out)

    def test_alerts(self):
        tid = self.through_budget()
        self.run_cmd("alert", "from-trade", "--trade", tid)
        code, out = self.run_cmd("alert", "check", "--prices", '{"TSLA": 99.5}')
        self.assertIn("ALARM: TSLA stop", out)
        code, out = self.run_cmd("alert", "check", "--prices", '{"TSLA": 99.5}')
        self.assertIn("Kein Alarm", out)
        code, out = self.run_cmd("alert", "add", "--kind", "open", "--exchange", "NYSE",
                                 now="2026-11-05T20:00:00-05:00")
        self.assertIn("2026-11-06 15:30:00 CET", out)

    def test_news_reaction(self):
        bars = history(2)[-60:]
        t0 = dt.datetime.fromisoformat(bars[30]["t"])
        for b in bars[31:34]:
            b["c"] = bars[30]["c"] * 1.03
            b["v"] = 60000
        code, out = self.run_cmd("news-react", "--bars", self.write("n.json", bars), "--time", t0.isoformat(),
                                 "--ticker", "TSLA")
        self.assertIn("KURSBEWEGEND", out)


if __name__ == "__main__":
    unittest.main()
