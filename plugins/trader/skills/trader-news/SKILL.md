---
name: trader-news
description: >-
  News Monitor. Verfolgt Nachrichten zu allen Watchlist-Tickern aus
  trader-config.json (Polygon News, Finnhub oder Benzinga) und markiert
  Meldungen, die den Kurs bewegen: Kursänderung plus Volumenspitze innerhalb
  von X Minuten nach der Meldung. Ausgabe: Ticker, Schlagzeile, Quelle,
  Uhrzeit und Kursreaktion. Verwenden bei "News zu meiner Watchlist", "was
  bewegt TSLA", "gibt es Nachrichten", "breaking news", "news monitor",
  "/trader-news".
argument-hint: "[TICKER,TICKER] [--hours 12] [--window 15]"
---

# /trader-news: News Monitor

Zeigt, was über die Watchlist gemeldet wurde, und misst, ob der Markt darauf
reagiert hat. Eine Meldung ist "kursbewegend", wenn nach ihr innerhalb des
Fensters beides passiert: Kursänderung über der Schwelle UND Volumenspitze.

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

| Input | Standard |
| --- | --- |
| Ticker | `watchlist` aus der Config |
| Zeitraum | letzte 12 Stunden (`--hours`) |
| Reaktionsfenster X | `news.reaction_window_minutes` (15) |
| Schwellen | `news.min_move_pct` (2,0 %) und `news.min_volume_ratio` (2,0 x Ø) |
| 1-Min-Bars rund um jede Meldung | aus Alpaca oder Polygon |

## Outputs

| Ticker | Schlagzeile | Quelle | Uhrzeit (Berlin) | Kursreaktion | Markierung |
| --- | --- | --- | --- | --- | --- |

Kursreaktion als `+3,1 % / 4,2x Vol in 15 Min.`; Markierung `KURSBEWEGEND`
oder leer. Meldungen vor Börsenöffnung: Reaktion ab Eröffnung messen und das
so kennzeichnen. Am Ende: Quellen, Abrufzeit, Zahl der Meldungen.

## Datenquellen

- **Polygon News** (MCP-Server `polygon`, z. B. Tool `list_ticker_news`, Parameter `ticker`, `published_utc.gte`). Key: `POLYGON_API_KEY`.
- **Finnhub** (kein Connector in `.mcp.json`): REST
  `GET https://finnhub.io/api/v1/company-news?symbol=TSLA&from=YYYY-MM-DD&to=YYYY-MM-DD`
  mit Header `X-Finnhub-Token: $FINNHUB_API_KEY`. Key kommt aus der
  Umgebungsvariable; im Befehl nur als `$FINNHUB_API_KEY` referenzieren, nie ausschreiben.
  Im Cloud-Umfeld den Key als Netzwerk-Secret für `finnhub.io` hinterlegen lassen.
- **Benzinga** (kein Connector): News-API `https://api.benzinga.com/api/v2/news?tickers=TSLA`
  mit `token=$BENZINGA_API_KEY`, kostenpflichtig.
- **Alpaca News** ist im Alpaca-Datenfeed enthalten (Benzinga-Quelle), wenn der Server ein News-Tool anbietet (`/mcp` prüfen).
- **Kurse für die Reaktion**: 1-Min-Bars über `alpaca` oder `polygon`.

Ist kein Key gesetzt (`trader.py env` zeigt das, ohne Werte), dem Nutzer sagen,
welche Variable fehlt und wo sie gesetzt wird. Nie nach dem Key im Chat fragen.

## Ablauf

1. `trader.py config` und `trader.py env` ausführen.
2. Meldungen je Ticker abrufen, Duplikate (gleiche Schlagzeile, mehrere Quellen) zusammenfassen, früheste Uhrzeit behalten.
3. Für jede Meldung 1-Min-Bars von 30 Minuten davor bis X Minuten danach holen,
   als `trader/data/news-<TICKER>-<HHMM>.json` speichern und messen:
   `trader.py news-react --bars <datei> --time "<published_utc>" --ticker TSLA --headline "<Schlagzeile>" --journal`
4. Tabelle ausgeben, kursbewegende Meldungen zuerst.
5. Bei anstehenden Earnings (nächste 2 Handelstage) für einen Ticker deutlich warnen und auf `/trader-analyst` verweisen.

## Regeln

- Uhrzeiten immer in Europe/Berlin, mit Quelle der Zeit (Veröffentlichungszeit der Quelle, nicht Abrufzeit).
- Reaktion ist Korrelation, keine Kausalität: dazuschreiben, wenn mehrere Meldungen im selben Fenster liegen.
- Keine Schlagzeile erfinden oder umformulieren; Original zitieren, Link angeben.
- Gerüchte und Pressemitteilungen des Unternehmens als solche kennzeichnen.
- Löst keine Order aus.

## Journal

Jede gemessene Meldung: Aktion `news`, Ticker, Ergebnis `kursbewegend`/`neutral`,
Begründung mit Schlagzeile und Messwerten (über `--journal`).

## Hinweis

Analyse, keine Anlageberatung. News-Reaktionen sind schnell, volatil und oft
schon eingepreist, bevor eine API sie liefert.
