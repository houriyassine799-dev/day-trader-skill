---
name: trader-broker
description: >-
  Order Executor, Pflichtschritt 5 und einziger Skill, der eine Order senden
  darf. Sendet nur Orders, die /trader-risk freigegeben hat und die die ganze
  Pipeline (exit, timemachine, risk, budget) bestanden haben, an die
  Broker-API (Alpaca Paper API über den MCP-Server alpaca). Zeigt vorher eine
  Zusammenfassung (Ticker, Richtung, Stückzahl, Entry, Stop, Target, Modus
  paper/live) und wartet auf ein ausdrückliches "Ja". Nutzt Bracket-Orders,
  damit Stop und Target direkt mit angelegt werden. Verwenden bei "Order
  senden", "kaufen", "ausführen", "place the order", "/trader-broker", und
  nach /trader-budget.
argument-hint: "<trade-id>"
---

# /trader-broker: Order Executor

Der strengste Skill des Plugins. Eine Order erreicht den Broker nur, wenn
ALLES zutrifft:

1. exit, timemachine, risk und budget sind PASS, aktuell (`pipeline.max_age_minutes`) und für genau diesen Plan.
2. Der finale Risk-Re-Check mit der endgültigen Stückzahl ist PASS.
3. Die Zusammenfassung wurde gezeigt, und der Nutzer hat danach mit **"Ja"** geantwortet (innerhalb von `pipeline.confirm_within_minutes`).
4. `trader.py confirm` hat das "Ja" akzeptiert.
5. Der Modus des Broker-Kontos passt zur Config (paper/live).

`trader.py` steht für `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py"`.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

- Trade-ID mit Pipeline-Status (in `trader/trades/<id>.json`).
- Config: `mode`, `broker`.
- Broker-Verbindung: MCP-Server `alpaca` aus `.mcp.json`, Keys aus
  `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, `ALPACA_PAPER_TRADE` (Standard `true`).

## Outputs

1. Die Zusammenfassung:
   ```
   ORDER-ZUSAMMENFASSUNG  (Modus: PAPER, Broker: Alpaca)
     Ticker:    TSLA (NASDAQ)
     Richtung:  KAUF (long)
     Stueck:    7
     Entry:     ca. 364.18 USD (Market-Order, Fuellkurs kann abweichen)
     Stop:      358.32 USD
     Target:    375.90 USD
     Max. Verlust am Stop: 35,69 EUR | Gewinn am Ziel: 71,38 EUR
     Bracket-Order: Stop und Target werden direkt mit angelegt.
     Order-JSON: {"symbol": "TSLA", "side": "buy", "qty": "7", "type": "market", "time_in_force": "day",
                  "order_class": "bracket", "take_profit_limit_price": "375.90", "stop_loss_stop_price": "358.32"}
   Senden? Antworte mit "Ja" für genau diese Order. Alles andere bricht ab.
   ```
2. Nach "Ja": Broker-Antwort (Order-ID, Status, Füllkurs) und Journal-Eintrag.

## Ablauf

1. **Modus prüfen.** `trader.py config`. Dann `get_account_info` (nur lesend):
   - Config `paper` → das Konto MUSS ein Paper-Konto sein (Alpaca: `ALPACA_PAPER_TRADE` ist `true`,
     Paper-Kontonummern beginnen mit `PA`). Ist es ein Live-Konto: STOPP, nichts senden, Nutzer informieren.
   - Config `live` → deutlich warnen: "LIVE-MODUS: echtes Geld". Ist das Konto ein Paper-Konto, ebenfalls STOPP.
2. **Zusammenfassung erzeugen:** `trader.py broker <id>`. Bei BLOCK: Gründe zeigen, Ende.
3. **Zusammenfassung zeigen und auf die Antwort warten.** Genau eine Order pro Frage. Nie "Ja für alle" anbieten.
   Diese Nachricht endet mit der Frage; im selben Zug wird nichts gesendet.
4. **Antwort prüfen:** `trader.py confirm <id> --reply "<Antwort des Nutzers, wörtlich>"`.
   Nur ein ausdrückliches "Ja" (auch "ja", "Ja.", "Ja!") gibt frei. "ok", "mach", "yes",
   "ja, aber mit 10 Stück" usw. geben NICHT frei; dann erneut fragen oder abbrechen.
   Nie eine Antwort selbst als Ja deuten.
5. **Senden:** erst nach "FREIGEGEBEN" das Tool `place_stock_order` des Servers `alpaca`
   mit GENAU dem ausgegebenen JSON aufrufen. Nichts ändern, nichts ergänzen.
6. **Ergebnis festhalten:**
   - Erfolg: `trader.py placed <id> --broker-id <id> --filled-qty <n> --filled-price <p>` (Füllung aus der Antwort oder `get_orders`/`get_open_position`).
   - Abgelehnt/Fehler: `trader.py placed <id> --status rejected --note "<Fehlermeldung>"`. Kein automatischer Neuversuch; eine neue Order braucht einen neuen Re-Check und ein neues "Ja".
7. **Danach:** `/trader-alerts` mit `alert from-trade --trade <id>` für Stop, Ziel und Flat-by-Zeit.
8. **Schließen:** Wenn Stop, Ziel oder Glattstellung gefüllt sind: `trader.py close <id> --exit-price <p> --reason "<stop|target|flat>"`.
   Glattstellen vor Börsenschluss ist eine neue Order: Zusammenfassung zeigen, auf "Ja" warten,
   vorher die offenen Bracket-Teile stornieren (`cancel_order_by_id`, ebenfalls nur mit Zustimmung).

## Bracket-Orders (Alpaca)

`place_stock_order` mit `take_profit_limit_price` und `stop_loss_stop_price`
wird als `order_class: "bracket"` angelegt: Einstieg plus zwei
gegenseitig stornierende Ausstiegs-Orders (OCO). Nur ganze Stück,
`time_in_force` `day` oder `gtc`. Day-Orders verfallen zum Börsenschluss;
offene Positionen also vorher selbst glattstellen.

## Berechtigungen absichern

In Claude Code das Order-Tool nie auf "immer erlauben" setzen. In
`.claude/settings.json` des Projekts:

```json
{ "permissions": { "ask": ["mcp__plugin_trader_alpaca__place_stock_order",
                           "mcp__plugin_trader_alpaca__cancel_order_by_id",
                           "mcp__plugin_trader_alpaca__close_position"],
                   "deny": ["mcp__plugin_trader_alpaca__close_all_positions",
                            "mcp__plugin_trader_alpaca__place_option_order",
                            "mcp__plugin_trader_alpaca__place_crypto_order"] } }
```

(Exakte Tool-Namen mit `/mcp` prüfen.) Dann fragt Claude Code vor jedem Senden
zusätzlich mit seiner eigenen Bestätigung.

## Regeln

- Ohne alle fünf Bedingungen oben: keine Order. Keine Ausnahme, auch nicht im Paper-Modus.
- Nie Orders aus einem geplanten Task, einer Schleife oder im Hintergrund senden: dort kann niemand "Ja" sagen.
- Nie zwei Orders auf ein "Ja". Nie die Stückzahl, den Stop oder das Ziel nach dem "Ja" ändern.
- Nie `mode` oder `ALPACA_PAPER_TRADE` auf live umstellen. Das macht nur der Nutzer selbst.
- Nie Keys erfragen, lesen, anzeigen oder speichern. Fehlt ein Key: `trader.py env` zeigt, welcher.
- Keine Optionen, Krypto, Margin oder Leerverkäufe ohne ausdrückliche Config-Freigabe.

## Andere Broker

- **Interactive Brokers**: kein Connector in `.mcp.json`. Über die Client Portal Web API
  (lokales Gateway) oder einen IBKR-Connector des Nutzers; Bracket entspricht einer
  Parent-Order mit zwei Child-Orders (`parentId`). Gleiche Gates, gleiches "Ja".
- Für jeden Broker gilt: Zugangsdaten nur als Umgebungsvariablen, Paper-Konto zuerst.

## Journal

Automatisch: `broker-preview`, `broker-block`, `confirm` (mit der wörtlichen Antwort),
`order-placed`/`order-rejected`, `close` mit Ergebnis in Kontowährung und R.

## Hinweis

Analyse und Werkzeug, keine Anlageberatung. Ein PASS ist eine Regelprüfung,
keine Empfehlung. Market-Orders und Stops werden zum Marktpreis ausgeführt,
der vom gezeigten Kurs abweichen kann.
