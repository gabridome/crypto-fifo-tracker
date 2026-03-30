# Code Guidelines

> Regole universali di sviluppo. Vincolanti per tutti i progetti.
> Per regole specifiche del progetto, vedere `doc/project_guidelines.md`.
> Ultima revisione: 2026-03-30.

---

## 1. Integrità dei dati

### 1.1 Atomicità delle operazioni DB

Ogni operazione che modifica il database deve essere atomica quando la prudenza lo suggerisce.
In particolare, ogni pattern delete+insert **deve** essere wrappato in una singola transazione:

```python
# CORRETTO
conn.execute("BEGIN")
# ... tutte le operazioni di scrittura ...
conn.commit()

# SBAGLIATO — crash fra DELETE e COMMIT = dati persi
conn.execute("DELETE ...")
conn.commit()  # <-- il DELETE è già permanente
# ... INSERT che potrebbe non completarsi ...
conn.commit()
```

**Regola**: se un'operazione ha più di un statement di scrittura, usare `BEGIN` esplicito
e un singolo `COMMIT` alla fine, con `ROLLBACK` nel path di errore.

### 1.2 Precisione numerica

Scegliere la precisione adeguata al dominio. Per valori dove l'errore cumulativo
conta (finanza, misure scientifiche, conteggi fiscali), preferire `Decimal` a `float`:

```python
from decimal import Decimal, ROUND_HALF_UP

# Somme cumulative su molti record
total = sum(Decimal(str(row['value'])) for row in rows)
rounded = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

**Mai** confrontare float con `==`. Usare tolerance o `round()`.
Arrotondare esplicitamente prima di ogni INSERT in DB.

> **Dettagli specifici** (quali campi, quanti decimali, eccezioni) → `doc/project_guidelines.md`

### 1.3 Connessioni DB: lifecycle management

Ogni connessione deve essere chiusa anche in caso di errore:

```python
conn = sqlite3.connect(DB_PATH)
try:
    conn.execute("BEGIN")
    # ... operazioni ...
    conn.commit()
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
```

**Mai** lasciare una connessione aperta senza un `finally: conn.close()`.
Nelle web app Flask, usare il pattern `g` con `teardown_appcontext`.

---

## 2. Gestione errori

### 2.1 Mai silenziare le eccezioni

Ogni blocco `except` deve fare almeno una di queste cose:
1. **Loggare** l'errore (con `logging` o almeno `print`)
2. **Re-raise** l'eccezione
3. **Restituire un errore significativo** all'utente

```python
# VIETATO
except Exception:
    pass

# VIETATO
except:
    return 0

# CORRETTO
except (ValueError, TypeError) as e:
    logging.warning(f"Parsing fallito per riga {i}: {e}")
    parse_errors += 1
```

**Regola**: `except Exception: pass` non è mai accettabile. Se pensi che un errore
sia "impossibile", loggalo comunque — gli errori impossibili sono quelli che ti
fanno perdere un'intera giornata di debug.

### 2.2 Eccezioni specifiche

Catturare sempre il tipo più specifico possibile:

```python
# CORRETTO
except (ValueError, TypeError) as e:

# SBAGLIATO
except:          # cattura anche KeyboardInterrupt, SystemExit
except Exception: # troppo ampio se il tipo è prevedibile
```

---

## 3. Sicurezza

### 3.1 Path safety

Ogni route o funzione che accetta un nome file dall'esterno deve validare che il path
risultante resti all'interno della directory prevista:

```python
def safe_path(base_dir: str, filename: str) -> str:
    """Restituisce il path sicuro o solleva ValueError."""
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name.startswith('.'):
        raise ValueError(f"Filename non valido: {filename}")
    full_path = os.path.join(base_dir, safe_name)
    if not os.path.realpath(full_path).startswith(os.path.realpath(base_dir)):
        raise ValueError(f"Path traversal attempt: {filename}")
    return full_path
```

Se il progetto usa Flask con Werkzeug, preferire `werkzeug.utils.secure_filename`.
Applicare a: upload, delete, download, qualsiasi route con `<filename>`.

### 3.2 SQL parametrizzato

Tutte le query SQL devono usare placeholder (`?` per SQLite, `%s` per PostgreSQL).
Nessuna interpolazione di stringhe, f-string, o `.format()` nelle query.

### 3.3 Credenziali

Mai loggare, mostrare o committare: API key, token, password, secret key.
Le credenziali vanno in `.env` o variabili d'ambiente, mai nel codice.

### 3.4 Validazione input

I dati provenienti dall'esterno (form web, API, CSV, file upload) devono essere
validati ai confini del sistema prima di raggiungere il DB o la logica applicativa.

> **Regole di validazione specifiche** (campi, formati, constraint) → `doc/project_guidelines.md`

---

## 4. Architettura

### 4.1 Path assoluti nei default

I path di configurazione devono essere assoluti, derivati da `__file__`:

```python
# CORRETTO
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "mydb.db")

# SBAGLIATO
DB_PATH = "data/mydb.db"  # relativo al CWD, fragile
```

### 4.2 Nessun side effect a import time

`import config` non deve mai fallire o eseguire operazioni irreversibili.
Calcoli che possono fallire (es. validazione di variabili d'ambiente) devono
essere differiti al primo accesso o wrappati in try/except.

### 4.3 No duplicazione codice

Ogni logica significativa deve esistere in un solo posto:
- Lo schema DB è definito in un unico file sorgente — test e setup lo leggono da lì
- Le costanti e i mapping sono definiti una volta e importati
- Pattern ripetuti devono essere estratti in utility condivise

Quando trovi codice duplicato, valuta se estrarre una funzione condivisa.
Tre copie identiche sono un bug in attesa.

### 4.4 Migrazioni DB idempotenti

Le migrazioni devono essere safe da rieseguire:
- `CREATE TABLE IF NOT EXISTS`
- Verificare l'esistenza di colonne prima di `ALTER TABLE`
- Mai `DROP` senza backup esplicito

---

## 5. Testing

### 5.1 Testare il codice reale

I test devono importare e chiamare il codice di produzione, non reimplementazioni
embedded nel file di test. Se il test contiene la sua versione di una funzione,
quel test non protegge da regressioni nel codice reale.

### 5.2 Test-Driven Development (TDD)

Per nuove funzionalità e bug fix, seguire il ciclo **Red-Green-Refactor**:

1. **Red**: scrivere un test che fallisce per il comportamento desiderato
2. **Green**: scrivere il codice minimo per far passare il test
3. **Refactor**: migliorare il codice mantenendo i test verdi

Ogni step di verifica è obbligatorio — il test deve essere eseguito e il risultato
osservato prima di procedere. Per i bug fix: **sempre** partire da un test che
riproduce il bug.

### 5.3 Anti-pattern da evitare nei test

- **Testare i mock, non il codice**: mai asserire su elementi mockati. Testare il comportamento reale.
- **Metodi test-only nel codice di produzione**: helper e cleanup vanno nelle test utilities.
- **Mock eccessivi**: se il setup del mock è più lungo della logica del test, considerare un test di integrazione.
- **Mock incompleti**: mockare la struttura dati completa, non solo i campi usati dal test.
- **Test come afterthought**: i test sono parte dell'implementazione, non un follow-up opzionale.

### 5.4 Pianificare i test in fase di planning

Ogni task di sviluppo deve includere nella pianificazione:
- Quali test esistenti vanno eseguiti dopo la modifica
- Se servono nuovi test
- Quali edge case coprire

### 5.5 Eseguire e documentare i test

Al termine dello sviluppo:
1. Eseguire tutti i test rilevanti
2. Documentare l'esito nella PR o nel commit message
3. Se un test fallisce, **non** ignorarlo — correggerlo o spiegare perché

### 5.6 Test di esistenza

Verificare che i file e le risorse critiche esistano (DB, config, schema).
Un test che fallisce subito con "file not found" è meglio di un errore criptico
a runtime.

---

## 6. Debugging sistematico

Quando si incontra un bug, **mai** procedere a tentativi. Seguire questo processo:

### 6.1 Investigare la causa radice

1. Leggere il messaggio di errore **completamente** (stack trace, line number, codice errore)
2. Riprodurre il problema in modo consistente prima di investigare
3. Controllare i cambiamenti recenti (`git diff`, nuove dipendenze, config)
4. Tracciare il flusso dei dati all'indietro dal sintomo alla sorgente

### 6.2 Una ipotesi alla volta

1. Formulare una singola ipotesi specifica e scriverla
2. Testare con il cambiamento più piccolo possibile
3. Se non funziona, formulare una **nuova** ipotesi — non accumulare fix
4. Se 3+ tentativi falliscono: **fermarsi**. Probabilmente è un problema architetturale,
   non un bug. Discutere prima di continuare.

### 6.3 Implementare il fix

1. Creare un test che riproduce il bug (deve fallire)
2. Implementare un singolo fix per la causa radice
3. Verificare che il test passa
4. Nessun miglioramento "already here" — solo il fix

**Red flag**: "fix veloce per ora", "provo a cambiare X", cambiamenti multipli
contemporanei, proporre soluzioni prima di tracciare il flusso dati.

---

## 7. Verifica prima di dichiarare completato

Nessuna affermazione di completamento senza **evidenza fresca di verifica**.

| Affermazione | Richiede |
|---|---|
| "I test passano" | Output del comando test con 0 failure |
| "Il build funziona" | Comando/avvio con exit code 0 |
| "Il bug è risolto" | Test del sintomo originale che passa |
| "I requisiti sono soddisfatti" | Checklist punto per punto contro la specifica |

**Mai** usare "dovrebbe funzionare", "probabilmente", "sembra ok".
**Mai** esprimere soddisfazione ("Fatto!", "Perfetto!") prima della verifica.
**Mai** fidarsi del report di successo di un agent senza verifica indipendente.

Linter ok ≠ build ok. Build ok ≠ test ok. Test ok ≠ requisiti soddisfatti.

---

## 8. Workflow di sviluppo

### 8.1 Branch prima di sviluppare

Prima di iniziare qualsiasi sviluppo non banale, valutare se creare un branch:

```bash
git checkout -b feature/descrizione-breve
```

Il branch protegge main, permette review, e rende facile tornare indietro.

### 8.2 Analisi statica (LINT)

Al termine dello sviluppo, eseguire un'analisi statica del codice.
Tool consigliati: `ruff` (fast, all-in-one per Python), `flake8`, `mypy` per type checking.

```bash
ruff check .
ruff format --check .
```

Risolvere i problemi critici prima di committare.

### 8.3 Aggiornare la documentazione

La documentazione (CLAUDE.md, README, guide) deve essere aggiornata:
- **In fase di pianificazione**: annotare cosa si intende fare e perché
- **Durante lo sviluppo**: se la sessione potrebbe interrompersi, salvare lo stato
- **Al termine**: aggiornare CLAUDE.md con nuove funzionalità, schema, route, etc.

La documentazione è la rete di sicurezza per la perdita di contesto.
Se perdi la sessione, CLAUDE.md e le guidelines devono bastare per riprendere.

### 8.4 Commit frequenti

Committare spesso. Il commit è la protezione principale del codice.
Un commit ogni task completato, non un mega-commit a fine giornata.

### 8.5 Pianificazione prima dell'implementazione

Per task non banali, progettare prima di implementare:
- Proporre 2-3 approcci con trade-off e raccomandazione
- Decomporre in step piccoli (2-5 minuti ciascuno)
- Nessun placeholder: ogni step deve contenere il codice/comandi effettivi
- Applicare YAGNI: rimuovere funzionalità non necessarie dal design
- In codebase esistenti, seguire i pattern stabiliti — non ristrutturare unilateralmente

---

## 9. Uso di Superpowers (plugin Claude Code)

Il plugin **superpowers** (`superpowers@superpowers-marketplace`) fornisce skill
strutturate per lo sviluppo. Le skill sono vincolanti quando applicabili.

### 9.1 Quando usare le skill

Se una skill potrebbe applicarsi al task corrente, **deve** essere invocata prima
di qualsiasi risposta o azione. Le skill determinano il *come*, non il *cosa*.

| Situazione | Skill da invocare |
|---|---|
| Nuova feature o design | `brainstorming` → poi `write-plan` |
| Bug da risolvere | `systematic-debugging` |
| Implementazione da piano | `executing-plans` o `subagent-driven-development` |
| Code review richiesta | `requesting-code-review` |
| Fine sviluppo su branch | `finishing-a-development-branch` |
| Verifica completamento | `verification-before-completion` |
| Nuovo codice | `test-driven-development` |

### 9.2 Ordine di priorità

1. **Skill di processo** prima (brainstorming, debugging) — determinano l'approccio
2. **Skill di implementazione** dopo (TDD, executing-plans) — guidano l'esecuzione

### 9.3 Regole dalle skill incorporate

Le sezioni 5 (Testing/TDD), 6 (Debugging) e 7 (Verifica) di queste guidelines
incorporano le regole delle skill superpowers corrispondenti.
Le skill vanno comunque invocate — forniscono workflow dettagliati oltre le regole base.

### 9.4 Istruzioni utente prevalenti

Le istruzioni in CLAUDE.md, in queste guidelines e in `doc/project_guidelines.md`
hanno **priorità superiore** alle skill superpowers.

---

## 10. Checklist pre-commit

Prima di ogni commit, verificare:

- [ ] Nessun `except: pass` o `except Exception: pass` introdotto
- [ ] Operazioni DB in transazione atomica
- [ ] Path da input esterno validati
- [ ] Precisione numerica adeguata (vedi `doc/project_guidelines.md`)
- [ ] Connessioni DB chiuse in `finally`
- [ ] Nessuna credenziale nel codice
- [ ] Test rilevanti eseguiti e passati — con evidenza (output)
- [ ] Verifiche di completamento fatte (non "dovrebbe funzionare")
- [ ] Documentazione aggiornata se necessario
- [ ] Nessun path relativo hardcodato in nuovi file
- [ ] Skill superpowers invocate dove applicabili

---

## Appendice: regole specifiche del progetto

Ogni progetto ha un file `doc/project_guidelines.md` con regole aggiuntive:
- Precisione numerica specifica (quali campi, quanti decimali, eccezioni)
- Regole di validazione input (campi, formati, constraint di dominio)
- Convenzioni di naming e stile specifiche
- Pattern architetturali del progetto
- Comandi test specifici
- Procedure di deploy
