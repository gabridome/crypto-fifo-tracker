#!/usr/bin/env bash
# Backup cifrato degli INGRESSI della catena FIFO.
#
# Perche' esiste. `import_all_data.py` ricostruisce il database dai soli file in
# `data/`, e `verify_rebuild.py` lo dimostra (doc/rebuild.md). La ricetta e' nel
# repo, al sicuro; gli ingredienti no: `.gitignore` esclude tutta `data/`,
# quindi i 30 file che la catena legge vivono su una sola macchina e in
# `data/backups`, e in nessun altro posto. Il repo e' pubblico: non e' li' che
# possono andare.
#
# Cosa archivia: i file di `data/` che non si rigenerano — gli export CSV, la
# cache dei tassi BCE, la cache dei prezzi, il template del manual entry e i due
# registri di provenienza (`evidence_map.md`, `manual_entries_log.md`). Non il
# database (si ricostruisce in una decina di secondi), non `data/backups`, non
# `data/reports`. Circa 15 MB, 3 MB compressi.
#
# Dentro l'archivio c'e' anche un MANIFEST.txt con lo sha256 di ogni file e
# l'IMPRONTA di determinismo del database (`verify_rebuild.py --fingerprint`).
# Serve al caso che conta: dopo un disastro non hai un database di riferimento
# con cui confrontare la ricostruzione, quindi i numeri attesi devono viaggiare
# con gli ingredienti.
#
# Destinazione consigliata: un server che si usa e si sa mantenere — per esempio
# quello che ospita il proprio `password-store` — in una directory ACCANTO al
# repo di pass e non dentro. Dentro significherebbe aggiungere l'archivio intero
# alla storia git a ogni aggiornamento, in un repo pensato per restare minuscolo
# e clonato su ogni dispositivo; e `pass ls` mostrerebbe l'archivio come se fosse
# una password. V. doc/rebuild.md.
#
# Uso:
#   scripts/backup_data_inputs.sh --dry-run    # crea e verifica, non carica
#   scripts/backup_data_inputs.sh              # crea, verifica e carica
#
# Variabili d'ambiente:
#   GPG_RECIPIENTS  obbligatoria: le chiavi a cui cifrare, separate da spazi
#   BACKUP_DEST     obbligatoria per caricare (non per --dry-run), forma
#                   utente@host:cartella
#   BACKUP_KEEP     6 di default: quante versioni tenere sul server

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Destinazione e destinatari NON stanno nel repo: sono dettagli operativi di chi
# usa lo strumento, e questo repo e' pubblico. Vanno nel proprio ambiente.
DEST="${BACKUP_DEST:-}"
# Validato, non usato a fiducia. Senza questo controllo un BACKUP_KEEP non
# numerico (o vuoto, o 0) rende `$((KEEP+1))` uguale a 1, `tail -n +1` emette
# OGNI riga e la rotazione cancella TUTTI gli archivi remoti — compreso quello
# appena caricato, lasciando zero copie fuori dalla macchina dei soli file che
# non si possono rigenerare. Misurato il 2026-10-19 da una review.
KEEP="${BACKUP_KEEP:-6}"
case "$KEEP" in
  ''|*[!0-9]*) echo "✗ BACKUP_KEEP deve essere un intero ≥ 1 (dato: '$KEEP')" >&2; exit 1;;
esac
[ "$KEEP" -ge 1 ] || { echo "✗ BACKUP_KEEP deve essere ≥ 1 (dato: $KEEP)" >&2; exit 1; }
# Conviene indicare DUE chiavi: una la cui sottochiave di cifratura sta su un
# token (l'archivio si apre solo col token in mano, che e' quel che si vuole da
# un backup) e una la cui parte privata sta sulla macchina. Con entrambe
# l'archivio si apre se manca il token O se manca la macchina; la protezione
# effettiva e' quella della piu' debole, ed e' una scelta deliberata a favore
# della disponibilita', perche' un backup che non si puo' aprire non e' un
# backup.
RECIPIENTS="${GPG_RECIPIENTS:-}"
if [ -z "$RECIPIENTS" ]; then
  echo "✗ GPG_RECIPIENTS non impostata: indica le chiavi a cui cifrare," >&2
  echo "  separate da spazi. Es.: export GPG_RECIPIENTS='KEYID1 KEYID2'" >&2
  exit 1
fi

STAMP="$(date +%Y-%m-%d)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
NAME="crypto-fifo-inputs-$STAMP.tar.gz.gpg"
# Stretto di proposito: questo script carica e cancella. Un argomento non
# riconosciuto non deve valere "esecuzione vera" — `--dryrun` o `-n`, scritti
# per sbaglio, finirebbero per caricare e ruotare.
DRY=0
case "${1:-}" in
  '')         ;;
  --dry-run)  DRY=1 ;;
  *) echo "✗ argomento non riconosciuto: $1" >&2
     echo "  uso: $(basename "$0") [--dry-run]" >&2; exit 1 ;;
esac
[ "$#" -le 1 ] || { echo "✗ troppi argomenti" >&2; exit 1; }

echo "══ Backup degli ingressi della catena FIFO ══"

# ── 1. cosa archiviamo ───────────────────────────────────────────────────────
# Glob e non `ls`: `mapfile` vuole bash >= 4 e /bin/bash su macOS e' la 3.2,
# quindi su un clone senza bash di homebrew lo script moriva con
# "mapfile: command not found".
FILES=()
for f in data/*.csv data/*.md; do [ -e "$f" ] && FILES+=("$f"); done
if [ "${#FILES[@]}" -eq 0 ]; then
  echo "✗ nessun file in data/*.csv — niente da archiviare" >&2; exit 1
fi
SIZE=$(du -ch "${FILES[@]}" | tail -1 | cut -f1)
echo "  ${#FILES[@]} file, $SIZE"

# ── 2. il manifest, con le impronte e le istruzioni di ripristino ────────────
MAN="$WORK/MANIFEST.txt"
{
  echo "Ingressi della catena FIFO crypto — $STAMP"
  echo "prodotto da scripts/backup_data_inputs.sh su $(hostname -s)"
  echo "commit del tracker: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo
  echo "RIPRISTINO"
  echo "  1. git clone <repo del tracker>; ./setup.sh"
  echo "  2. gpg --decrypt $NAME | tar xzf - -C <repo>/   (ricrea data/)"
  echo "  3. python3 import_all_data.py --db data/crypto_fifo.db"
  echo "  4. python3 verify_rebuild.py data/crypto_fifo.db --fingerprint"
  echo "     e confronta con l'IMPRONTA qui sotto: devono coincidere riga per riga"
  echo "     (la prima riga porta il nome del file e puo' differire)"
  echo
  echo "IMPRONTA DEL DATABASE AL MOMENTO DEL BACKUP"
  if [ -f data/crypto_fifo.db ]; then
    venv/bin/python verify_rebuild.py data/crypto_fifo.db --fingerprint 2>&1 | sed 's/^/  /'
  else
    echo "  (nessun database presente: impronta non calcolata)"
  fi
  echo
  echo "SHA256 DEI FILE ARCHIVIATI"
  shasum -a256 "${FILES[@]}" | sed 's/^/  /'
} > "$MAN"
echo "  manifest: $(wc -l < "$MAN") righe"

# ── 3. tar + gpg ─────────────────────────────────────────────────────────────
REC_ARGS=()
for r in $RECIPIENTS; do REC_ARGS+=(--recipient "$r"); done
tar -czf "$WORK/payload.tar.gz" -C "$REPO" "${FILES[@]}" -C "$WORK" MANIFEST.txt
gpg --batch --yes --encrypt --trust-model always "${REC_ARGS[@]}" \
    --output "$WORK/$NAME" "$WORK/payload.tar.gz"
echo "  cifrato: $NAME ($(du -h "$WORK/$NAME" | cut -f1))"

# ── 4. verifica strutturale senza decifrare ──────────────────────────────────
# `--list-packets` non richiede chiavi private: dice a chi e' cifrato. Verificare
# con una decifratura vera richiederebbe il token o la passphrase, cioe' un
# prompt interattivo — che in uno script non si deve avere.
echo "  destinatari nell'archivio:"
PACKET_IDS=$(gpg --list-packets "$WORK/$NAME" 2>/dev/null \
             | grep -oE "keyid [0-9A-F]{16}" | awk '{print $2}' | sort -u)
echo "$PACKET_IDS" | sed 's/^/    /'
N_REC=$(echo "$PACKET_IDS" | grep -c .)
EXPECTED=$(echo "$RECIPIENTS" | wc -w | tr -d ' ')
# `-ne`, non `-lt`: un destinatario IN PIU' e' il caso che conta. Con
# --trust-model always, una riga `encrypt-to` in gpg.conf o l'espansione di un
# gruppo possono aggiungere una chiave che l'operatore non ha chiesto, e un
# controllo "almeno N" direbbe che e' tutto a posto.
if [ "$N_REC" -ne "$EXPECTED" ]; then
  echo "✗ l'archivio e' cifrato a $N_REC destinatari, attesi $EXPECTED." >&2
  echo "  Controlla GPG_RECIPIENTS e eventuali encrypt-to in ~/.gnupg/gpg.conf" >&2
  exit 1
fi
# E i keyid visibili devono appartenere alle chiavi richieste. Alcune
# sottochiavi hanno il keyid soppresso e compaiono come 0000000000000000: quelle
# non si possono verificare cosi', e lo si dice invece di far finta.
WANTED=$(for r in $RECIPIENTS; do
           gpg --with-colons --list-keys "$r" 2>/dev/null \
           | awk -F: '$1=="pub"||$1=="sub"{print toupper($5)}'
         done | sort -u)
HIDDEN=0
for id in $PACKET_IDS; do
  if [ "$id" = "0000000000000000" ]; then HIDDEN=$((HIDDEN+1)); continue; fi
  echo "$WANTED" | grep -qx "$id" || {
    echo "✗ l'archivio e' cifrato a una chiave non richiesta: $id" >&2; exit 1; }
done
[ "$HIDDEN" -gt 0 ] && echo "    ($HIDDEN con keyid soppresso: non verificabile per id)"

if [ "$DRY" = "1" ]; then
  cp "$WORK/$NAME" "/tmp/$NAME"
  echo
  echo "--dry-run: nessun caricamento. Archivio cifrato in /tmp/$NAME"
  echo "  (resta li': cancellalo quando hai finito di guardarlo)"
  echo "Per caricarlo davvero: scripts/backup_data_inputs.sh"
  exit 0
fi

# ── 5. caricamento e rotazione ───────────────────────────────────────────────
if [ -z "$DEST" ]; then
  echo "✗ BACKUP_DEST non impostata: indica dove caricare, nella forma" >&2
  echo "  utente@host:cartella. L'archivio cifrato e' pronto, non e' stato" >&2
  echo "  caricato. Con --dry-run questo passo non serve." >&2
  exit 1
fi
case "$DEST" in
  *:*) ;;
  *) echo "✗ BACKUP_DEST deve avere la forma utente@host:cartella (dato: '$DEST')" >&2
     echo "  senza i due punti lo scp diventa una copia locale." >&2; exit 1 ;;
esac
HOST="${DEST%%:*}"; DIR="${DEST#*:}"
echo "  carico su $DEST"
ssh "$HOST" "mkdir -p '$DIR'"
scp -q "$WORK/$NAME" "$DEST/"
# Verificato prima di cancellare qualunque cosa: la rotazione che segue
# cancella, e cancellare fidandosi di uno scp silenzioso non e' prudente.
LOCAL_SIZE=$(wc -c < "$WORK/$NAME" | tr -d ' ')
# `tail -1` perche' un profilo remoto che stampa su stdout per una ssh non
# interattiva (motd, un .bashrc loquace) corromperebbe il valore e farebbe
# abortire dopo un upload perfettamente riuscito.
REMOTE_SIZE=$(ssh "$HOST" "wc -c < '$DIR/$NAME'" 2>/dev/null | tail -1 | tr -d ' ')
if [ "$LOCAL_SIZE" != "$REMOTE_SIZE" ]; then
  echo "✗ l'archivio remoto misura $REMOTE_SIZE byte, quello locale $LOCAL_SIZE." >&2
  echo "  Non ruoto nulla: le copie vecchie restano." >&2; exit 1
fi
echo "  ✓ caricato e verificato ($LOCAL_SIZE byte)"
echo "  rotazione: tengo le $KEEP versioni piu' recenti"
# L'archivio appena caricato e' escluso per nome: se la rotazione cancellasse
# anche lui, il risultato sarebbe zero copie remote. E niente `xargs -r`, che e'
# una estensione GNU e non esiste su un host BSD/macOS: un ciclo `while read`
# non cancella nulla quando la lista e' vuota.
ssh "$HOST" "cd '$DIR' && ls -1t crypto-fifo-inputs-*.tar.gz.gpg 2>/dev/null \
  | grep -vFx '$NAME' | tail -n +$KEEP | while read -r old; do rm -v -- \"\$old\"; done"
echo "  versioni sul server:"
ssh "$HOST" "cd '$DIR' && ls -1t crypto-fifo-inputs-*.tar.gz.gpg 2>/dev/null | sed 's/^/    /'"
echo
echo "✓ fatto"
