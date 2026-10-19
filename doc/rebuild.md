# Ricostruire il database dai soli file in `data/`

> Scritto il 2026-10-19, dopo la prima prova riuscita. Vale per chiunque: la
> domanda a cui risponde è *«se perdo `data/crypto_fifo.db`, posso rifarlo, e le
> cifre della dichiarazione sono le stesse?»*. Risposta: **sì, dimostrato** —
> con una differenza nota, dichiarata qui sotto.

## I due comandi

```bash
# 1. ricostruire in un database NUOVO (mai sopra quello vivo, la prima volta)
python3 import_all_data.py --db /tmp/ricostruito.db

# 2. dimostrare che dice le stesse cifre di quello vivo, allegato compreso
python3 verify_rebuild.py data/crypto_fifo.db /tmp/ricostruito.db --annex 2024
```

Il secondo esce `0` solo se tutto ciò che è confrontabile combacia. Se esce `1`,
le cifre non sono da usare finché la differenza non è capita — è il punto di
questo strumento.

Prima di eseguire, `python3 import_all_data.py --dry-run` stampa i 26 passi
senza toccare nulla.

## Cos'è la catena

Quattro fasi, e l'ordine non è cosmetico:

| fase | cosa fa | perché deve stare qui |
|---|---|---|
| 1 | i **22 export** degli exchange → `transactions` | niente da cui dipenda |
| 2 | le **gambe non-BTC** di 3 export | gli importer BTC scartano il lato stablecoin per progetto (`doc/known_import_issues.md`); questi reader lo mettono a verbale |
| 3 | `swap_group_id` sulle gambe già scritte | serve che entrambe le gambe esistano |
| 4 | **FIFO**: lotti e match di vendita | legge tutte le transazioni |

Poi, fuori dalla ricostruzione, l'allegato:

```bash
python3 calculators/generate_irs_report.py 2024 data/crypto_fifo.db
# → <cartella del db>/reports/IRS_Crypto_FIFO_2024.xlsx
```

Due file di `data/` non sono export ma servono comunque, e senza di loro
l'import muore a metà: `eurusd.csv` (cache dei tassi BCE, alimenta ogni
conversione da USD) e `crypto_prices.csv` (valorizza le gambe crypto-crypto).

**Durante una ricostruzione quei due file sono ingressi, non cache.**
`importers/ecb_rates.py` con `auto_fetch` attivo interroga l'API Frankfurter
quando trova il file stantio e **riscrive `data/eurusd.csv`**: una
ricostruzione che riscrive uno dei file che non si rigenerano contraddirebbe
tutto ciò per cui questa catena esiste, e legherebbe il risultato al calendario
e alla rete invece che al contenuto di `data/`. Per questo
`import_all_data.py` passa `ECB_AUTO_FETCH=0` a ogni passo (dal 2026-10-19,
dopo che una review ha misurato che il file stava per diventare stantio il
giorno dopo). Per aggiornare i tassi di proposito si lancia un import
singolo senza quella variabile.
`import_all_data.py` li controlla **prima** di scrivere qualsiasi cosa, insieme
ai 25 export (22 importati nella fase 1 e 3 letti solo per le gambe non-BTC) e
agli script delle fasi 2 e 3, e si rifiuta di partire se ne manca uno.

## La prova del 2026-10-19

Ricostruzione in una cartella separata, con una copia degli export, e confronto
col database vivo. **Una precisazione che la prima stesura di questo documento
sbagliava**: `--data-dir` redirige gli export ma **non** le due cache dei tassi,
che gli importer leggono da `<repo>/data/` comunque — quindi la prova usava i
tassi del repo, non quelli della copia. Se la copia era integrale il confronto
resta valido — ma questo non l'ho verificato file per file, e la cartella
comunque non era isolata: ora lo script lo dice a schermo, e `doc/TODO.md` ha la
voce per chiudere il buco.

| confronto | esito |
|---|---|
| transazioni | stesso numero |
| lotti FIFO | stesso numero |
| match di vendita | stesso numero |
| `swap_group_id` scritti | stesso numero |
| trasferimenti di lotto delle permute differite | **identici** riga per riga, e dentro l'impronta |
| transazioni, colonne confrontabili (date, tipi, importi, prezzi, valori, fee, valute, exchange, source, hash, swap, note) | **hash del multiset identico** |
| lotti: date, quantità, costi | **identici** |
| match: importi, cost basis, proventi, gain, giorni di detenzione | **identici** |
| aggregati fiscali, tutti i 12 anni | **identici** |
| giacenze aperte | **identiche**: numero di lotti, quantità residua e cost basis |
| **allegato IRS 2024, cella per cella** | **tutte le celle identiche**, salvo l'unica che deve differire: l'ora di generazione |

Durata: **una decina di secondi** per la catena intera — import, gambe non-BTC,
link e FIFO — di cui meno di un secondo il solo FIFO. (Il vecchio avviso «10-20
minutes» dentro `calculate_fifo.py` era sbagliato di tre ordini di grandezza ed
è stato corretto con la misura.)

## Le differenze note

Due, entrambe dichiarate e riportate da `verify_rebuild.py` come «spiegato».
Qualunque **altra** differenza fa uscire lo strumento con errore.

### 1. `fee_currency`: vuota nel vivo, `EUR` nel ricostruito

Riguarda le righe importate da versioni vecchie degli importer, che sono la
maggior parte: lì il database vivo ha `NULL`, mentre `doc/schema.sql` dichiara `fee_currency TEXT DEFAULT 'EUR'`, e
quindi una ricostruzione riempie quel vuoto. **Nessun effetto su nessuna cifra**
(le commissioni di quelle righe sono in euro comunque, e le colonne degli importi
sono identiche). Il ricostruito è, su questo punto, più corretto del vivo.

La deroga nello strumento è strettissima — **solo** vuoto → `EUR`, a parità di
numero di righe per ogni `source`. `USD` → `EUR` sarebbe un errore di
conversione, e viene bocciato: v. `tests/test_verify_rebuild.py`.

### 2. Tre colonne vestigiali, vuote

`order_id`, `trade_pair`, `import_batch_id` esistono nella tabella viva, sono
**interamente NULL** e nessuna riga di codice le legge. `doc/schema.sql` non le
dichiara, quindi un ricostruito non le ha. Nessun dato perso. Se una di loro
contenesse valori, `verify_rebuild.py` **boccerebbe** invece di spiegare.

`import_source` era il quarto caso e **non** è una differenza: è vestigiale ma
popolata (costante `'CSV'` su tutte le righe), quindi è stata dichiarata in
`doc/schema.sql` il 2026-10-19 perché un ricostruito non perda una colonna che
c'è. La sua rimozione è una decisione separata, con migrazione: v. `doc/TODO.md`.

## Etichette: perché due CSV sono stati corretti

Nella prima prova tre righe tornavano con l'exchange sbagliato: le due righe
OTC del 2017 e la vendita OTC del 2024 erano etichettate `Binance` invece di
`OTC` e `Binance OTC`. Causa: `import_standard_csv.py` prende l'`exchange_name`
**dal file**, e dentro `otc_2017.csv` e `binance_otc_2024.csv` c'era scritto
`Binance` — che è anche sbagliato nel merito, perché quelle operazioni non sono
passate dal book di Binance.

Corretti i due file (una cella ciascuno, registrata in
`data/manual_entries_log.md`), le etichette tornano identiche — e con loro il
`record_hash`, che include l'exchange. È la ragione per cui oggi la colonna
`exchange_name` e la colonna `record_hash` risultano identiche al database vivo
e non c'è nulla da spiegare su di loro.

**Regola che ne segue**: per i file in formato standard, l'etichetta vera sta
nel file; l'argomento passato a riga di comando non la sovrascrive. Se un giorno
una riga comparisse con l'exchange sbagliato, è il CSV da correggere, non il
comando.

## Gli ingredienti: `scripts/backup_data_inputs.sh`

`data/` è **in `.gitignore`** (riga 6): gli export non sono versionati da nessuna
parte. La ricetta è in codice e dimostrata, ma senza gli ingredienti non
ricostruisce niente — e il repo è pubblico, quindi non è lì che possono andare.

```bash
scripts/backup_data_inputs.sh --dry-run   # crea e verifica, non carica
scripts/backup_data_inputs.sh             # crea, verifica e carica
```

Archivia i **30 file** di `data/` che non si rigenerano: i 28 CSV — 25 export,
le due cache (tassi BCE e prezzi) e il template del manual entry — più i due
registri di provenienza. Circa 15 MB, **3,3 MB cifrati**. Fuori restano il
database (si ricostruisce in una decina di secondi), `data/backups` e
`data/reports`, che sono tutti derivabili. Unica eccezione alla regola: il
template è anche versionato nel repo, quindi è l'unico file dell'archivio che
si potrebbe rigenerare — ci resta perché escluderlo per nome costerebbe più di
quanto valga.

Dentro l'archivio c'è un `MANIFEST.txt` con lo sha256 di ogni file, le istruzioni
di ripristino e **l'impronta di determinismo** del database
(`verify_rebuild.py --fingerprint`). Serve al caso che conta: dopo un disastro
non esiste un database di riferimento con cui confrontare la ricostruzione, e i
numeri attesi devono viaggiare con gli ingredienti. L'impronta è costruita su un
elenco **fisso** di colonne (`FINGERPRINT_COLUMNS`) proprio perché il vivo e un
ricostruito diano le stesse righe nonostante le due differenze note.

Conviene cifrarla a **due chiavi**: una la cui sottochiave di cifratura sta su
un token — un backup che si apre solo col token in mano è quello che si vuole —
e una la cui parte privata sta sulla macchina. Con entrambe l'archivio si apre
se manca il token **o** se manca la macchina; la protezione effettiva è quella
della più debole, ed è una scelta deliberata a favore della disponibilità. Quali
chiavi, e dove caricare, non stanno scritti qui: sono `GPG_RECIPIENTS` e
`BACKUP_DEST` nell'ambiente di chi usa lo strumento. Lo script verifica che
l'archivio sia cifrato **esattamente** al numero di destinatari richiesto e che
i keyid visibili siano quelli, perché una riga `encrypt-to` in `gpg.conf` o
l'espansione di un gruppo possono aggiungere una chiave che non hai chiesto.

Destinazione e destinatari **non sono scritti nel repo**: lo script li prende da
`BACKUP_DEST` e `GPG_RECIPIENTS` e si rifiuta di partire se mancano. Vanno nel
proprio ambiente (`~/.zshrc` o un file non versionato), perché sono dettagli
operativi di chi usa lo strumento, non dello strumento.

La scelta sensata è un server che già si usa e si sa mantenere — per esempio
quello che ospita il proprio `password-store` — in una directory **accanto** al
repo di `pass` e **non dentro**. Dentro significherebbe aggiungere la dimensione
intera dell'archivio alla storia git a ogni aggiornamento, in un repo pensato
per restare minuscolo e clonato su ogni dispositivo, telefono compreso; e
`pass ls` mostrerebbe l'archivio come se fosse una password.

Il determinismo dimostrato qui vale **a parità di regola fiscale**:
`tax_rules.swap_realizes` in `config.py` cambia le cifre per progetto, non per
errore, e `verify_rebuild.py` confronta due database senza sapere sotto quale
regola sono stati calcolati. Se li confronti attraverso un cambio di quella
regola, le differenze sono vere e lo strumento le segnala giustamente. V. la
voce sul «checksum di determinismo» in `doc/TODO.md` e `config.tax_rule_banner()`.
