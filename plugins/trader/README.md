# trader: Day-Trading-Plugin für Claude Code

11 Slash-Commands, die als Pipeline zusammenarbeiten: vom Chart-Scan bis zur
Paper-Order, mit Exit-Plan, Simulation, Risikoprüfung und Positionsgröße vor
jeder Order. **Paper Trading ist Standard. Keine Order ohne dein "Ja".**

> Analyse und Werkzeug, keine Anlageberatung. Die meisten Day-Trader
> verlieren Geld. Nutze zuerst ausschließlich ein Paper-Konto.

## Die 11 Commands

| Command | Rolle | Pipeline |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner: ORB 5/15/30, Bull-/Bear-Flags, Oversold-Bounces | Ideen |
| `/trader-news` | News Monitor: Schlagzeilen plus gemessene Kursreaktion | Ideen |
| `/trader-entry` | Entry Timer: meldet den Auslöser Kerze für Kerze, ohne Order | Ideen |
| `/trader-exit` | Exit Planner: Stop und Ziel (mind. 2:1) vor dem Einstieg | **1** |
| `/trader-timemachine` | 10.000 Monte-Carlo-Sessions plus Backtest des Setups | **2** |
| `/trader-risk` | Risk Manager: alle Limits, kann nicht übersprungen werden | **3** |
| `/trader-budget` | Budget Manager: Stückzahl = max. Verlust / (Entry - Stop) | **4** |
| `/trader-broker` | Order Executor: Zusammenfassung, "Ja", Bracket-Order | **5** |
| `/trader-alerts` | Kursmarken, Stops, Ziele, Börsenöffnung | danach |
| `/trader-analyst` | Earnings Analyst über SEC EDGAR (10-Q, 10-K, 8-K) | Recherche |
| `/trader-pelosi` | Congress Trade Tracker (House Clerk, Senate eFD) | Recherche |

Als Plugin installiert heißen die Commands mit Namensraum
`/trader:trader-scanner` usw. Wer die kurzen Namen `/trader-scanner` möchte,
kopiert die Ordner aus `skills/` nach `~/.claude/skills/` (dann aber auch
`scripts/` mitnehmen und `${CLAUDE_PLUGIN_ROOT}` in den SKILL.md durch den
Pfad ersetzen).

## Globale Regeln

1. **Paper ist Standard.** Echtes Geld nur, wenn `trader/trader-config.json` exakt `"mode": "live"` enthält. Jeder andere Wert ist Paper.
2. **Kein Senden ohne "Ja".** `/trader-broker` zeigt eine Zusammenfassung und gibt die Order erst frei, wenn deine Antwort ein ausdrückliches "Ja" ist (geprüft von `trader.py confirm`). Auch im Paper-Modus.
3. **Feste Reihenfolge:** `/trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker`. Jeder Schritt speichert PASS/BLOCK mit Zeitstempel und einem Fingerabdruck des Plans. Fehlt ein Schritt, ist einer BLOCK, älter als 30 Minuten oder wurde der Plan danach geändert, blockiert `/trader-broker`. Direkt vor dem Senden läuft die Risikoprüfung mit der endgültigen Stückzahl noch einmal.
4. **Keys nur aus Umgebungsvariablen.** Die Config enthält nur Variablennamen; `trader.py` bricht ab, wenn darin ein Key im Klartext steht. `trader.py env` zeigt nur "gesetzt"/"fehlt".
5. **Analyse, keine Beratung.** Jede Ausgabe nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. **Journal.** Jede Aktion landet in `trader/journal.csv`:
   `Zeit, Trade-ID, Aktion, Modus, Ticker, Setup, Entry, Stop, Target, Größe, Ergebnis, Begründung`.

## Aufbau

```
plugins/trader/
├── .claude-plugin/plugin.json
├── .mcp.json                     Alpaca (Broker + Marktdaten), Polygon (Marktdaten + News), SEC EDGAR
├── trader-config.example.json    Vorlage der Konfiguration
├── scripts/trader.py             gemeinsames Werkzeug (nur Python-Standardbibliothek, 3.9+)
└── skills/
    ├── trader-scanner/SKILL.md     ├── trader-budget/SKILL.md
    ├── trader-news/SKILL.md        ├── trader-broker/SKILL.md
    ├── trader-entry/SKILL.md       ├── trader-alerts/SKILL.md
    ├── trader-exit/SKILL.md        ├── trader-analyst/SKILL.md
    ├── trader-timemachine/SKILL.md └── trader-pelosi/SKILL.md
    └── trader-risk/SKILL.md
```

`trader.py` spricht nie selbst mit einem Broker oder einer Daten-API. Claude
holt Daten über die MCP-Server, speichert sie als JSON unter `trader/data/`,
und das Werkzeug rechnet, prüft und protokolliert. Alles Laufzeit-Material
liegt im Projekt unter `trader/` (Config, Journal, Trades, Alarme, Reports).

## Einrichtung

### 1. Plugin installieren

```
/plugin marketplace add houriyassine799-dev/day-trader-skill
/plugin install trader@day-trader-skill
```

### 2. Voraussetzungen

- Python 3.9 oder neuer (für `scripts/trader.py`).
- [uv](https://docs.astral.sh/uv/) (liefert `uvx`, startet die MCP-Server).
- Optional `pdftotext` (poppler) für die House-PDFs in `/trader-pelosi`.

### 3. API-Keys als Umgebungsvariablen

Nur setzen, was du nutzt. Niemals in den Chat, in die Config oder ins Repo.

```bash
export ALPACA_API_KEY="..."          # Alpaca PAPER-Key (Dashboard > Paper Trading > API Keys)
export ALPACA_SECRET_KEY="..."
export ALPACA_PAPER_TRADE="true"     # Standard; nur du selbst stellst je auf false
export POLYGON_API_KEY="..."         # optional: Marktdaten + News
export FINNHUB_API_KEY="..."         # optional: News, Earnings-Konsens
export BENZINGA_API_KEY="..."        # optional: News
export QUIVER_API_KEY="..."          # optional: aufbereitete Congress-Trades
export SEC_EDGAR_USER_AGENT="Vorname Nachname du@beispiel.de"   # Pflicht der SEC für EDGAR
```

Am besten in einer Datei außerhalb des Repos (z. B. `~/.trader.env`, `chmod 600`)
und vor dem Start von Claude Code laden. In Claude Code im Web die Keys als
Netzwerk-Secrets der Umgebung hinterlegen.

| Quelle | Connector | Variable |
| --- | --- | --- |
| Alpaca (Broker, Marktdaten) | `alpaca` in `.mcp.json` (`uvx alpaca-mcp-server`) | `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER_TRADE` |
| Polygon.io (Bars, News) | `polygon` in `.mcp.json` (`polygon-io/mcp_polygon`) | `POLYGON_API_KEY` |
| SEC EDGAR | `sec-edgar` in `.mcp.json` (`uvx sec-edgar-mcp`) | `SEC_EDGAR_USER_AGENT` |
| Finnhub, Benzinga | kein Connector: REST, siehe `/trader-news` | `FINNHUB_API_KEY`, `BENZINGA_API_KEY` |
| House Clerk, Senate eFD | kein Connector: frei, siehe `/trader-pelosi` | keine |
| Quiver Quantitative | kein Connector: REST | `QUIVER_API_KEY` |
| Interactive Brokers | kein Connector: Client Portal API, siehe `/trader-broker` | eigene Gateway-Anmeldung |

Die MCP-Server sind Drittsoftware. Prüfe Version und Quelle, bevor du
ihnen Keys gibst, und pinne bei Bedarf eine Version in `.mcp.json`.

### 4. Order-Tools absichern

In `.claude/settings.json` deines Projekts das Senden immer nachfragen lassen
und gefährliche Tools sperren (Tool-Namen mit `/mcp` prüfen):

```json
{
  "permissions": {
    "ask": ["mcp__plugin_trader_alpaca__place_stock_order",
            "mcp__plugin_trader_alpaca__cancel_order_by_id",
            "mcp__plugin_trader_alpaca__close_position"],
    "deny": ["mcp__plugin_trader_alpaca__close_all_positions",
             "mcp__plugin_trader_alpaca__place_option_order",
             "mcp__plugin_trader_alpaca__place_crypto_order"]
  }
}
```

### 5. Konfiguration

```bash
python3 "<plugin>/scripts/trader.py" init      # legt trader/trader-config.json an (mode: paper)
python3 "<plugin>/scripts/trader.py" config    # prüft und zeigt Modus und Limits
```

| Feld | Bedeutung | Vorlage |
| --- | --- | --- |
| `mode` | `"paper"` oder `"live"` | `"paper"` |
| `account_currency`, `account_budget` | Kontowährung und Budget | EUR, 10.000 |
| `max_loss_per_trade` | `{"value": 1, "unit": "%"}` oder `{"value": 100, "unit": "EUR"}` | 1 % |
| `max_daily_loss` | wie oben | 300 EUR |
| `max_open_positions` | Anzahl | 3 |
| `max_position_size` | Positionswert, % oder EUR | 25 % |
| `min_reward_risk` | Mindest-Chance-Risiko | 2.0 |
| `allow_short` | Short-Trades | false |
| `watchlist` | Ticker | 7 US-Werte |
| `timezone`, `trading_hours` | eigene Handelszeit in Europe/Berlin | 15:30 bis 21:45 |
| `exchanges`, `default_exchange` | Börsen mit Zeitzone, Öffnung, Schluss, Währung | NASDAQ, NYSE, XETRA |
| `broker` | Name, API, MCP-Server, Key-Variablen | Alpaca Paper API |
| `pipeline` | Gültigkeit der Freigaben, Frist für das "Ja" | 30 / 10 Min. |
| `timemachine` | Simulationen, Horizont, Mindest-Stichprobe, Block bei negativem Erwartungswert | 10.000, 78 Bars, 20, true |

## Beispiel-Ablauf: vom Scan bis zur Paper-Order

Donnerstag, 5. November 2026, 16:15 Uhr Berlin (10:15 Uhr New York). Konto
10.000 EUR, Paper. Die Zahlen sind illustrativ.

**1. Scan** - `/trader-scanner`
Claude lädt 5-Min-Bars der Watchlist über Alpaca, speichert sie und ruft
`trader.py scan --bars trader/data/scan-20261105-1615.json --journal` auf:

```
| Ticker | Setup       | Kurs   | Volumen vs. Ø | Score | Chart |
| TSLA   | ORB-15 long | 364.18 | 1.84x         | 62    | https://www.tradingview.com/chart/?symbol=TSLA |
| NVDA   | Bull-Flag   | 201.40 | 1.31x         | 41    | ... |
Daten: Alpaca IEX, letzter Bar 16:10 CET. Treffer sind Kandidaten, keine Empfehlungen.
```

**2. News** - `/trader-news TSLA`: zwei Meldungen am Morgen, keine
kursbewegende in den letzten 60 Minuten, keine Earnings in den nächsten 2 Tagen.

**3. Entry** - `/trader-entry TSLA --level 362.18`
`AUSGELOEST um 16:15:00 CET: Close 364.18 > 362.18 und Volumen 41200 > 1.5 x Ø 22400.`
Keine Order, weiter mit dem Exit-Plan.

**4. Exit-Plan (Schritt 1)** - `/trader-exit`

```
trader.py exit --ticker TSLA --setup ORB-15 --entry 364.18 --stop 358.32 --fx 0.87 --fx-source "EZB 05.11.2026" --flat-by 21:55
Trade-ID: 20261105-161600-a1b2c3
TSLA LONG ORB-15 | Entry 364.18 | Stop 358.32 (manuell) | Ziel 375.90 | Risiko/Stueck 5.86 USD | CRV 2.00:1
EXIT-PLAN: PASS
```

**5. Time Machine (Schritt 2)** - `/trader-timemachine 20261105-161600-a1b2c3`
10.000 Sessions aus 60 Tagen 5-Min-Volatilität plus 34 historische ORB-15-Tage:
Trefferquote, Erwartungswert in R, schlimmster Fall, Histogramm, Stichprobe,
"Vergangenheit ist keine Garantie". Ergebnis `TIME MACHINE: PASS`
(bei Erwartungswert <= 0 wäre hier Schluss).

**6. Risk (Schritt 3)** - `/trader-risk ...`

```
max_loss_per_trade 100,00 EUR -> max. 19 Stueck
max_position_size 2.500,00 EUR -> max. 7 Stueck
Tagesverlust frei 300,00 EUR von 300,00 EUR -> max. 58 Stueck
RISK: PASS, erlaubt bis 7 Stueck
```

**7. Budget (Schritt 4)** - `/trader-budget ...`

```
Stueckzahl = 100,00 EUR / 5,10 EUR = 19.61 -> abgerundet 19
  -> begrenzt durch /trader-risk auf 7
Verlust am Stop: 7 x 5,10 EUR = 35,69 EUR | Gewinn am Ziel: 71,38 EUR
BUDGET: PASS, 7 Stueck
```

**8. Broker (Schritt 5)** - `/trader-broker ...`
Claude prüft per `get_account_info`, dass das Alpaca-Konto ein Paper-Konto ist,
und zeigt:

```
ORDER-ZUSAMMENFASSUNG  (Modus: PAPER, Broker: Alpaca)
  TSLA, KAUF, 7 Stueck, Entry ca. 364.18, Stop 358.32, Target 375.90
  Bracket-Order: Stop und Target werden direkt mit angelegt.
Senden? Antworte mit "Ja" für genau diese Order.
```

Du antwortest **Ja**. Claude ruft `trader.py confirm <id> --reply "Ja"` auf,
danach `place_stock_order` mit genau dem angezeigten JSON; Claude Code fragt
wegen der `ask`-Regel noch einmal. Die Antwort von Alpaca wird mit
`trader.py placed` ins Journal geschrieben.

**9. Alarme** - `/trader-alerts from-trade <id>` legt Alarme für Stop und Ziel
an, dazu einen Zeit-Alarm für 21:55 zum Glattstellen. `/loop 1m /trader-alerts check`
prüft minütlich.

**10. Schluss** - Ziel gefüllt: `trader.py close <id> --exit-price 375.90 --reason target`.
`trader.py journal --show` zeigt den ganzen Weg vom Scan bis zum Ergebnis.

## Tests

```bash
python3 -m unittest discover -s tests -p "test_trader_plugin.py" -v
```

(aus dem Repo-Wurzelverzeichnis; die Tests laufen offline mit synthetischen Daten.)

## Kleingedrucktes

- **Keine Anlageberatung.** Software, die Daten liest und rechnet. Sie kennt deine Situation nicht und sieht nicht in die Zukunft.
- **Simulation ist keine Prognose.** Monte Carlo und Backtest beruhen auf Vergangenheit und kennen keine Nachrichten, Handelsstopps oder Regimewechsel.
- **Ein Stop ist kein Garantiepreis.** Bei Gaps wird schlechter ausgeführt.
- **Datenqualität.** Freie Feeds (z. B. Alpaca IEX) zeigen nur einen Teil des Volumens; Verzögerungen sind möglich. Wichtiges beim Broker gegenprüfen.
- **Feiertage** kennt das Werkzeug nicht; `/trader-risk` prüft deshalb zusätzlich die Uhr des Brokers.
- **Steuern und Regeln** (z. B. Pattern-Day-Trader-Regeln deines Brokers, Abgeltungsteuer) sind nicht abgebildet.
