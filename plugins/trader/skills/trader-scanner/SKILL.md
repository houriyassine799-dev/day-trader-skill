---
name: trader-scanner
description: >-
  Chart Scanner (Pipeline-Schritt "Ideen finden"). Scannt die Watchlist aus
  trader-config.json oder ein Universum (z. B. S&P 500) nach Day-Trading-Setups:
  Opening-Range-Breakouts der ersten 5/15/30 Minuten, Bull- und Bear-Flags und
  Oversold-Bounces (RSI < 30 plus Umkehrkerze). Gibt eine Tabelle mit Ticker,
  Setup-Typ, Kurs, Volumen vs. Durchschnitt, Stärke-Score und Chart-Link aus.
  Platziert nie eine Order. Verwenden bei "scanne den Markt", "finde Setups",
  "was bricht aus", "Opening Range Breakout", "Bull Flag", "überverkaufte
  Aktien", "scan the watchlist", "find setups", "/trader-scanner".
argument-hint: "[watchlist | sp500 | TICKER,TICKER] [--or 5|15|30]"
---

# /trader-scanner: Chart Scanner

Der Scanner ist der Anfang jeder Idee, nicht der Anfang eines Trades. Er
findet Charts, die eine Regel erfüllen. Ein Treffer ist keine Kaufempfehlung
und keine Prognose.

`trader.py` steht im Folgenden für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.
Arbeitsordner ist `trader/` im aktuellen Projekt.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

| Input | Herkunft | Pflicht |
| --- | --- | --- |
| Universum | Argument: `watchlist` (Standard, aus der Config), `sp500` oder eine Ticker-Liste | nein |
| Intraday-Bars | 5-Min-Bars der laufenden Sitzung plus mind. 20 Bars davor, je Ticker | ja |
| Opening-Range-Länge | `--or 5`, `15` oder `30` (Standard: alle drei) | nein |
| Config | `trader/trader-config.json`: `watchlist`, `exchanges`, `timezone`, `entry.volume_lookback` | ja |

## Outputs

Eine Markdown-Tabelle, absteigend nach Score:

| Ticker | Setup | Kurs | Volumen vs. Ø | Score | Chart |
| --- | --- | --- | --- | --- | --- |

Darunter immer: Datenquelle, Zeitstempel des letzten Bars (Europe/Berlin),
Anzahl gescannter Ticker und der Hinweis "Treffer sind Kandidaten, keine
Empfehlungen".

## Datenquellen

- **Alpaca** (MCP-Server `alpaca` aus `.mcp.json`, Toolset `stock-data`):
  historische Bars, z. B. Tool `get_stock_bars` (Timeframe 5Min). Free-Plan:
  IEX-Feed, also nur ein Teil des Gesamtvolumens; das in der Ausgabe nennen.
- **Polygon.io** (MCP-Server `polygon`): Aggregates, z. B. `get_aggs`
  (multiplier 5, timespan minute). Braucht `POLYGON_API_KEY`.
- **Universum S&P 500**: es gibt keine offizielle freie API. Liste aus einer
  Quelle laden, die der Nutzer akzeptiert (z. B. Polygon `list_tickers` plus
  Index-Konstituenten eines Datenanbieters) und Quelle plus Datum nennen. Bei
  500 Tickern zuerst grob filtern (Gap und Volumen vor der Eröffnung per
  Snapshot), dann nur die Top 50 bis 100 mit Intraday-Bars prüfen.

Die genauen Tool-Namen mit `/mcp` prüfen; sie können sich mit der Server-Version ändern.

## Ablauf

1. `trader.py config` ausführen. Fehlt die Config: `trader.py init` und den Nutzer bitten, Budget und Limits zu setzen.
2. Universum bestimmen (Standard: `watchlist`).
3. Bars je Ticker über MCP holen und als JSON speichern:
   `trader/data/scan-<YYYYMMDD-HHMM>.json` im Format
   `{"TSLA": [{"t": "2026-11-05T09:30:00-05:00", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0}, ...], ...}`
   (`t` darf ISO-Zeit oder Epoch-Millisekunden sein, so wie Alpaca bzw. Polygon sie liefern).
4. Scannen: `trader.py scan --bars trader/data/scan-....json --journal`
   (`--json` für Rohdaten, `--min-score 40` zum Filtern, `--exchange XETRA` für deutsche Werte).
5. Tabelle zeigen. Für die besten 3 Treffer kurz erklären, warum sie anschlagen (Levels aus `--json`, Feld `info`).
6. Nächsten Schritt anbieten: `/trader-news` für die Ticker, dann `/trader-entry` für ein ausgewähltes Setup.

## Setup-Regeln (wie das Werkzeug rechnet)

- **Opening-Range-Breakout (ORB-5/15/30)**: Hoch und Tief der ersten N Minuten
  ab Börsenöffnung der jeweiligen Börse. Long, wenn der letzte Bar über dem
  Range-High schließt; Short, wenn unter dem Range-Low.
- **Bull-Flag**: Mast von mind. 1,5 % in 2 bis 8 Bars, danach 3 bis 10 Bars
  Konsolidierung, die weniger als 50 % des Masts zurücknimmt, bei geringerem
  Volumen; Signal, wenn der letzte Bar über der Flagge schließt. **Bear-Flag**
  gespiegelt.
- **Oversold-Bounce**: RSI(14) < 30 auf dem vorletzten oder letzten Bar und
  eine Umkehrkerze (Hammer oder bullisches Engulfing) auf dem letzten Bar.
- **Volumen vs. Ø**: Volumen des letzten Bars geteilt durch den Durchschnitt der 20 Bars davor.
- **Stärke-Score 0 bis 100**: bis 50 Punkte für Abstand zum Level bzw.
  Musterstärke, bis 50 Punkte für Volumen über dem Durchschnitt. Der Score
  sortiert, er sagt nichts über die Erfolgswahrscheinlichkeit.
- **Chart-Link**: `https://www.tradingview.com/chart/?symbol=<TICKER>`.

## Regeln

- Nur abgeschlossene Bars werten. Daten älter als 15 Minuten während der Sitzung deutlich als veraltet markieren.
- Keine Formulierungen wie "kaufen", "sicher", "wird steigen".
- Der Scanner erzeugt keinen Trade und keine Order. Ein Trade beginnt immer mit `/trader-exit`.
- Short-Setups nur zeigen; handelbar sind sie nur mit `allow_short: true` in der Config.

## Journal

`--journal` schreibt eine Zeile `scan` (Zeit, Ticker-Liste, Anzahl Treffer,
Datendatei). Ohne Werkzeug: `trader.py journal --action scan --ticker ... --result "N Treffer" --reason "Quelle, Datum"`.

## Hinweis

Analyse, keine Anlageberatung. Muster in Charts haben eine hohe Fehlerquote;
die meisten Day-Trader verlieren Geld.
