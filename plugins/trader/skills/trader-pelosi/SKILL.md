---
name: trader-pelosi
description: >-
  Congress Trade Tracker. Liest die Aktiengeschäfts-Offenlegungen von
  Kongressmitgliedern (House Clerk Financial Disclosures, Periodic Transaction
  Reports, und Senate eFD), sobald sie veröffentlicht werden. Ausgabe: Name,
  Ticker, Kauf/Verkauf, Betragsspanne, Transaktionsdatum UND
  Veröffentlichungsdatum, mit Zeitverzug in Tagen. Weist immer darauf hin,
  dass Meldungen bis zu 45 Tage nach dem Trade kommen. Verwenden bei "was
  kauft Pelosi", "Congress Trades", "Politiker-Aktien", "hat ein Senator X
  gekauft", "/trader-pelosi".
argument-hint: "[Name] [--ticker NVDA] [--days 30]"
---

# /trader-pelosi: Congress Trade Tracker

Mitglieder des US-Kongresses müssen Wertpapiergeschäfte (über 1.000 USD)
nach dem STOCK Act offenlegen: innerhalb von 30 Tagen nach Kenntnis, spätestens
45 Tage nach dem Trade. Dieser Skill liest diese öffentlichen Meldungen. Er
ist schnell, aber nie früh: Die Information ist Tage bis Wochen alt.

## Globale Regeln (gelten immer)

1. Paper Trading ist Standard. Echtes Geld nur bei `"mode": "live"` in `trader/trader-config.json`.
2. Keine Order ohne ausdrückliches "Ja" des Nutzers im Chat, auch nicht im Paper-Modus.
3. Vor jeder Order: /trader-exit → /trader-timemachine → /trader-risk → /trader-budget → /trader-broker. Fehlt ein Schritt oder schlägt einer fehl, ist die Order blockiert.
4. API-Keys nur aus Umgebungsvariablen. Nie erfragen, speichern oder ausgeben.
5. Analyse, keine Anlageberatung. Jede Aussage nennt Datengrundlage, Datum der Daten und Unsicherheit.
6. Jede Aktion landet im Journal `trader/journal.csv`.

## Inputs

- Optional: Name eines Mitglieds (Standard: alle), Ticker-Filter (Standard: Watchlist markieren), Zeitraum (Standard: 30 Tage Veröffentlichung).

## Datenquellen (kein Connector in `.mcp.json`)

**House (frei, kein Key):**
- Jahresindex: `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<JAHR>FD.zip`
  (enthält `<JAHR>FD.xml` bzw. `.txt`; Zeilen mit `FilingType` = `P` sind Periodic Transaction Reports;
  Felder u. a. `Last`, `First`, `StateDst`, `FilingDate`, `DocID`).
- Einzelner PTR als PDF: `https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/<JAHR>/<DocID>.pdf`.
  Text mit `pdftotext -layout` extrahieren (poppler) oder das PDF direkt lesen. Gescannte, handschriftliche
  PDFs können unlesbar sein: dann so melden, nichts raten.
- `FilingDate` im Index = Veröffentlichungsdatum. Transaktionsdatum steht je Zeile im PDF.

**Senate eFD (frei, aber mit Hürde):** `https://efdsearch.senate.gov/search/` verlangt das Akzeptieren
einer Nutzungsvereinbarung (Session-Cookie, CSRF-Token) bevor die Suche nach "Periodic Transactions"
Ergebnisse liefert. Automatisierter Abruf ist fragil; im Zweifel den Nutzer bitten, die Suche im Browser
zu öffnen, oder eine aufbereitete API nutzen.

**Aufbereitete APIs (optional, Key nötig):**
- Quiver Quantitative: `GET https://api.quiverquant.com/beta/live/congresstrading`
  mit Header `Authorization: Bearer $QUIVER_API_KEY` (House und Senate, mit Transaktions- und Meldedatum).
- Finnhub: `GET https://finnhub.io/api/v1/stock/congressional-trading?symbol=NVDA`
  mit `X-Finnhub-Token: $FINNHUB_API_KEY` (je nach Plan verfügbar).

Keys nur über Umgebungsvariablen; `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trader.py" env QUIVER_API_KEY FINNHUB_API_KEY`
zeigt, ob sie gesetzt sind. Bei aufbereiteten Daten immer zusätzlich den Link zum Original-PDF/Filing angeben, wenn vorhanden.

## Outputs

| Name | Kammer | Ticker | Kauf/Verkauf | Betragsspanne | Transaktionsdatum | Veröffentlicht | Verzug (Tage) | Quelle |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

- Inhaber (selbst, Ehepartner, Kind) nennen, wenn angegeben.
- Optionen: Typ, Strike, Verfall.
- Verzug = Veröffentlichung minus Transaktion. Über 45 Tage: als "verspätet gemeldet" markieren.
- Watchlist-Ticker hervorheben.
- Fester Hinweis unter jeder Tabelle: "Meldungen kommen bis zu 45 Tage nach dem Trade (STOCK Act). Diese Daten sind öffentlich und alt; sie zeigen nicht, was das Mitglied heute hält."

## Ablauf

1. Index des laufenden Jahres laden (bei Jahreswechsel auch das Vorjahr), PTRs im Zeitraum filtern.
2. Neue Meldungen seit dem letzten Lauf erkennen (gesehene `DocID`s in `trader/state/pelosi-seen.json`).
3. Jede neue PTR lesen und in Zeilen zerlegen.
4. Tabelle ausgeben; Journal: `trader.py journal --action congress --ticker NVDA --result "Kauf 1-5 Mio USD" --reason "PTR <DocID>, Trade 2026-09-12, veröffentlicht 2026-10-20"`.
5. Für laufende Überwachung: geplanter Task (z. B. stündlich), der nur meldet.

## Regeln

- Transaktionsdatum UND Veröffentlichungsdatum sind Pflicht; fehlt eines, so kennzeichnen.
- Immer auf den Zeitverzug (bis 45 Tage) hinweisen. Nie suggerieren, man könne "mit" einem Politiker handeln.
- Keine Vermutungen über Insiderwissen oder Motive; nur die gemeldeten Fakten.
- Betragsspannen nie als exakte Beträge darstellen.
- Löst keine Order aus.

## Hinweis

Analyse, keine Anlageberatung. Offenlegungen sind öffentlich, verspätet und
grob (Spannen statt Beträge); ein Nachahmen der Trades hat keinen belegten Vorteil.
