---
name: trader-exit
description: >-
  Exit Planner, Pflichtschritt 1 der Order-Pipeline. Legt VOR dem Einstieg
  Stop-Loss und Kursziel fest: Stop unter dem Range-Low oder ATR-basiert,
  Ziel mit mindestens 2:1 Chance-Risiko-Verhältnis (min_reward_risk aus der
  Config). Ohne Exit-Plan kein Trade. Erzeugt die Trade-ID, mit der alle
  folgenden Schritte arbeiten. Verwenden bei "wo setze ich den Stop",
  "Kursziel", "Exit-Plan", "Stop-Loss", "plan the exit", "/trader-exit", und
  immer direkt nach einem Signal von /trader-entry.
argument-hint: "TICKER --entry 364.18 [--stop X | --stop-mode range|atr] [--target Y]"
---

# /trader-exit: Exit Planner

Jeder Trade beginnt hier. Der Exit-Plan legt fest, wo der Trade falsch ist
(Stop) und wo er genug gebracht hat (Ziel), bevor Geld im Spiel ist. Ohne
PASS in diesem Schritt blockieren alle folgenden Schritte.

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

| Input | Pflicht | Hinweis |
| --- | --- | --- |
| `--ticker`, `--setup`, `--side long/short` | ja | aus Scanner/Entry |
| `--entry` | ja | geplanter Einstieg (z. B. Auslösekurs von `/trader-entry`) |
| `--stop` | oder `--stop-mode` | fester Stop |
| `--stop-mode range` + `--bars` + `--or-minutes 15` | | Stop am Range-Low (long) bzw. Range-High (short), optional `--buffer 0.05` |
| `--stop-mode atr` + `--bars` + `--atr-mult 1.5` | | Stop = Entry -/+ 1,5 x ATR(14) |
| `--target` | nein | ohne Angabe: Entry + min_reward_risk x Risiko |
| `--exchange` | nein | Standard `default_exchange` |
| `--fx`, `--fx-source` | bei Fremdwährung | Kontowährung je 1 Kurswährung, z. B. `0.87` EUR je USD, mit Quelle und Datum |
| `--flat-by` | empfohlen | spätester Ausstieg, z. B. `21:55 Berlin` |
| `--data-basis`, `--reason` | empfohlen | Datenquelle mit Zeitstempel, Begründung des Setups |

## Outputs

- Trade-ID (z. B. `20261105-161600-a1b2c3`), gespeichert in `trader/trades/<id>.json`.
- Zeile mit Entry, Stop (und Methode), Ziel, Risiko je Stück, Chance-Risiko-Verhältnis.
- `EXIT-PLAN: PASS` oder `EXIT-PLAN: BLOCK - <Grund>`.

## Ablauf

1. Bars für die Stop-Methode bereitstellen (`trader/data/...json`) oder einen festen Stop vom Nutzer nehmen.
2. Bei US-Werten und EUR-Konto: aktuellen EUR/USD-Kurs mit Quelle und Uhrzeit besorgen (z. B. EZB-Referenzkurs oder Polygon Forex) und als `--fx` übergeben.
3. Ausführen, z. B.
   `trader.py exit --ticker TSLA --setup ORB-15 --side long --entry 364.18 --bars trader/data/entry-TSLA.json --stop-mode range --or-minutes 15 --fx 0.87 --fx-source "EZB 05.11.2026" --flat-by "21:55" --reason "ORB-15 Close über Range-High, Vol 1,8x"`
4. Ergebnis zeigen und begründen: warum dieser Stop (strukturell oder volatilitätsbasiert), was das Ziel bedeutet.
5. Bei PASS die Trade-ID nennen und `/trader-timemachine <id>` als nächsten Schritt aufrufen.

## Regeln

- Ohne Exit-Plan kein Trade. Kein Schritt der Pipeline startet ohne Trade-ID aus diesem Skill.
- Long: Stop < Entry < Ziel. Short: Ziel < Entry < Stop. Sonst BLOCK.
- Chance-Risiko mindestens `min_reward_risk` (Standard 2,0). Darunter BLOCK; das Ziel nicht schönrechnen, um die Regel zu erfüllen. Liegt ein realistisches Ziel (Widerstand, Tageshoch) näher, ist der Trade eben nicht gut genug.
- Stop nie enger setzen, nur damit die Positionsgröße steigt.
- Jede Änderung von Entry, Stop oder Ziel erzeugt einen neuen Exit-Plan; die alte ID verliert ihre späteren Freigaben (der Fingerabdruck ändert sich).
- Day-Trade: `--flat-by` vor Börsenschluss setzen; `/trader-alerts` erinnert daran.

## Journal

Automatisch: Aktion `exit-plan` mit Ticker, Setup, Entry, Stop, Target, Ergebnis PASS/BLOCK und Begründung.

## Hinweis

Analyse, keine Anlageberatung. Ein Stop ist ein Plan, keine Garantie: bei
Gaps oder schnellen Märkten kann er deutlich schlechter ausgeführt werden.
