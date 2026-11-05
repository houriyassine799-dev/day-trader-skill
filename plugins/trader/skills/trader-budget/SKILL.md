---
name: trader-budget
description: >-
  Budget Manager, Pflichtschritt 4 der Order-Pipeline. Berechnet die
  Positionsgröße: Stückzahl = max_loss_per_trade / (Entry - Stop), abgerundet,
  begrenzt durch die Höchststückzahl aus /trader-risk und das verfügbare
  Budget, und zeigt die komplette Rechnung inklusive Währungsumrechnung.
  Verwenden bei "wie viele Aktien", "Positionsgröße", "wie viel kann ich
  kaufen", "position size", "/trader-budget", und immer nach /trader-risk.
argument-hint: "<trade-id> [--cash 8500]"
---

# /trader-budget: Budget Manager

Rechnet aus, wie viele Stück der Trade haben darf, und zeigt jeden
Rechenschritt, damit der Nutzer ihn nachprüfen kann.

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
| Trade-ID | Schritte exit, timemachine, risk PASS, aktuell, Plan unverändert |
| `max_loss_per_trade`, `account_budget` | Config |
| `max_qty` | Ergebnis von `/trader-risk` |
| `--cash` | verfügbares Kapital laut Broker (`get_account_info`, Feld Buying Power bzw. Cash, in Kontowährung umgerechnet). Ohne Angabe: `account_budget` minus gebundenes Kapital offener Trades minus heutige Verluste |
| FX | aus dem Exit-Plan (`--fx`), bei Fremdwährung |

## Rechnung

```
Stückzahl = max_loss_per_trade / (Entry - Stop)       Risiko je Stück in Kontowährung (x FX)
          = 100,00 EUR / |364.18 - 358.32| x FX 0.87
          = 100,00 EUR / 5,10 EUR = 19.61 -> abgerundet 19
  -> begrenzt durch /trader-risk auf 7 (Positionsgröße/Tageslimit)
  -> begrenzt durch verfügbares Budget, falls kleiner
Positionswert: 7 x 316,84 EUR = 2.217,88 EUR
Verlust am Stop: 7 x 5,10 EUR = 35,69 EUR | Gewinn am Ziel: 71,38 EUR
BUDGET: PASS, 7 Stueck
```

Immer abrunden, nie aufrunden. Nur ganze Stück (Bracket-Orders bei Alpaca
erlauben keine Bruchstücke).

## Outputs

- Die Rechnung wie oben, mit allen Begrenzungen und ihrem Grund.
- `BUDGET: PASS, N Stueck` oder `BUDGET: BLOCK - <Grund>` (z. B. weniger als 1 Stück).
- Die Stückzahl wird im Trade gespeichert; `/trader-broker` nutzt genau diese Zahl.

## Ablauf

1. Verfügbares Kapital beim Broker lesen (nur lesend) und in Kontowährung umrechnen.
2. `trader.py budget <id> --cash <betrag>`
3. Rechnung zeigen. Bei PASS: `/trader-broker <id>`.

## Regeln

- Die Stückzahl darf nie größer sein als `max_qty` aus `/trader-risk` oder als das verfügbare Budget.
- Keine Hebel, kein Margin: Positionswert <= verfügbares Kapital.
- Der Nutzer darf eine KLEINERE Stückzahl wünschen. Dafür nicht die Datei bearbeiten, sondern `max_loss_per_trade` in der Config senken oder einen neuen Exit-Plan anlegen und die Pipeline neu durchlaufen. Eine größere Stückzahl ist ausgeschlossen.
- Kurs und FX nennen mit Zeitpunkt; bei Market-Orders weicht der Füllkurs ab, der Verlust am Stop kann dadurch leicht steigen.

## Journal

Automatisch: Aktion `budget`, Größe, Ergebnis PASS/BLOCK, Begründung mit den greifenden Begrenzungen.

## Hinweis

Analyse, keine Anlageberatung. Die Positionsgröße begrenzt den geplanten
Verlust am Stop, nicht den tatsächlichen Verlust bei Gaps oder Slippage.
