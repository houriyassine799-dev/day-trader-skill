---
name: trader-risk
description: >-
  Risk Manager, Pflichtschritt 3 der Order-Pipeline, kann nicht übersprungen
  werden. Prüft jeden geplanten Trade gegen alle Limits aus
  trader-config.json: Verlust pro Trade, Tagesverlust, offene Positionen,
  Positionsgröße, Chance-Risiko, Handelszeiten und Börsenöffnung, Short-Verbot.
  Bei Überschreitung wird der Trade blockiert und der Grund genannt. Verwenden
  bei "prüfe das Risiko", "darf ich den Trade machen", "Risk Check", "wie viel
  habe ich heute riskiert", "/trader-risk", und immer nach /trader-timemachine.
argument-hint: "<trade-id>"
---

# /trader-risk: Risk Manager

Der Risk Manager ist das Tor zwischen Idee und Order. Sein BLOCK ist endgültig
für diesen Plan. Er läuft zweimal: als Schritt 3 der Pipeline und noch einmal
automatisch als finaler Re-Check in `/trader-broker` mit der endgültigen Stückzahl.

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

- Trade-ID (Schritte exit und timemachine PASS, aktuell, Plan unverändert).
- `trader/trader-config.json`: `max_loss_per_trade`, `max_daily_loss`,
  `max_open_positions`, `max_position_size`, `min_reward_risk`, `allow_short`,
  `trading_hours`, `timezone`, `exchanges`.
- Kontostand aus `trader/trades/` (offene Trades, heute geschlossene Ergebnisse).
  Vorher mit dem Broker abgleichen: `get_all_positions` bzw. `get_account_info`
  (nur lesend). Weicht der Broker ab (z. B. Position außerhalb des Plugins
  eröffnet), das melden und den Trade blockieren, bis es geklärt ist.

Limits können in Kontowährung (`{"value": 300, "unit": "EUR"}`) oder in
Prozent des `account_budget` (`{"value": 1, "unit": "%"}`) stehen.

## Prüfungen

| Prüfung | BLOCK, wenn |
| --- | --- |
| Pipeline | exit oder timemachine fehlt, ist BLOCK, zu alt, oder der Plan wurde geändert |
| Verlust pro Trade | schon 1 Stück mehr Risiko hat als `max_loss_per_trade` erlaubt |
| Tagesverlust | realisierte Verluste heute + offenes Risiko an den Stops + dieser Trade > `max_daily_loss` |
| Offene Positionen | bereits `max_open_positions` offen |
| Positionsgröße | schon 1 Stück > `max_position_size` |
| Chance-Risiko | < `min_reward_risk` |
| Zeit | außerhalb `trading_hours` (Europe/Berlin) oder die Börse ist geschlossen (eigene Zeitzone, Wochenende) |
| Short | Short-Trade bei `allow_short: false` |

Feiertage kennt das Werkzeug nicht: vor dem PASS die Broker-Uhr prüfen
(Alpaca `get_clock`, `is_open`). Ist der Markt zu, BLOCK.

## Outputs

```
RISK CHECK <id> (TSLA, Modus paper)
  Risiko je Stueck: 5,10 EUR
  max_loss_per_trade 100,00 EUR -> max. 19 Stueck
  max_position_size 2.500,00 EUR -> max. 7 Stueck
  Tagesverlust frei 300,00 EUR von 300,00 EUR -> max. 58 Stueck
  offene Positionen 0 von max. 3 | CRV 2.00:1
RISK: PASS, erlaubt bis 7 Stueck
```

oder `RISK: BLOCK` mit jedem Grund als eigene Zeile. Die erlaubte
Höchststückzahl (`max_qty`) ist verbindlich für `/trader-budget`.

## Ablauf

1. Broker-Stand lesen (Positionen, Konto, Uhr) und mit `trader.py status` abgleichen.
2. `trader.py risk <id>`
3. Ergebnis wörtlich zeigen. Bei BLOCK: Grund erklären, keine Umgehung anbieten.
4. Bei PASS: `/trader-budget <id>`.

## Regeln

- Kann nicht übersprungen werden. Kein anderer Skill darf eine Order ohne PASS dieses Schritts vorbereiten; `/trader-broker` prüft das technisch.
- Limits nie während eines Trades oder nach einem BLOCK lockern, um den Trade doch zu machen. Änderungen an der Config nur durch den Nutzer selbst, und dann läuft die Pipeline neu.
- Kein "nur dieses eine Mal". Kein Aufteilen eines Trades in mehrere kleine, um ein Limit zu umgehen.
- Nach Erreichen des Tageslimits: für heute keine neuen Einstiege. Ausstiege bleiben immer möglich.

## Journal

Automatisch: Aktion `risk`, PASS mit `max N Stueck` oder BLOCK mit allen Gründen.

## Hinweis

Analyse, keine Anlageberatung. Limits begrenzen Verluste, sie machen einen
Trade nicht profitabel.
