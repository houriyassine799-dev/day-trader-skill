---
name: trader-analyst
description: >-
  Earnings Analyst. Liest Quartals- und Jahresberichte über SEC EDGAR (10-Q,
  10-K, 8-K mit Item 2.02) und fasst zusammen: Umsatz und Gewinn bzw. EPS vs.
  Erwartung und Vorjahr, Ausblick und Guidance, und was sich gegenüber dem
  letzten Bericht geändert hat, immer mit Quellenlink. Verwenden bei
  "Quartalszahlen", "Earnings von AAPL", "was steht im 10-Q", "Guidance",
  "wie waren die Zahlen", "earnings analysis", "/trader-analyst".
argument-hint: "TICKER [10-Q | 10-K | 8-K]"
---

# /trader-analyst: Earnings Analyst

Liest die Originaldokumente bei der SEC und fasst sie nüchtern zusammen.
Für Day-Trader vor allem wichtig: Wann kommen die nächsten Zahlen, und was
hat sich geändert.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

- Ticker (Pflicht), optional Formulartyp (Standard: neuester 10-Q/10-K plus zugehöriges 8-K Item 2.02).
- Erwartungen (Konsens): optional, nur aus einer genannten Quelle (z. B. Finnhub
  `GET /api/v1/stock/earnings?symbol=AAPL` mit `X-Finnhub-Token: $FINNHUB_API_KEY`,
  oder Polygon/Benzinga Earnings). Ohne Quelle: "Erwartung nicht verfügbar", nie schätzen.

## Datenquellen

- **MCP-Server `sec-edgar`** aus `.mcp.json` (Paket `sec-edgar-mcp`): Filings, Volltext,
  XBRL-Finanzdaten. Braucht `SEC_EDGAR_USER_AGENT` = "Name email@beispiel.de" (Pflicht der SEC, kein Geheimnis, aber trotzdem aus der Umgebung).
- **Ohne Connector, direkt (frei, kein Key):** jeder Aufruf mit Header `User-Agent: $SEC_EDGAR_USER_AGENT`, höchstens 10 Anfragen pro Sekunde.
  - Ticker → CIK: `https://www.sec.gov/files/company_tickers.json`
  - Filings: `https://data.sec.gov/submissions/CIK##########.json` (10-stellig, mit Nullen)
  - Kennzahlen (XBRL): `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
    (z. B. `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `NetIncomeLoss`, `EarningsPerShareDiluted`)
  - Dokument: `https://www.sec.gov/Archives/edgar/data/<CIK>/<Accession ohne Bindestriche>/<Hauptdokument>`

## Outputs

```
AAPL, 10-Q für Q4 FY2026 (eingereicht 2026-10-30, Periode bis 2026-09-26)
Quelle: <Link zum Filing>  |  Pressemitteilung: <Link zum 8-K Ex. 99.1>

| Kennzahl | Ist | Erwartung (Quelle, Datum) | Vorjahresquartal | Veränderung |
| Umsatz | ... | ... | ... | +x,x % |
| Nettogewinn | ... | ... | ... | |
| EPS verwässert | ... | ... | ... | |

Ausblick/Guidance: wörtliche Kernaussagen mit Fundstelle, oder "keine Guidance im Filing".
Geändert gegenüber dem letzten Bericht: Segmente, Margen, Risikofaktoren (Item 1A), Rechtsstreitigkeiten,
Aktienrückkäufe, Bilanzposten mit großer Änderung.
Nächster Termin: voraussichtliches Datum (Quelle) oder "unbekannt".
Unsicherheit: z. B. bereinigte vs. GAAP-Zahlen, Restatements, fehlende Konsensquelle.
```

## Ablauf

1. CIK ermitteln, letzte Filings listen, das passende 10-Q/10-K und 8-K (Item 2.02) wählen.
2. Kennzahlen aus XBRL holen: aktuelles Quartal, Vorjahresquartal (gleiche `fp`/`fy`-Logik, Dauer ~3 Monate), Vorquartal.
3. Erwartungen nur aus einer benannten Quelle; Abrufdatum angeben.
4. Guidance aus der Pressemitteilung (8-K Exhibit 99.1) bzw. MD&A (Item 2 im 10-Q, Item 7 im 10-K) zitieren.
5. Änderungen ggü. dem letzten Bericht: Kennzahlen-Deltas plus Textvergleich der Risikofaktoren.
6. Ergebnis ausgeben, Journal schreiben:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py" journal --action earnings --ticker AAPL --result "Umsatz +8 % ggü. VJ" --reason "10-Q 2026-10-30, <Link>"`

## Regeln

- Jede Zahl mit Fundstelle (Filing, Periode, XBRL-Konzept). Keine Zahl ohne Quelle.
- GAAP und bereinigte (non-GAAP) Zahlen nie vermischen; kennzeichnen, was was ist.
- Datum der Einreichung UND Berichtsperiode nennen.
- Bei Day-Trades: anstehende Earnings innerhalb der Haltedauer deutlich als Risiko nennen.
- Löst keine Order aus und gibt keine Kauf- oder Verkaufsempfehlung.

## Hinweis

Analyse, keine Anlageberatung. Die Kursreaktion auf Zahlen hängt an
Erwartungen, die oft nicht öffentlich und nicht einheitlich sind.
