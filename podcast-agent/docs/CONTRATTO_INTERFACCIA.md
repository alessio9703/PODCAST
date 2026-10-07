# Contratto d'interfaccia · podcast-agent per l'adattatore web

> Documento in costruzione: si completa fase per fase (vedi `docs/avanzamento/`).
> Coperti finora: contratto generale (C0) e passi 1–4 + Confronto (C1).
> Le azioni dei passi 5–7, Archivio e Librerie arrivano con le fasi C2–C4; la
> versione completa con la sezione "a carico dell'adattatore" chiude la C5.

## 1. Come si chiama un comando

Ogni azione è un comando `tools/<nome>.py` lanciato come sottoprocesso:

```
<progetto>/.venv/bin/python tools/<nome>.py [argomenti] [--config PERCORSO]
```

- **stdout**: sempre **un solo oggetto JSON**, sia in caso di successo sia di
  errore. Va letto per intero solo a processo terminato.
- **stderr**: messaggi per l'umano (avanzamento, avvisi). Non va interpretato:
  si può mostrare in un pannello "dettagli" o registrare nei log.
- **exit code**: uno di quattro valori, sempre coerente con il JSON.
- Nessun comando chiede input da tastiera: tutto passa dagli argomenti.
- `--config` è accettato da tutti i comandi (default: `config.yaml` del
  progetto).

## 2. I quattro esiti

| exit | `ok` | Significato | Cosa fa la UI (vedi `Stati.dc.html`) |
|---|---|---|---|
| 0 | `true` | Tutto a posto | Conferma breve, si prosegue |
| 1 | `false` | Errore correggibile dall'utente | Dice cosa correggere, accanto al campo che l'ha causato |
| 2 | `false` | Errore del provider esterno (voce/sintesi) | **Blocco, nessun "Riprova"** automatico: una clonazione ripetuta creerebbe voci duplicate a pagamento |
| 3 | `false` | Serve una scelta dell'utente | Mostra la domanda e le opzioni (da `details`), poi richiama il comando con la risposta |

### Forma del JSON di errore

```json
{
  "ok": false,
  "error_type": "AmbiguousName",
  "error": "messaggio leggibile, in italiano",
  "exit_code": 3,
  "details": { "...": "solo se l'errore porta dati strutturati" }
}
```

- `error_type` è un **nome stabile**: la UI decide cosa mostrare da questo,
  mai dal testo di `error`, che può cambiare.
- `details` è **opzionale**: c'è solo per gli errori che hanno dati da
  mostrare. Un errore senza `details` si mostra con il solo testo di `error`.

## 3. `error_type` con significato fisso

L'elenco cresce fase per fase; quelli qui sotto sono già in vigore.

### `Interrupted` · exit 1

Il comando è stato fermato da SIGTERM (il modo con cui l'adattatore ferma un
sottoprocesso) o da Ctrl-C prima di finire. Il JSON viene scritto comunque.

```json
{
  "ok": false,
  "error_type": "Interrupted",
  "error": "Interrotto prima della fine. Nessun file e' rimasto scritto a meta': il comando si puo' rilanciare.",
  "exit_code": 1
}
```

- Nessun `details`.
- Tutte le scritture su disco sono atomiche, quindi un'interruzione non lascia
  file corrotti: il comando si può rilanciare così com'è.
- In precedenza un'interruzione usciva con codice 130 senza JSON: questo non
  succede più.
- Il comando "produci" (fase C2) avrà una propria interruzione ordinata, con un
  `error_type` dedicato e il punto raggiunto in `details`.

### `AmbiguousName` · exit 3

Un nome scritto dall'utente corrisponde a più elementi: il comando **non
sceglie mai al posto dell'utente**. Oggi lo usano `music_list.py --resolve` e
`music_set.py`.

```json
{
  "ok": false,
  "error_type": "AmbiguousName",
  "error": "'piano' non e' un nome certo in intro/: candidati Piano calmo, Piano forte.",
  "exit_code": 3,
  "details": {
    "category": "intro",
    "query": "piano",
    "candidates": ["Piano calmo", "Piano forte"]
  }
}
```

| Campo di `details` | Tipo | Significato |
|---|---|---|
| `category` | stringa | Dove si è cercato (per le musiche: `intro`, `outro`, `sottofondo`) |
| `query` | stringa | Il testo cercato, così come è arrivato |
| `candidates` | lista di stringhe | I nomi **esatti** fra cui scegliere |

La UI mostra i candidati come opzioni e richiama lo stesso comando passando il
candidato scelto così com'è: viene riconosciuto come nome esatto e si ottiene
exit 0. Unica eccezione: due file i cui nomi differiscono solo per maiuscole,
accenti o trattini restano ambigui anche così, e vanno rinominati nella
libreria.

Un nome che non corrisponde a niente **non** è un `AmbiguousName`: è exit 1
senza `details`, con l'elenco dei disponibili nel testo.

### Introdotti in C1

| error_type | exit | Quando | `details` |
|---|---|---|---|
| `DocumentNotExtractable` | 1 | Documento senza testo ricavabile | `motivo` (`scansione`, `protetto`, `vuoto`, `illeggibile`), `file` |
| `UnsupportedDocument` | 1 | Estensione non gestita | `estensione`, `ammesse` |
| `SpokenNameIncomplete` | 3 | Nome da pronunciare senza cognome | `campo`, `ruolo`, `nome` |
| `EpisodeConfigError` | 1 | Scelta non valida nei passi 1–2 | `campo` (+ `ruolo`, `ammessi`, ...) |
| `AmbiguousName` | 3 | Anche per le voci in `episode_config.py` | `campo: "voce"`, `ruolo`, `query`, `candidates` |
| `ScriptInvalid` | 1 | Una modifica renderebbe il copione non valido: non salvata | `errors` |
| `ScriptHasErrors` | 1 | Approvare una versione con errori bloccanti | `version`, `errors` |
| `VersionConflict` | 1 | Il copione è cambiato rispetto alla versione che l'utente guardava | `expected_version`, `current_version` |
| `NothingToChange` | 1 | La modifica lascerebbe tutto com'è: nessuna versione creata | `turn` o `version` |
| `TurnNotFound` | 1 | Numero di battuta inesistente | `turn`, `turn_count` |
| `RunBusy` | 1 | Sulla run è già in corso un'operazione lunga | `in_corso: {operazione, dal, pid}` |

`VersionConflict` è il caso "due persone sullo stesso copione": la UI ricarica il
copione e mostra la versione nuova, non riprova da sola.

### Errori dell'API del modello (scrittura e revisione del copione)

| error_type | exit | Quando | `details` |
|---|---|---|---|
| `ModelAuthError` | **2** | Chiave Anthropic non valida o senza permessi (HTTP 401/403) | `status` |
| `ModelCreditExhausted` | **2** | Credito dell'account esaurito | `status` |
| `ModelRateLimited` | 1 | Troppe richieste (HTTP 429) | `status`, `retry_after_seconds` (null se il servizio non lo indica) |
| `ModelOverloaded` | 1 | Servizio sovraccarico o in errore (HTTP 5xx, 529) | `status`, `retry_after_seconds` |
| `ModelUnreachable` | 1 | Rete assente o timeout | `status: null`, `retry_after_seconds: null` |
| `ScriptWriterError` | 1 | Altro: rifiuto del modello, risposta non valida, altri errori HTTP | — |

- Exit 2 vale come per il provider vocale: blocco, nessun "Riprova", si
  interviene su account o configurazione.
- Gli errori con exit 1 sono passeggeri: la UI può proporre di riprovare,
  dopo `retry_after_seconds` se c'è.
- **Euristica:** il credito esaurito non ha un codice dedicato. L'API risponde
  400 con un messaggio sul saldo crediti, e lo si riconosce dal testo.
- Nessuno di questi errori crea versioni del copione, e il lock di lavoro
  viene rilasciato.
- I messaggi non contengono mai intestazioni HTTP né la chiave.

### `LockTimeout` · exit 1

Un file condiviso (per esempio il registro delle voci) è rimasto in uso da
un'altra operazione per più di 30 secondi. In `details`: `file` e
`timeout_seconds`. Di norma non succede: le operazioni protette durano
millisecondi. Se compare, la UI può proporre di riprovare.

### Errori già esistenti, invariati

| error_type | exit | Quando |
|---|---|---|
| `needs_user_input` | 3 | Esiti 3 storici non ancora strutturati (es. `lessons.py --accept` quando serve togliere una lezione) |
| `MusicChangedError` | 3 | Un brano già usato è stato sostituito: serve `--accept-changed-music` |
| nome della classe del provider (`ProviderQuotaError`, `ProviderAuthError`, ...) | 2 | Errore del servizio vocale |
| nome della classe di dominio (`ValueError`, `ScriptWriterError`, ...) | 1 | Errore correggibile; il testo dice cosa fare |

## 4. Concorrenza

Più comandi possono girare in parallelo, anche per utenti diversi:

- i file condivisi (`voices_registry.json`, e dalle prossime fasi i metadati
  di voci e musiche) si modificano sotto lock fra processi, rileggendo il
  contenuto dentro il lock: due clonazioni simultanee non si cancellano a
  vicenda;
- tutte le scritture sono atomiche: chi legge vede la versione vecchia o
  quella nuova, mai una a metà.

Accanto ai file condivisi compaiono file `*.lock`: sono normali, non vanno
cancellati mentre un comando è in esecuzione (sono esclusi da git).


---

# Azioni per schermata

Convenzioni delle tabelle: **Lunga** = può durare più di qualche secondo (la UI
mostra uno stato di attesa); **Costa** = chiama un servizio a pagamento. Negli
esempi `output/<run>` sta per il percorso assoluto della cartella dell'episodio
e le liste lunghe sono accorciate con `"…"`.

## Passo 1 · Documento

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Carica un file | *adattatore*: salva il file in `documenti/` (vedi fondo) | — | — |
| Estrai e mostra pagine/parole | `doc_extract.py NOME [--preview-chars N]` | no | no |
| Crea la bozza con titolo e documento | `run_init.py --title T --document NOME` | no | no |
| Cambia titolo / sostituisci documento | `episode_config.py --run-dir R --titolo T` / `--documento NOME` | no | no |

**`doc_extract.py`**: chiavi `name`, `kind` (`pdf`/`docx`/`text`), `pages`
(null per DOCX e testo), `words`, `chars`, `size_bytes`, `sha256`, `preview`,
`extractable: true`. Una scansione dà exit 1 con
`error_type: DocumentNotExtractable`, `details.motivo: "scansione"`: è il caso
"Questo PDF non contiene testo" di `Stati.dc.html`.

**`run_init.py --document`** controlla il documento **prima** di creare la
cartella: una scansione non lascia bozze vuote in Archivio. Restituisce
`run_dir` e `episodio` (la stessa vista di `episode_config.py`, sotto).

## Passo 2 · Cast e formato

Un solo comando per leggere e modificare: `episode_config.py --run-dir R`.
Senza altri argomenti legge e basta. Le modifiche passate in una chiamata si
applicano **tutte o nessuna**.

| Azione | Argomenti |
|---|---|
| Formato | `--formato conversazione\|intervista` (le voci restano assegnate: `voce_a`↔`intervistatore`, `voce_b`↔`ospite`) |
| Durata indicativa | `--durata H:MM` oppure `--minuti N` (1–180) |
| Assegna una voce a un ruolo | `--voce RUOLO=NOME` (nome dalla libreria Voci; ruoli: `presentatore`, `intervistatore`, `ospite`, `voce_a`, `voce_b`) |
| Nome e cognome da pronunciare | `--nome RUOLO="Nome Cognome"`; senza cognome → exit 3 `SpokenNameIncomplete`; conferma esplicita con `--accetta-nome-incompleto` |
| Ruolo (chi è, di cosa si occupa) | `--descrizione RUOLO="..."` |
| Libera un ruolo | `--togli RUOLO` |

Esempio reale (cast completo, con un'intro scelta al passo 3):

```json
{
  "ok": true,
  "run_dir": "output/<run>",
  "episodio_file": "output/<run>/episodio.json",
  "titolo": "Il trimestre",
  "documento": {"nome": "report.txt", "percorso": "…/documenti/report.txt", "sha256": "sha256:1d61…4bab",
                "tipo": "text", "pagine": null, "parole": 10, "caratteri": 59},
  "formato": "intervista",
  "durata_minuti": 15,
  "ruoli": [
    {"ruolo": "presentatore", "etichetta": "Presentatore", "voce": "Anna Neri", "nome_pronunciato": "Anna Neri",
     "descrizione": null, "voce_presente": true, "clonata": false,
     "nome_proposto": "Anna Neri", "nome_proposto_completo": true},
    {"ruolo": "intervistatore", "etichetta": "Intervistatore", "voce": "Mario Rossi", "nome_pronunciato": "Mario Rossi",
     "descrizione": "giornalista economico", "voce_presente": true, "clonata": false,
     "nome_proposto": "Mario Rossi", "nome_proposto_completo": true},
    "…"
  ],
  "speakers": ["Anna Neri", "Mario Rossi", "Giulia Bianchi"],
  "presenter": "Anna Neri",
  "interviewer": "Mario Rossi",
  "participants": [{"speaker": "Mario Rossi", "nome": "Mario Rossi", "ruolo": "giornalista economico"}, "…"],
  "mancanti": [],
  "cast_completo": true,
  "durata_indicativa": {"parlato_seconds": 900, "music_intro_seconds": 0.5, "music_intro_gap_seconds": 0.8,
                        "music_extra_seconds": 1.3, "totale_seconds": 901.3},
  "warnings": []
}
```

- `ruoli[]` elenca sempre i ruoli del formato, anche vuoti, nell'ordine dello
  schermo. `clonata` è `true`/`false` ("Già clonata" / "Da clonare"),
  `null` se il ruolo non ha voce. Il consenso arriva in C2.
- `nome_proposto` è solo un suggerimento ricavato dal nome del file
  (`nome_proposto_completo: false` = manca nome o cognome): **non viene mai
  salvato da solo**.
- `mancanti[]` dice cosa manca, in forma `{"campo", "ruolo"?}`, per evidenziare
  i campi. `cast_completo` diventa `true` quando formato, durata e ruoli sono
  pronti.
- `warnings[]` avvisa, ad esempio, se esiste già un copione scritto con un cast
  diverso.
- `approval_revoked`: se il copione era approvato e cambiano formato, voci,
  nomi pronunciati o descrizioni dei ruoli, l'approvazione viene ritirata e
  qui c'è quella ritirata (`{version, sha256, at, by}`); altrimenti `null`.
  Titolo e durata non la toccano.

| exit | Quando |
|---|---|
| 0 | letto o salvato |
| 1 | `EpisodeConfigError` (`details.campo`), voce inesistente (`VoiceLibraryError`), `DocumentNotExtractable` |
| 3 | `AmbiguousName` (voce scritta in modo incerto), `SpokenNameIncomplete` |

## Passo 3 · Musiche (parte già disponibile)

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Elenco brani per sezione | `music_list.py` | no | no |
| Scegli / togli un brano | `music_set.py --run-dir R --intro NOME` / `--none intro` | no | no |
| Salta il passo (nessuna musica) | `music_set.py --run-dir R --none intro outro sottofondo` | no | no |
| Durata totale indicativa | `episode_config.py --run-dir R` → `durata_indicativa` | no | no |

Le durate dei brani mp3/m4a ora si misurano anche senza `ffprobe` di sistema.
"Usato in N episodi", la licenza e la trascrizione arrivano con la libreria
Musiche (C4).

## Passo 4 · Copione

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Scrittura automatica (entrando nel passo) | `script_write.py --run-dir R [--by U]` | **sì** | **sì** (API Anthropic) |
| Mostra il copione / stato "Sto scrivendo…" | `script_show.py --run-dir R` | no | no |
| Modifica la battuta | `script_edit.py --run-dir R --turn N --text "..." --expected-version V [--by U]` | no | no |
| Chiedi una modifica (a parole) | `script_revise.py --run-dir R --instructions "..." [--by U]` | **sì** | **sì** |
| Approva questa versione | `script_approve.py --run-dir R --version V --by U` | no | no |
| Annulla approvazione | `script_approve.py --run-dir R --revoke --by U` | no | no |
| Versioni | `script_versions.py --run-dir R --list` | no | no |

**Scrittura e revisione** tengono il lock di lavoro della run per tutta la
chiamata. Mentre girano, `script_show.py` restituisce
`in_corso: {"operazione": "scrittura_copione" | "revisione_copione", "dal", "pid"}`,
e una seconda operazione lunga sulla stessa run esce con `RunBusy`. Con
`has_script: false` e `in_corso` valorizzato la UI mostra lo stato
"Sto scrivendo il copione…".

Il copione prodotto dall'API si valida con le stesse regole del subagent. Se
ha errori bloccanti (es. il presentatore non nomina un ospite) viene salvato
comunque, perché la chiamata è già pagata, ma con `errors` non vuoto e
`approvable: false`: la UI lo mostra da correggere ("Modifica la battuta" o
"Chiedi una modifica"), e `script_approve.py` lo rifiuta (`ScriptHasErrors`).

**JSON del copione**: comune a `script_show`, `script_write`, `script_revise`,
`script_edit` e `script_versions --restore`. Esempio reale da `script_show.py`:

```json
{
  "ok": true,
  "run_dir": "output/<run>",
  "has_script": true,
  "in_corso": null,
  "titolo": "Il trimestre in tre minuti",
  "turni": [{"speaker": "Anna Neri", "testo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, …"}, "…"],
  "speakers": ["Anna Neri", "Mario Rossi", "Giulia Bianchi"],
  "format": "intervista", "interviewer": "Mario Rossi", "presenter": "Anna Neri",
  "participants": [{"speaker": "Mario Rossi", "nome": "Mario Rossi", "ruolo": "giornalista economico"}, "…"],
  "turn_count": 5, "chars": 336, "words": 50, "spoken_words": 47,
  "duration_hint_seconds": 19,
  "version": 7, "revision_count": 5,
  "warnings": [], "errors": [],
  "approved": false, "approved_version": null, "approved_at": null, "approved_by": null,
  "approval_problem": "Il copione non e' stato approvato dall'utente. …",
  "approvable": true,
  "script_file": "output/<run>/conversazione.json",
  "session_file": "output/<run>/_script_session.json",
  "rendered": "TITOLO: Il trimestre in tre minuti\n\n[01] Anna Neri: …",
  "cast": {
    "Anna Neri":   {"ruolo": "presentatore", "presentatore": true, "nome_pronunciato": "Anna Neri", "descrizione": null},
    "Mario Rossi": {"ruolo": "intervistatore", "presentatore": false, "nome_pronunciato": "Mario Rossi", "descrizione": "giornalista economico"},
    "…": "…"
  },
  "turni_strutturati": [
    {"n": 1, "speaker": "Anna Neri", "ruolo": "presentatore", "presentatore": true, "nome_pronunciato": "Anna Neri",
     "testo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico, e Giulia Bianchi, analista finanziaria.",
     "testo_parlato": "Benvenuti. Oggi con noi Mario Rossi, giornalista economico, e Giulia Bianchi, analista finanziaria.",
     "tag": ["[caldo]"],
     "parti": [{"tipo": "tag", "testo": "[caldo]", "inizio": 0, "fine": 7},
               {"tipo": "testo", "testo": " Benvenuti. Oggi con noi …", "inizio": 7, "fine": 107}]},
    "…"
  ],
  "versions": [
    {"version": 1, "at": "2026-10-06T17:26:22", "instruction": null, "kind": "prima_stesura", "by": "Utente A",
     "turn_count": 5, "errors": ["La presentazione non nomina 'Giulia Bianchi' …"], "restored_from": null,
     "current": false, "approved": false, "approved_but_outdated": false},
    "…"
  ]
}
```

- **Ruoli**: `ruolo` vale `presentatore`, `intervistatore`, `ospite` o `voce`
  (in conversazione). Il badge Presentatore/Partecipanti si decide da
  `presentatore` (vero/falso).
- **Tag**: `parti[]` copre l'intero `testo` senza buchi, con offset in
  caratteri. La UI evidenzia le parti `tag` senza rifare il parsing.
  `testo_parlato` è il testo senza tag.
- **Intestazione "[n] battute · ~[mm] min"**: `duration_hint_seconds` è una
  stima grezza (parole pronunciate / parole al minuto); quella vera è al passo 5.
- **Approvazione**: `approved` è vero solo se l'approvazione vale sul testo
  corrente. Dopo una modifica diventa `false` da solo, e `approval_problem`
  dice perché.
- **Versioni**: `kind` vale `prima_stesura`, `riscrittura`, `revisione`,
  `modifica_manuale` o `ripristino`; `instruction` è la richiesta che ha
  generato la versione (es. "modifica manuale battuta 4").
  `approved_but_outdated` marca la versione approvata in passato quando
  l'approvazione è decaduta.

**`script_edit.py`** sostituisce testo e tag di una battuta; lo speaker resta
quello. L'istruzione registrata è "modifica manuale battuta N". Chiavi in più:
`edited_turn`. Se la modifica rompe una regola bloccante non salva niente:

```json
{
  "ok": false,
  "error_type": "ScriptInvalid",
  "error": "La modifica non e' stata salvata perche' renderebbe il copione non valido:\n  - La presentazione non nomina 'Mario Rossi' …",
  "exit_code": 1,
  "details": {"errors": ["La presentazione non nomina 'Mario Rossi' per nome e cognome (…)", "…"]}
}
```

**`script_approve.py --version V --by U`**:

```json
{"ok": true, "approved": true, "version": 7, "sha256": "sha256:3512…cc35",
 "at": "2026-10-06T17:31:27", "by": "Utente A", "session_file": "output/<run>/_script_session.json"}
```

**`--revoke --by U`** restituisce l'approvazione ritirata (`null` se non ce
n'era una):

```json
{"ok": true, "approved": false,
 "revoked": {"version": 7, "sha256": "sha256:3512…cc35", "at": "2026-10-06T17:31:27", "by": "Utente A"},
 "revoked_by": "Utente A", "current_version": 7, "session_file": "output/<run>/_script_session.json"}
```

| exit | Comandi del passo 4 |
|---|---|
| 0 | fatto (per `script_write`/`script_revise` anche con `errors` non vuoto: la versione è salvata) |
| 1 | `ScriptInvalid`, `ScriptHasErrors`, `VersionConflict`, `NothingToChange`, `TurnNotFound`, `RunBusy`, `ScriptWriterError` (API: rifiuto, risposta non valida, rete), cast incompleto (prima di qualunque chiamata a pagamento) |
| 3 | — |

## Pannello Confronto versioni

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Confronta con vN | `script_versions.py --run-dir R --diff N [M] [--only-changes]` (senza M: con la versione corrente) | no | no |
| Tutto il copione / Solo modifiche | senza / con `--only-changes` | no | no |
| Mostra una versione | `script_versions.py --run-dir R --show N` | no | no |
| Ripristina vN | `script_versions.py --run-dir R --restore N --expected-version V --by U` | no | no |

Esempio reale (`--diff 1 3 --only-changes`):

```json
{
  "ok": true,
  "from": {"version": 1, "at": "2026-10-06T17:31:00", "instruction": null, "kind": "prima_stesura", "by": "Utente A"},
  "to": {"version": 3, "at": "2026-10-06T17:31:06", "instruction": "modifica manuale battuta 4",
         "kind": "modifica_manuale", "by": null, "current": false},
  "only_changes": true,
  "titolo_prima": "Il trimestre in tre minuti", "titolo_dopo": "Il trimestre in tre minuti", "titolo_cambiato": false,
  "conteggi": {"invariata": 3, "modificata": 2, "aggiunta": 0, "rimossa": 0},
  "righe": [
    {"stato": "modificata", "n_prima": 1, "n_dopo": 1, "speaker": "Anna Neri", "speaker_prima": "Anna Neri",
     "testo_prima": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico.",
     "testo_dopo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico, e Giulia Bianchi, analista finanziaria.",
     "segmenti": [{"tipo": "uguale", "testo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico"},
                  {"tipo": "aggiunto", "testo": ", e Giulia Bianchi, analista finanziaria"},
                  {"tipo": "uguale", "testo": "."}]},
    {"stato": "invariate", "conteggio": 2, "da_n": 2, "a_n": 3},
    {"stato": "modificata", "n_prima": 4, "n_dopo": 4, "speaker": "Mario Rossi", "…": "…"},
    {"stato": "invariate", "conteggio": 1, "da_n": 5, "a_n": 5}
  ]
}
```

- `stato`: `invariata`, `modificata`, `aggiunta` (solo `n_dopo`, `testo_dopo`),
  `rimossa` (solo `n_prima`, `testo_prima`). Con `--only-changes` le invariate
  consecutive diventano `{"stato": "invariate", "conteggio", "da_n", "a_n"}`:
  è la riga "2 battute invariate · mostra" del wireframe, con `da_n`/`a_n`
  numerati nella versione di arrivo.
- Due battute contano come "la stessa, modificata" solo con lo stesso speaker
  e un testo simile; altrimenti risultano una rimossa e una aggiunta.
- `segmenti[]` dà il confronto parola per parola dentro una battuta modificata.
- "Richiesta che ha creato la vN" è `to.instruction`.
- **Ripristina** crea una **nuova** versione con il contenuto della vN e
  ritira l'approvazione, anche se quel testo era stato approvato: va
  riapprovata. La storia resta intatta e il JSON è quello del copione, con in
  più `restored_from` e `versions`. Ripristinare la versione corrente dà
  `NothingToChange`.
