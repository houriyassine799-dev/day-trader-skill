---
name: trader-timemachine
description: >-
  Time Machine, Pflichtschritt 2 der Order-Pipeline. 1) Monte-Carlo-Simulation
  von 10.000 Sessions auf Basis der historischen Volatilität des Tickers,
  2) Backtest über jedes historische Auftreten desselben Setups beim Ticker.
  Ausgabe: Trefferquote, durchschnittlicher Gewinn und Verlust,
  Erwartungswert, schlimmster Fall und Verteilung als Chart, immer mit
  Stichprobengröße und dem Hinweis, dass Vergangenheit keine Garantie ist.
  Verwenden bei "simuliere den Trade", "Backtest", "Monte Carlo", "wie oft
  hat das funktioniert", "Time Machine", "/trader-timemachine", und immer nach
  /trader-exit.
argument-hint: "<trade-id> [--sims 10000] [--method gbm|bootstrap]"
---

# /trader-timemachine: Time Machine

Bevor Geld riskiert wird, spielt die Time Machine den geplanten Trade
(Entry, Stop, Ziel aus `/trader-exit`) tausendfach durch und schaut, wie oft
genau dieses Setup beim Ticker früher funktioniert hat.

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

| Input | Herkunft |
| --- | --- |
| Trade-ID | `/trader-exit` (Schritt 1 muss PASS und jünger als `pipeline.max_age_minutes` sein) |
| Historische Bars (`--returns-bars`) | mind. 20, besser 60 Handelstage 5-Min-Bars des Tickers (Alpaca/Polygon) |
| Historische Setup-Auftreten (`--backtest`) | JSON-Liste, siehe unten |
| Parameter | `timemachine.simulations` (10.000), `horizon_bars` (78 = eine US-Sitzung in 5-Min), `--seed` für Reproduzierbarkeit |

### Backtest-Datei

Für jeden historischen Tag, an dem dasselbe Setup beim Ticker auftrat, eine Zeile.
Zwei Formate sind erlaubt:

```json
[{"date": "2026-09-14", "entry": 351.2, "stop": 347.9, "target": 357.8, "side": "long",
  "bars": [{"t": "...", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0}]},
 {"date": "2026-09-21", "r": -1.0}]
```

Mit `bars` (die Bars NACH dem Einstieg bis Sitzungsende) rechnet das Werkzeug
selbst; trifft ein Bar Stop und Ziel, zählt konservativ der Stop. So
entstehen die Auftreten: für jeden historischen Tag `trader.py scan` auf den
Bars bis zum jeweiligen Zeitpunkt anwenden und dieselbe Stop/Ziel-Regel wie
im aktuellen Exit-Plan verwenden (gleiche Methode, gleiches CRV).

## Outputs

1. **Monte Carlo**: Anteil Ziel zuerst / Stop zuerst / offen bis Sitzungsende,
   Trefferquote, Ø Gewinn und Ø Verlust (in R und in Kontowährung je Stück),
   Erwartungswert, schlimmster Fall, 5 %-Quantil, Histogramm.
2. **Backtest**: n, Zeitraum, Trefferquote, Ø Gewinn/Verlust, Erwartungswert, schlimmster Fall, Histogramm.
3. Datengrundlage (Anzahl Bars, von/bis, Volatilität je Bar, Methode, Seed).
4. Pflichtsatz: "Vergangenheit ist keine Garantie" plus Stichprobengröße.
5. `TIME MACHINE: PASS` oder `BLOCK - <Grund>`; Rohdaten in `trader/reports/<id>-timemachine.json`.

Das ASCII-Histogramm genügt. Wenn der Nutzer eine Grafik möchte, aus
`mc_outcomes_sample` und `backtest_rows` des Reports ein Histogramm rendern.

## Ablauf

1. Historische Bars holen und speichern (`trader/data/hist-<TICKER>-5m.json`).
2. Historische Auftreten des Setups bestimmen und als `trader/data/bt-<TICKER>-<setup>.json` speichern.
3. Ausführen:
   `trader.py timemachine <id> --returns-bars trader/data/hist-TSLA-5m.json --backtest trader/data/bt-TSLA-orb15.json --seed 42`
4. Ergebnis zusammenfassen: in Worten, was Trefferquote und Erwartungswert bedeuten und wie unsicher sie sind.
5. Bei PASS: `/trader-risk <id>`. Bei BLOCK: Pipeline endet hier.

## Methode

- `gbm` (Standard): Log-Renditen je Bar normalverteilt mit der historischen
  Volatilität (Standardabweichung der 5-Min-Log-Renditen), Drift 0.
- `bootstrap`: zieht echte historische Bar-Renditen zufällig. Bildet dicke
  Ränder besser ab, kennt aber keine Tageszeit-Muster.
- Jeder Pfad endet am Stop, am Ziel oder nach `horizon_bars`. Springt ein Pfad
  über den Stop, wird am simulierten Kurs geschlossen (Verlust kann > 1R sein).
- Ergebnisse in R (Vielfache des Risikos Entry minus Stop).

## Regeln (BLOCK-Gründe)

- Schritt `/trader-exit` fehlt, ist BLOCK, älter als `max_age_minutes` oder der Plan wurde geändert.
- Monte-Carlo-Erwartungswert <= 0 (bei `block_negative_expectancy: true`).
- Backtest-Erwartungswert <= 0, sofern n >= `min_backtest_samples` (20).
- n < 20 ist kein BLOCK, aber eine deutliche WARNUNG: "statistisch kaum aussagekräftig".
- Keine Zahl ohne Stichprobengröße. Nie "Gewinnwahrscheinlichkeit" als Versprechen formulieren.

## Journal

Automatisch: Aktion `timemachine`, Ergebnis PASS/BLOCK, Begründung mit
Erwartungswert, Trefferquote und n für Monte Carlo und Backtest.

## Hinweis

Analyse, keine Anlageberatung. Vergangenheit ist keine Garantie: Die
Simulation kennt keine Nachrichten, Earnings, Handelsstopps oder
Regimewechsel, und ein kleiner Backtest kann reiner Zufall sein.
