---
name: trader-entry
description: >-
  Entry Timer. Beobachtet ein ausgewähltes Setup Kerze für Kerze (1-Min oder
  5-Min) und meldet, sobald die definierten Einstiegsbedingungen erfüllt sind,
  z. B. Close über Range-High mit Volumen > 1,5 x Durchschnitt. Nennt
  Bedingung, Kurs und Zeitpunkt. Löst KEINE Order aus. Verwenden bei "sag mir
  wann ich rein soll", "beobachte TSLA", "Entry-Signal", "wann ist der
  Einstieg", "watch this setup", "/trader-entry".
argument-hint: "TICKER --level 364.18 [--interval 1|5] [--direction above|below]"
---

# /trader-entry: Entry Timer

Wartet auf einen sauberen, vorher definierten Auslöser und meldet ihn mit
Bedingung, Kurs und Uhrzeit. Er sendet nichts an einen Broker. Nach einem
Auslöser geht es mit `/trader-exit` weiter.

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

| Input | Beispiel | Herkunft |
| --- | --- | --- |
| Ticker und Setup | TSLA, ORB-15 long | `/trader-scanner` oder Nutzer |
| Level | Range-High 362.18 | `trader.py scan --json`, Feld `info.level` |
| Richtung | `above` (long) / `below` (short) | Setup |
| Intervall | 1 oder 5 Minuten | Nutzer, Standard 5 |
| Volumen-Multiplikator | 1,5 | `entry.volume_multiple` in der Config |

Bedingungen vor dem Start einmal ausformulieren und vom Nutzer bestätigen
lassen, z. B. "Ein 5-Min-Bar schließt über 362.18 UND sein Volumen ist größer
als 1,5 x Durchschnitt der letzten 20 Bars."

## Outputs

- `WARTEN (Bar bis 16:15 CET): Kurs ... nicht erfüllt; Volumen ... nicht erfüllt`
- `AUSGELOEST um 16:15:00 CET: Close 364.18 > 362.18 und Volumen 41200 > 1.5 x Ø 23000. Kurs 364.18.`
- Immer mit Datenquelle und Zeit des letzten Bars.

## Ablauf

1. Bars holen (Alpaca `get_stock_bars` bzw. Polygon `get_aggs`, 1Min oder 5Min,
   heute plus mind. 20 Bars davor) und als `trader/data/entry-<TICKER>.json` speichern.
2. Prüfen: `trader.py entry --bars trader/data/entry-TSLA.json --level 362.18 --interval 5 --ticker TSLA --setup ORB-15 --journal`
   Das Werkzeug wertet nur ABGESCHLOSSENE Bars (Startzeit + Intervall <= jetzt).
3. Wiederholen, bis ausgelöst oder ungültig:
   - in Claude Code mit `/loop 1m /trader-entry TSLA --level 362.18` (1-Min) bzw. `/loop 5m ...`,
   - oder als geplanter Task. Ein geplanter Task darf nur melden, nie eine Order anstoßen.
4. Beim Auslösen: Meldung ausgeben und `/trader-exit` mit dem Auslösekurs als Entry vorschlagen.
5. Abbrechen und melden, wenn das Setup ungültig wird (z. B. Close zurück unter das Range-Low,
   nach `trading_hours.end`, oder der Nutzer stoppt).

## Regeln

- **Löst KEINE Order aus.** Kein Aufruf von Broker-Tools in diesem Skill.
- Nur abgeschlossene Bars; nie auf einen laufenden Bar reagieren.
- Bedingungen nach dem Start nicht stillschweigend ändern. Änderung = neue Bestätigung durch den Nutzer.
- Ein Signal ist ein erfüllter Regelzustand, keine Vorhersage.
- Datenlücken (fehlender Bar, Handelsstopp) melden statt zu interpolieren.

## Journal

Jede Prüfung mit `--journal`: Aktion `entry-check`, Ergebnis `wartet`/`ausgeloest`,
Bedingungen als Begründung. Bei Schleifen nur Statuswechsel journalen
(erste Prüfung, Auslösung, Abbruch), damit das Journal lesbar bleibt.

## Hinweis

Analyse, keine Anlageberatung. Ausbrüche scheitern häufig (Fehlausbrüche);
deshalb folgt immer ein Exit-Plan, eine Simulation und eine Risikoprüfung.
