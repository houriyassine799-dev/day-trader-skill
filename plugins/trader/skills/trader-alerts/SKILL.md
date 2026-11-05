---
name: trader-alerts
description: >-
  Price Alerts. Setzt Alarme für Kursmarken, Stops, Ziele und die
  Börsenöffnung (je Börse aus trader-config.json) und benachrichtigt über den
  Chat bzw. einen geplanten Task (/loop, Cron). Löst nie eine Order aus.
  Verwenden bei "Alarm bei 360", "sag Bescheid wenn", "erinnere mich an die
  Börsenöffnung", "Stop-Alarm", "price alert", "/trader-alerts", und nach
  jeder platzierten Order.
argument-hint: "add TICKER --level 360 | open [--exchange NYSE] | from-trade <id> | list | check"
---

# /trader-alerts: Price Alerts

Hält Kursmarken im Blick und meldet sich, wenn sie erreicht werden. Alarme
liegen in `trader/alerts.json`; ein Alarm klingelt genau einmal.

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

| Alarmtyp | Befehl |
| --- | --- |
| Kursmarke | `trader.py alert add --kind price --ticker TSLA --level 360 --direction below --note "Unterstützung"` |
| Stop / Ziel eines Trades | `trader.py alert from-trade --trade <id>` (legt beide an) |
| Einzelner Stop/Ziel | `trader.py alert add --kind stop --ticker TSLA --level 358.32` |
| Börsenöffnung | `trader.py alert add --kind open --exchange NASDAQ --before 5` (5 Min. vorher; Zeit in Europe/Berlin) |
| Liste / Entfernen | `trader.py alert list [--all]`, `trader.py alert remove --id <id>` |

## Outputs

- Beim Anlegen: Alarm-ID, Ticker, Typ, Marke bzw. Uhrzeit in Europe/Berlin.
- Beim Klingeln: `ALARM: TSLA stop: Kurs 358.10 <= 358.32 (2026-11-05 17:42:00 CET) Trade <id>`,
  dazu die Datenquelle und der Zeitstempel des Kurses.

## Benachrichtigung

Claude kann nicht von selbst aufwachen. Deshalb:

1. **Im Chat (Claude Code):** `/loop 1m /trader-alerts check` prüft jede Minute. Bei jedem Lauf:
   aktuelle Kurse aller Alarm-Ticker holen (Alpaca `get_stock_latest_quote`/`get_stock_snapshot`
   oder Polygon Snapshot), dann
   `trader.py alert check --prices '{"TSLA": 361.20, "NVDA": 201.4}'`.
2. **Als geplanter Task:** für die Börsenöffnung einen einmaligen Task zur Alarmzeit anlegen
   (Cron bzw. "geplanter Task" der Claude-App); für Kursalarme ein wiederkehrender Task alle
   1 bis 5 Minuten innerhalb der `trading_hours`.
3. **Stop und Ziel beim Broker:** Bei Bracket-Orders liegen Stop und Ziel bereits als Orders beim
   Broker. Der Alarm informiert nur; er ersetzt die Broker-Order nicht.
4. **Flat-by:** Für Day-Trades zusätzlich einen Zeit-Alarm zur `flat_by`-Zeit des Exit-Plans
   (als geplanter Task). Er erinnert an das Glattstellen über `/trader-broker`.

## Regeln

- Ein Alarm löst nie eine Order aus, auch nicht für Stop oder Glattstellung. Er schlägt höchstens `/trader-broker` vor, und dort gilt wieder das "Ja".
- Geplante Tasks melden nur. Sie dürfen keine Broker-Tools aufrufen, die etwas verändern.
- Kurse nur aus echten Abrufen; ein fehlender Kurs heißt "nicht geprüft", nicht "nicht erreicht".
- Alarme außerhalb der Handelszeit (außer `open`) als "Markt geschlossen" melden statt still zu bleiben.
- Bei Ausfall einer Datenquelle den Nutzer informieren, damit er sich nicht auf einen stummen Alarm verlässt.

## Journal

Automatisch: `alert-add` beim Anlegen, `alert` beim Klingeln (mit Kurs und Marke).

## Hinweis

Analyse, keine Anlageberatung. Alarme hängen von Datenqualität und
Abfrageintervall ab; zwischen zwei Prüfungen kann der Kurs eine Marke
durchlaufen haben.
