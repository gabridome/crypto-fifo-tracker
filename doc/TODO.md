# TODO — Crypto FIFO Tracker

> Lista delle cose da fare, ordinata per priorità. Aggiornare ad ogni sessione.
> Ultima revisione: 2026-10-17.

---

## Priorità ALTA — Integrità e sicurezza

- [x] ~~**Atomicità DB negli importers**~~ — `import_and_verify()` in tutti i 13 importers (2026-03-30)
- [x] ~~**Atomicità DB nel FIFO engine**~~ — rimosso `conn.commit()` intermedio (2026-03-30)
- [x] ~~**Migrare a Decimal**~~ — Decimal per cost_basis/proceeds/gain_loss + _to_eur() rounding (2026-03-30)
- [x] ~~**Round esplicito**~~ — incluso nella migrazione Decimal (2026-03-30)
- [x] ~~**Path traversal fix**~~ — safe_path() + secure_filename su tutte le route (2026-03-30)
- [x] ~~**Validazione input**~~ — tipo, importo, data, exchange nel manual entry (2026-03-30)
- [x] ~~**Manual delete**~~ — `WHERE source = 'web_manual_entry'` (2026-03-30)
- [x] ~~**Rimuovere `import_bitfinex.py`**~~ — rimosso (2026-03-30)
- [x] ~~**Fix USD→EUR fallback**~~ — abort se ECB non disponibile (2026-03-30)

## Priorità MEDIA — Qualità codice

- [x] ~~**Eliminare `except Exception: pass`**~~ — 15 blocchi con logging (2026-03-30)
- [x] ~~**Connection lifecycle**~~ — try/finally su tutte le connessioni (2026-03-30)
- [x] ~~**Config path assoluti**~~ — `PROJECT_ROOT` + path assoluti (2026-03-30)
- [x] ~~**Config safe import**~~ — fallback a PT con warning (2026-03-30)
- [x] ~~**DB path da config**~~ — report generators usano config.DATABASE_PATH (2026-03-30)
- [x] ~~**Exchange country unificato**~~ — una mappa con iso + at_code + at_name (2026-03-30)
- [x] ~~**Import path consistenti**~~ — `from importers.ecb_rates` in tutti i file (2026-03-30)
- [x] ~~**Colonne mancanti**~~ — fee_currency/currency in tutti i 13 importers (2026-03-30)
- [x] ~~**Epsilon consistente**~~ — `DUST_THRESHOLD = Decimal('1e-8')` (2026-03-30)
- [x] ~~**Unused imports**~~ — rimossi Dict, usato zoneinfo al posto di pytz (2026-03-30)

## Priorità MEDIA — Testing

- [x] ~~**Test sul FIFO reale**~~ — usa `CryptoFIFOTracker` reale (2026-03-30)
- [x] ~~**Nuovi test edge case**~~ — sell > available, zero fee, dust, multi-lot (2026-03-30)
- [x] ~~**Schema unico**~~ — test leggono `doc/schema.sql` (2026-03-30)
- [x] ~~**Test esistenza file**~~ — schema, config, tracker, utils, guidelines (2026-03-30)
- [x] ~~**Portare a pytest**~~ — 33 test, tutti passano (2026-03-30)

## Priorità MEDIA — Refactoring

- [x] ~~**Estrarre CSV parser**~~ — web/csv_parser.py, app.py da 2574 a 1542 righe (2026-03-30)
- [x] ~~**Estrarre boilerplate importers**~~ — `import_and_verify()` in `import_utils.py` (2026-03-30)
- [x] ~~**Context processor caching**~~ — TTL 5s su inject_globals() (2026-03-30)

## Priorità BASSA — Miglioramenti

- [x] ~~**CSRF protection**~~ — flask-wtf CSRFProtect + auto-inject token (2026-03-30)
- [x] ~~**Secret key**~~ — os.urandom(24) (2026-03-30)
- [x] ~~**Logging**~~ — logging module in import_utils.py (2026-03-30)
- [x] ~~**`fmt_eur_filter`**~~ — mostra €0.00 per zero, '—' solo per None (2026-03-30)

---

## Priorità ALTA — Dati

- [x] ~~**Ordini Prime `Cancelled` con fill parziale**~~ — il filtro `status == 'Completed'`
  scartava esecuzioni vere (quantità, cost basis e commissioni addebitate). Ora
  `filled_orders()` tiene ogni riga con `filled base quantity > DUST_THRESHOLD`
  (2026-10-15). Dopo un fix così serve **reimport + ricalcolo FIFO**: cambiano i lotti
  aperti e la frontiera dei 365 giorni.
- [x] ~~**Stablecoin senza catena — gli strumenti**~~ — convertire BTC in USDT/USDC
  registrava la vendita del BTC ma non l'acquisto della stablecoin: nessun lotto, e
  le vendite di stablecoin restavano non matchate. Chiuso il **2026-10-16** dal
  branch `feature/non-btc-legs` (progetto:
  `doc/specs/2026-10-16-non-btc-legs-design.md`): `importers/non_btc_readers.py`
  legge le tre shape di export, `importers/non_btc_valuation.py` valorizza e
  `importers/import_non_btc_legs.py` scrive la gamba mancante, collegata alla
  prima da `swap_group_id`. La valorizzazione usa il **fiat effettivamente pagato
  sulla sede d'acquisto**, non il controvalore all'arrivo altrove, che è ciò che
  produceva i lotti a parità 1,00. Il caso **fiat → stablecoin → fiat** è trattato
  per quello che è — un acquisto e una cessione di cripto-attività, con la
  differenza di cambio come risultato fiscale — e non come permuta.
- [ ] **Stablecoin senza catena — i dati.** Lo strumento c'è; **che gli export
  reali siano stati importati e il FIFO ricalcolato non è verificato qui** (il
  task 12 non ha interrogato il database di produzione). Passo operativo, da
  spuntare solo dopo averlo guardato: importare gli export delle sedi dove stanno
  le stablecoin, ricalcolare, e confrontare i lotti aperti con i saldi reali.
- [ ] **Verificare la regola fiscale su crypto-to-crypto (domanda legale, aperta).**
  Il regime cripto dell'art. 10.º CIRS ha una norma specifica sulla **permuta** fra
  cripto-attività, e se non c'è realizzo al momento dello scambio cambiano sia gli
  eventi imponibili sia il **periodo di detenzione** che decide l'esenzione oltre i
  365 giorni. Da verificare con un professionista fiscale locale.
  **Stato degli strumenti al 2026-10-17** — la parte che il codice poteva chiudere
  è chiusa, la domanda legale no:
  - la regola è un flag dichiarato e testato, `tax_rules.swap_realizes` in
    `config.py` (task 7, `tests/test_swap_realizes.py`), stampato in ogni summary
    (`config.tax_rule_banner()`);
  - **il motore si dirama su entrambi i valori** (task 9-11: `calculate_fifo.py:66`,
    `crypto_fifo_tracker.py:271, 349, 360, 442, 623`), quindi cambiarlo **cambia le
    cifre**: non i totali su tutta la storia, ma le date — e sono le date a decidere
    quali plusvalenze superano i 365 giorni. Una versione precedente di questa voce
    e delle due guide diceva il contrario ("cambiarlo oggi non cambia nessuna
    cifra"): era vera quando è stata scritta, falsa dopo i task 9-11, rimossa dal
    task 12. **Una cautela scaduta è peggio dell'affermazione che sostituisce**: dice
    al lettore che un'assunzione legale è inerte e lo invita a non esaminarla;
  - le due guide (`doc/en/`, `doc/pt/`) non presentano più la regola come diritto
    stabilito e puntano a `doc/compliance.md`.
  Il flag rende la regola dichiarabile e cambiabile — non ne certifica la
  correttezza.
- [ ] **Vendite senza lotto d'acquisto** (es. coin da fork del 2017): quattro SELL non
  matchati, nessun cost basis. Decidere come trattarli nel report IRS.
- [ ] **Riconciliazione con il custode** — i lotti aperti del tracker vanno confrontati
  con i saldi reali (exchange + custodia propria): se non combaciano, mancano
  acquisti da una parte o dismissioni dall'altra. Vale come controllo periodico.
- [ ] **Web UI: lo stdout del subprocess viene scartato sul success path.**
  (Dal code review del task 7, 2026-10-17.) `web/app.py` — `run_fifo()` e
  `generate_report()` fanno `flash()` dell'output del subprocess solo quando
  `returncode != 0`, e anche lì solo gli ultimi 500 caratteri. Sul percorso di
  successo (il caso comune) l'output non arriva mai all'utente della web UI —
  quindi nemmeno il banner `Tax rule in force: swap_realizes=...` stampato da
  `calculate_fifo.py`/`generate_irs_report.py`/`generate_annual_summary.py` lo
  raggiunge — **compreso il blocco di avviso** che quel banner stampa quando
  l'impostazione e i dati non concordano, che è la cosa che meno di tutte può
  restare su un canale che la web UI butta.
  **Misurato il 2026-10-17 leggendo il codice e l'output, non avviando Flask:**
  il blocco d'avviso è **949 caratteri su 21 righe**, la finestra della web app
  è `[-500:]` (`web/app.py:1317` per il FIFO, `:1471` per il report), quindi non
  ci sta **su nessun percorso**; e su `returncode == 0` (`:1314-1315`,
  `:1468-1469`) lo stdout viene scartato del tutto. Nei due report misurati il
  blocco comincia a 1.483 e a 1.021 caratteri dalla fine dell'output, cioè
  interamente fuori dalla coda anche se ci stesse. Nessun altro canale lo
  mostra: le sole altre letture di `result.stdout` in `web/app.py` (`:1015`,
  `:1055`) sono sui percorsi di import e non eseguono questi script, e nessun
  template legge l'output di un sottoprocesso. **Conclusione: l'avviso non
  raggiunge l'utente della web UI**, su nessun percorso. Chi chiude questa voce
  tratti il blocco d'avviso come il caso da far passare per primo. Importa **adesso**, non "prima che": dopo i task 9-11
  `swap_realizes=False` cambia le date, e un utente della web UI non ha modo di
  scoprire sotto quale regola sono state calcolate le cifre che legge.
- [x] ~~**`calculators/crypto_fifo_tracker.py`: import `SWAP_REALIZES` non letto**~~
  — era `# noqa: F401`, tenuto solo perché un test potesse fare
  `monkeypatch.setattr('calculators.crypto_fifo_tracker.SWAP_REALIZES', ...)`.
  Chiuso dai task 9-11 (2026-10-17): il motore lo legge in cinque punti e il
  `noqa` non c'è più. Verificato con `grep -rn 'SWAP_REALIZES' calculators/`.
- [ ] **`config.py`: paese non riconosciuto ricade su PT con solo un warning su stderr.**
  (Dal code review del task 7, 2026-10-17.) Pre-esistente, non del task 7 — non
  è stato toccato. Se `COUNTRY`/`FIFO_COUNTRY` è scritto male, l'intero calcolo
  gira silenziosamente sotto le regole fiscali di un altro paese (stesso difetto
  di principio che il task 7 ha appena chiuso per `swap_realizes`, qui lasciato
  com'era). Da trattare come decisione tracciata — fallire forte come
  `swap_realizes`, o restare un fallback deliberato — non come sorpresa.
- [ ] **`config.py`: una `tax_rules.swap_realizes` mancante ora fa fallire con
  traceback anche la web app, non solo i calculator.** (Dal code review del
  task 7, 2026-10-17.) Deviazione deliberata da `doc/code_guidelines.md` §4.2
  («`import config` non deve mai fallire»), già accettata a livello di spec
  del task 7 — il motivo è che non esiste un default sicuro per un'assunzione
  legale. A verbale qui perché è un'eccezione alla regola generale, non
  un'infrazione dimenticata.

## Permuta differita — chi legge `sale_lot_matches` come registro delle cessioni

`sale_lot_matches` è il registro delle **plusvalenze realizzate**, non delle
cessioni. Con `tax_rules.swap_realizes = False` la gamba SELL di una permuta
consuma i lotti e **non scrive nessuna riga** lì: data e costo d'acquisto
passano all'asset ricevuto via `swap_lot_transfers`. È corretto — non è un
evento tassabile — ma rende silenziosamente incompleto tutto ciò che legge
quella tabella come «le cessioni», mentre tutto ciò che legge `transactions` le
conta al valore pieno.

Elenco dal quality review del task 9, ogni punto verificato nel codice. Il task
12 ha **corretto** i tre punti dove l'output di oggi inganna chi lo legge (il
riepilogo annuale, la riga «Sales matched» del driver, l'invariante in
`CLAUDE.md`) e **documentato** qui tutti gli altri. La definizione unica di
«cessione differita» sta in `calculators/swap_reporting.py`; chi chiude una di
queste voci la usi, invece di riscrivere la query.

- [x] ~~**`calculators/generate_annual_summary.py`: VENDITE e RISULTATO FIFO non
  quadravano, affiancate e senza nota**~~ — §2 *VENDITE* legge `transactions` e
  conta la permuta al `total_value` pieno, §3 *RISULTATO FIFO* e §4 *per
  exchange* leggono `sale_lot_matches` e la escludono. Un lettore che le
  riconcilia conclude che il FIFO ha perso una vendita. Chiuso 2026-10-17: tre
  note condizionali (in VENDITE, in RISULTATO FIFO e nel RIEPILOGO FINALE)
  dicono la differenza. **Nessuna cifra è cambiata.** Test:
  `tests/test_deferred_disposal_reporting.py`.
- [x] ~~**`calculators/calculate_fifo.py`: «Sales matched» escludeva la cessione
  differita senza dirlo**~~ — la contava fra le vendite stampate prima (da
  `transactions`) e non fra quelle abbinate (da `sale_lot_matches`); e con
  `if stats['sales_matched']:` una cripto le cui cessioni fossero **tutte**
  differite non stampava niente. Chiuso 2026-10-17: riga «Deferred swap
  disposals» fuori da quella guardia. `crypto_fifo_tracker.py:730` incrementa
  `sells` anche per una cessione differita e lo stampa a `:791` («Processed N
  purchases, M sales»): resta così — è il conteggio delle transazioni, ed è la
  nota del driver a spiegare lo scarto.
- [x] ~~**`CLAUDE.md`: «one row per (SELL × consumed lot)»**~~ — invariante reso
  falso dal differimento, nel file che ogni agente legge per primo. Chiuso
  2026-10-17 in entrambi i punti (diagramma e descrizione dello schema), che
  ora dicono anche che le tabelle sono quattro e non tre.
- [ ] **`web/app.py:217-219`: il setup dichiara il FIFO completo solo con
  `lot_count > 0 AND match_count > 0`.** Un database le cui uniche cessioni
  siano permute differite tiene il passo FIFO incompleto e i report bloccati,
  pur avendo un calcolo FIFO corretto e completo. **Documentato, non corretto
  dal task 12**: cambiare la soglia di completamento del setup è lavoro con un
  suo rischio e va deciso a parte.
- [ ] **`web/app.py:1336-1337` (`/reports`) e `:1626-1627` (`/audit`): le
  tendine degli anni vengono da `sale_lot_matches`.** Un anno le cui uniche
  cessioni siano state permute differite **sparisce** da entrambe le tendine:
  l'utente non può nemmeno chiedere quell'anno. **Documentato, non corretto dal
  task 12**, stessa ragione di sopra.
- [ ] **`web/app.py:1650-1679` (`/api/audit`): la pista di audit è una join pura
  su `sale_lot_matches`.** Una cessione differita non compare da nessuna parte
  nella pista, e `doc/en/audit_trail_guide.md:68` presenta quella join come la
  pista completa. Due vie per chiuderlo: estendere la pista a
  `swap_lot_transfers`, oppure dire sulla pagina che copre le sole cessioni
  realizzate. Il limite è già scritto in `doc/compliance.md` §2.
- [ ] **`web/app.py:1252-1256` e `:1263`, `:1269` (`/fifo`): `SUM(amount_sold)`
  letto come «quanto hai ceduto».** `total_gain` esclude correttamente le
  permute differite, ma la quantità ceduta è incompleta — e le pagine di import
  contano i SELL da `transactions`. Due pagine, due risposte alla stessa
  domanda. Vale anche per `/api/db-stats` (`:1724`), che pubblica `matches`
  come se fosse il numero delle cessioni.
- [ ] **`doc/schema.sql:158-170` `v_annual_gains`: `sale_count`, `total_sold`,
  `total_proceeds` diventano in silenzio «escluse le permute differite»;
  `v_exchange_summary` (`:129-139`) legge `transactions` e quindi le
  **include** al valore pieno.** La stessa contraddizione del riepilogo
  annuale, in SQL. Attenuante verificata (2026-10-17): nessuna vista è
  referenziata da Python o da un template — solo le copie del DDL le creano —
  quindi il raggio d'azione è la query scritta a mano da chi apre il database.
  Il task 12 ha messo la nota in un commento `--` sopra entrambe le viste;
  **non ha cambiato le viste**, perché cambiarle cambierebbe le cifre che
  restituiscono. Resta aperto: decidere se `v_annual_gains` debba rinominare
  quelle tre colonne (`realized_sale_count`, …) alla prossima migrazione, dove
  un rename è possibile senza rompere una query esistente in silenzio.
- [ ] **I file `.sql` monouso, tutti su `sale_lot_matches`, tutti
  silenziosamente incompleti sotto differimento**:
  `demo/queries/{gains_per_sale_year,long_vs_short_term,exchange_performances,purchases_by_sale_year,sale_matches_detailed}.sql`
  e `calculators/{exchange_performances,purchases_year_sale_2024,2024_sale_purchases_detailed}.sql`.
  Girano dal runner di `/reports` e dalla riga di comando. Il task 12 ha messo
  a ognuno un commento `--` in testa che dice cosa escludono (il runner
  strippa i commenti `--` prima del controllo SELECT/WITH: i tre in
  `calculators/` verificati chiamando `/reports/query/<file>`, tutti e otto
  contro la stessa logica di `web/app.py:1419-1431`) — **non un filtro**: un filtro cambierebbe le cifre. Resta
  aperto: nessuno di questi file stampa la regola in forza, quindi l'output
  non dice sotto quale `swap_realizes` sono state calcolate le righe che
  legge — e, a differenza dei tre report, non ha un banner che lo verifichi
  contro il database. La via naturale: farli girare dal runner di `/reports`
  con il banner in testa all'output, oppure accettare che chi li lancia a mano
  legga prima un report.
- [ ] **`doc/en/audit_trail_guide.md:95-101` e il gemello portoghese: il
  checksum di determinismo.** `SELECT SUM(gain_loss), COUNT(*) FROM
  sale_lot_matches` resta deterministico a parità di regola, ma confrontato
  **attraverso** un cambio di `swap_realizes` mostra un calo senza causa
  documentata. Il task 12 ha aggiunto la cautela a entrambe le guide (en e pt),
  insieme a quella sulla query «All sales must be matched» di `:68`, che sotto
  differimento **non** restituisce 0. Resta aperto il resto: la pagina continua
  a descrivere la pista di audit come completa, e la query di `/audit` non legge
  `swap_lot_transfers` — vedi la voce su `/api/audit` qui sopra.
- [x] ~~**`config.tax_rule_banner()` dichiarava il valore di `config.py` al
  momento della stampa come se fosse la regola che ha prodotto le cifre**~~ —
  il docstring diceva «the tax rule that produced the figures just printed»:
  vero per `calculate_fifo.py`, che stampa subito dopo aver calcolato, **falso**
  per `generate_annual_summary.py` e `generate_irs_report.py`, che leggono dal
  database cifre scritte da un'altra esecuzione sotto un'altra impostazione.
  Non teorico: è esattamente il passo 7 del task 13 (girare sotto
  `swap_realizes = False` per misurare l'altra regola, poi tornare a `True`).
  Chiuso 2026-10-17: il docstring dice il vero, la funzione prende una
  connessione opzionale e, quando ce l'ha, confronta l'impostazione con la
  **prova positiva** nel database (`calculators/swap_reporting.py`). In
  disaccordo stampa un blocco che non si scorre via, dice quale prova è
  scattata, quanta parte dei dati è coinvolta e le due vie d'uscita
  (ricalcolare, o leggere le cifre sotto la regola con cui sono nate). Tutti e
  tre i chiamanti passano la connessione. **Nessuna cifra si muove e il
  percorso di default pulito stampa la riga di prima**, verificato eseguendo i
  tre report, non dedotto.
  Le prove sono **tre**, e la terza è arrivata dal quality review: le prime due
  (`deferred_disposals`, `realized_swap_disposals`) guardano solo la gamba che
  **cede**, quindi non vedevano lo stato in cui si ricalcola solo l'asset
  ceduto dopo un cambio d'impostazione — i lotti **ereditati** dall'asset
  ricevuto restano in `fifo_lots`, e il `DELETE FROM swap_lot_transfers`, che
  gira anche sotto `True`, porta via l'ultima traccia. Riprodotto: giro
  differito completo, poi `calculate_fifo_lots('ETH')` sotto `True` — la gamba
  che cede realizza, i trasferimenti spariscono, e il lotto USDT conserva
  €1.000 di costo d'acquisto e una data del 2020 contro una gamba BUY del 2024.
  Lo stesso costo contato da due lati e una plusvalenza tassabile spostata nella
  colonna esente dell'Anexo G1, sotto una riga pulita. `inherited_lots` lo vede.
  **Limite della terza prova, dichiarato**: una permuta avvenuta lo stesso
  giorno dell'acquisto consumato eredita quello stesso giorno, quindi le due
  date coincidono e il lotto è indistinguibile da uno ordinario — la prova è
  **sufficiente e non necessaria**.

## Limiti noti — non difetti aperti

- **Andata e ritorno fra due asset in permuta differita, il motore non lo sa
  esprimere.** Calcola una criptovaluta per volta, quindi A→B in aprile e B→A in
  giugno vorrebbe A fino ad aprile, poi B, poi A di nuovo: `swap_dependency_order`
  vede il ciclo e **rifiuta**, invece di scegliere un lato in silenzio. Cosa
  servirebbe a risolverlo: una passata unica su **tutte** le cripto in ordine di
  data, al posto del ciclo per criptovaluta — cioè riscrivere il codice che
  produce le cifre già dichiarate. **È un limite, non un difetto aperto**: sugli
  export reali le permute vanno in un verso solo (verifica del proprietario dei
  dati, non ripetuta dal task 12), quindi oggi il caso non è raggiungibile. Da
  rivalutare solo se un export futuro ne contiene uno — e allora il motore lo
  dice, non lo indovina.
- **Tre copie del DDL, ora due.** `doc/schema.sql` è la fonte unica
  (`doc/project_guidelines.md`, `doc/code_guidelines.md` §4.3). Il
  `schema.sql` nella radice e il DDL incorporato in `setup.sh:156-246` non
  avevano né `transactions.swap_group_id` né `swap_lot_transfers` (zero
  occorrenze di "swap" in entrambi), e un database costruito da uno dei due
  viene rifiutato dal primo controllo del motore. Decisione del task 12
  (2026-10-17): il `schema.sql` della radice **non è più una copia**, è un
  puntatore a `doc/schema.sql` — nessun file del repo lo leggeva (verificato con
  grep; `README.md:130` indica `doc/schema.sql`, dentro l'albero di `doc/`, non
  quello della radice). Resta aperto il DDL incorporato in `setup.sh`: è il ramo
  `else` di `setup.sh:144-146`, che preferisce `doc/schema.sql` quando esiste —
  e in un clone esiste sempre, perché è tracciato. Chiusura naturale: far
  **fallire** quel ramo invece di costruire un database che il motore rifiuterà.
- **`calculate_fifo.py:15` importa il motore come modulo top-level** mentre i
  test importano `calculators.crypto_fifo_tracker`: due oggetti modulo, due
  `SWAP_REALIZES` distinti. Non è un difetto di comportamento — il driver legge
  il valore vero da `config` — ma rende `main()` non pilotabile da un test
  in-process, e per questo i test del task 9 riproducono la forma del loop del
  driver invece di chiamarlo, e il task 12 presidia la riga «Deferred swap
  disposals» con un'asserzione sul sorgente. (Dal code review del task 9,
  2026-10-17.)
- **Su un database anteriore alla migrazione `swap_group_id` il banner tace
  anche sotto `False`.** Senza quella colonna nessuna riga è identificabile come
  gamba di permuta, quindi tutte e tre le prove leggono zero e la riga esce
  pulita — per **mancanza di prove**, non per mancanza di contraddizione. Lì
  nessun giro differito è stato possibile (il motore lo rifiuta senza la tabella
  `swap_lot_transfers`), quindi con l'impostazione a `False` il banner nomina
  una regola che provabilmente non ha prodotto nessuna di quelle cifre. Non lo
  si trasforma in un allarme: la prova è categorica in una sola direzione e il
  caso scatta solo sotto la regola non di default, su un database che il motore
  rifiuterebbe comunque di ricalcolare. **Il problema è il silenzio
  indistinguibile dall'accordo**, ed è questo che sta scritto nel docstring di
  `config.tax_rule_banner` — una riga pulita stabilisce che nessuna cessione e
  nessun lotto ereditato *sul registro* contraddice l'impostazione, non che
  l'impostazione ha prodotto quelle cifre.
- **`doc/pt/audit_trail_guide (1).md`** è un duplicato di
  `doc/pt/audit_trail_guide.md` (stessa lunghezza), evidentemente un download
  ripetuto. Da rimuovere, previa conferma di quale dei due è il buono.

## Operativo / Infrastruttura

- [ ] **Niente rileva un lotto orfano** — un `fifo_lots.purchase_transaction_id`
  che non ha più una riga in `transactions`, per esempio dopo una transazione
  cancellata a calcolo già fatto o un restore parziale. La `FOREIGN KEY` è
  dichiarata in `doc/schema.sql` ma SQLite **non la applica** senza
  `PRAGMA foreign_keys = ON`, che nessun file del progetto imposta (verificato:
  zero occorrenze). `import_and_verify()` verifica il numero di righe inserite,
  non l'integrità dei riferimenti, e il controllo chiamato «orphan» nel motore
  riguarda un'altra cosa — un gruppo di permuta la cui gamba BUY non è mai stata
  importata. Un lotto orfano continua a produrre match, con un costo e una data
  che non si possono più risalire a un acquisto: la pista di audit si interrompe
  e il report non lo dice. Chiusura naturale: un controllo nel passo di verifica
  che conti i lotti senza acquisto e rifiuti, oppure `PRAGMA foreign_keys = ON`
  sulla connessione — che però va misurato sul resto del progetto prima, perché
  renderebbe applicate anche le altre due FK dichiarate.
  **La spec di questo branch (§9) e il piano affermavano che un passo di verifica
  li segnala: non esiste.** (Trovato dal task 12, 2026-10-17.)

- [ ] **Un BUY senza prezzo unitario perde il costo di base nel match** —
  preesistente, non introdotto dal branch non-BTC. Il lotto prende
  `cost_basis` da `total_value` ma lascia `purchase_price_per_unit = 0`; il
  match però ricalcola il costo **dal prezzo unitario**, non da `cost_basis`,
  quindi del costo resta solo la commissione e la plusvalenza risulta
  sovrastimata. **Latente, non vivo**: sul database di produzione, interrogato
  in sola lettura il 2026-10-17, i lotti con prezzo unitario assente e costo
  presente sono **zero**. Le vie per arrivarci sono `import_standard_csv.py` e
  il form `/manual` quando il prezzo manca. Il ramo che eredita del task 10
  evita la trappola mettendo `purchase_price_per_unit = cost / qty`: la
  trappola è stata vista ma non chiusa dove già mordeva.
  (Dal code review del task 10, 2026-10-17.)
- [ ] **Tre convenzioni diverse sulla stessa data** — `ORDER BY
  transaction_date` ordina le stringhe, `_acquisition_key` confronta istanti
  assoluti, `holding_period_days` confronta la data di calendario **come
  scritta**. Con le sole forme che gli importer dedicati producono ('+00:00' e
  naive) le tre concordano; con un offset diverso da UTC divergono, e FIFO
  consumerebbe un lotto che la convenzione del periodo di detenzione considera
  più recente. **Latente, non vivo**: zero date con offset diverso da UTC sul
  database di produzione (sola lettura, 2026-10-17). La via è
  `import_standard_csv.py`, che usa `pd.to_datetime` e conserva l'offset,
  mentre `/exchange-docs` documenta il campo come `+00:00`. Chiusura naturale:
  normalizzare a UTC all'importazione, così le convenzioni concordano per
  costruzione. (Dal code review del task 10, 2026-10-17.)
- [ ] **La pista di audit di un lotto ereditato si ferma alla permuta** — il
  drill-down di `/audit` unisce `fifo_lots.purchase_transaction_id` a
  `transactions` e mostra commissione, sorgente e `record_hash` di quella
  riga. Per un lotto ereditato quella riga è la gamba BUY della permuta: la
  sua commissione non è nel costo del lotto e il suo `amount` non è la
  quantità del lotto. Il collegamento all'acquisto da cui il costo viene
  davvero esiste — `swap_lot_transfers.source_fifo_lot_id` — ma nessuna query
  del progetto legge quella tabella. **Il task 12 non lo ha chiuso**: ha scritto
  il limite dove un terzo lo legge (`doc/compliance.md` §2, «What the audit
  trail does not yet cover») e corretto sotto la voce spuntata qui più in basso
  la dicitura che dava la tracciabilità per completa.
  (Dal code review del task 10, 2026-10-17.)

- [ ] **Dichiarare esplicitamente la modalità di transazione di `sqlite3`** —
  oggi ciò che rende efficace il `rollback()` di `calculate_fifo_lots` non è
  codice nostro: è il controllo di transazione legacy del modulo `sqlite3`, che
  apre una transazione implicita prima del primo DML. Python ha annunciato il
  passaggio all'autocommit come default; quel giorno, un `rollback()` senza
  `BEGIN` esplicito non annulla più niente. Il `BEGIN` esplicito c'è ed è
  presidiato da `test_the_write_phase_opens_an_explicit_transaction`, quindi
  non c'è un difetto aperto — ma la difesa vera è `autocommit=False`, che
  **non esiste su Python 3.11** (misurato: `TypeError`), ed è il piano
  d'appoggio che `pyproject.toml` dichiara con `target-version = "py311"`.
  `isolation_level=''` non serve: ridichiara il default di oggi e dalla 3.12 è
  scavalcata quando `autocommit` è impostato. Primo passo quando si farà:
  verificare che `autocommit=False` sia neutro per gli script di report e per
  la web app, che usano la stessa classe — cambia **quando** le transazioni si
  aprono, anche per le SELECT, e questo non è verificato.
  (Dal code review del task 9, 2026-10-17.)

- [x] ~~**Audit trail / tracciabilità fiscale**~~ — pagina /audit con drill-down per riga IRS,
  Print CSS (2026-03-30). **Non è completa**, e non lo era già prima del differimento:
  copre le cessioni **realizzate**, e per un lotto ereditato si ferma alla gamba
  d'acquisto della permuta. Le due voci aperte sopra ne tengono conto.
- [x] ~~**Backup data/ su Google Drive**~~ — `backup_drive.sh` con rclone (2026-03-30)
- [x] ~~**Manuale importatori**~~ — `doc/en/IMPORT_MAPPING.md` esisteva già (587 righe), aggiornato con source tracking (2026-03-30)
- [x] ~~**Semplificazione web UI**~~ — pagina unificata `/exchanges` (2026-03-30)
- [x] ~~**Enforcement layer**~~ — CLAUDE.md preamble, hooks, skill `before-coding`, git pre-commit (2026-04-26)

---

## Completate

- [x] ~~Wirex EUR mancanti~~ — usa CryptoPrices (2026-03-03)
- [x] ~~Storico prezzi crypto~~ — CryptoCompare API integrato (2026-03-03)
- [x] ~~Crypto-to-crypto EUR~~ — import_standard_csv.py calcola EUR via CryptoPrices (2026-03-03)
- [x] ~~Coinpal USD→EUR~~ — convertito via ECB (2026-03-03)
- [x] ~~Source tracking~~ — migrate + backfill completati (2026-03-03)
