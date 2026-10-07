# Contratto d'interfaccia · podcast-agent per l'adattatore web

> Documento in costruzione: si completa fase per fase (vedi `docs/avanzamento/`).
> Coperti finora: contratto generale (C0), passi 1–4 + Confronto (C1), correzioni
> della revisione di sicurezza C0+C1 e modalità web.
> Le azioni dei passi 5–7, Archivio e Librerie arrivano con le fasi C2–C4; la
> versione completa con la sezione "a carico dell'adattatore" chiude la C5.

## 1. Come si chiama un comando

Ogni azione è un comando `tools/<nome>.py` lanciato come sottoprocesso:

```
PODCAST_AGENT_WEB=1 <progetto>/.venv/bin/python tools/<nome>.py [argomenti]
```

- **stdout**: sempre **un solo oggetto JSON**, sia in caso di successo sia di
  errore. Va letto per intero solo a processo terminato.
- **stderr**: messaggi per l'umano (avanzamento, avvisi). Non va interpretato:
  si può mostrare in un pannello "dettagli" o registrare nei log.
- **exit code**: uno di quattro valori, sempre coerente con il JSON.
- Nessun comando chiede input da tastiera: tutto passa dagli argomenti.
- I **valori liberi** scritti dall'utente (testo di una battuta, titolo,
  istruzioni, nomi) si passano sempre nella forma `--opzione=valore`: un valore
  che comincia con `-` (`-Davvero?`) altrimenti viene preso per un'opzione.
- Argomenti non validi (mancanti, sconosciuti, malformati) danno exit 1
  `InvalidArguments`, con il JSON come ogni altro errore (`details.messaggio`).
- **Timeout**: l'adattatore mette un limite di tempo ai sottoprocessi brevi
  (estrazione dei documenti: `doc_extract.py`, `run_init.py --document`,
  `episode_config.py --documento`). Le estrazioni hanno già un limite interno
  di tempo e di lavoro, ma una singola pagina di un PDF non si interrompe da
  dentro.
- L'adattatore imposta **sempre** `PODCAST_AGENT_WEB=1` (vedi "Modalità web"):
  i comandi usano allora sempre `config.yaml` del progetto.

### Modalità web

Con la variabile d'ambiente `PODCAST_AGENT_WEB` impostata (qualunque valore
diverso da vuoto, `0`, `false`, `no`, `off`) i comandi **rifiutano** gli
argomenti riservati all'uso locale, invece di affidarsi solo alla disciplina
dell'adattatore:

| Argomento | Perché è riservato |
|---|---|
| `--config` | `script.client_factory` in un config scelto da fuori importa ed esegue un modulo qualsiasi |
| `--trusted-paths` | è l'unico modo di uscire da `documenti/` e `output/` |
| `--no-approval-gate` | salta il gate sullo sha256 del copione approvato |
| `--no-consent-gate` | (C2) salta il consenso prima della clonazione |
| `--save-to`, `--text-file`, `--session`, `--script-json`, `--script`, `--out`, `--voices`, `--audio-paths` | file passati per percorso: l'adattatore lavora sempre per `--run-dir`, e i nomi dei file li fissa il tool |
| `--document` di `script_write.py` | il documento si registra con `episode_config.py --documento` |
| `--current` di `script_approve.py` | dall'interfaccia si approva solo la versione che l'utente ha letto (`--version V`) |

Il controllo guarda i valori già interpretati, quindi valgono anche le
abbreviazioni (`--trusted`) e la forma `--opzione=valore`. Esito:

```json
{
  "ok": false,
  "error_type": "NotAllowedInWebMode",
  "error": "Argomenti riservati all'uso locale, non ammessi dall'interfaccia web: --trusted-paths.",
  "exit_code": 1,
  "details": {"argomenti": ["--trusted-paths"]}
}
```

Il comando non fa niente prima del rifiuto. Per lo stesso motivo in modalità
web non si usano `voices_prepare.py --out` né `run_finalize.py` (`--voices`
obbligatorio): preparazione delle voci e chiusura della run fanno parte di
`produce.py` (C2). `NotAllowedInWebMode` indica un
errore dell'adattatore, non dell'utente: va registrato nei log, non mostrato
come campo da correggere. Conseguenza: `synthesize.py` ed `estimate.py`
(entrambi con `--script` obbligatorio) e `script_save.py` (`--script-json`)
non sono utilizzabili in modalità web; produzione e stima per `--run-dir`
arrivano in C2.

### Campi da mostrare e campi da non mostrare

Le risposte di successo contengono ancora percorsi **assoluti** del server,
che servono al subagent: `run_dir`, `session_file`, `script_file`,
`episodio_file`, `musiche_file`, `musiche_usate_file`, `output`, `path`,
`documento.percorso`, `voices_dir`, `music_dir`, `timings_file`,
`segnali_file`, `voci_usate`, `text_file`, `audio_file`. **L'adattatore non
li mostra mai all'utente.** Per la UI ci sono i nomi relativi:

- `run`: il nome della cartella dell'episodio (è anche un `--run-dir` valido);
- `documento.nome`, `name`: il nome del documento nella libreria.

Altre garanzie:
- i messaggi d'errore (`error`, `details`) e **tutto** stderr riportano solo
  nomi o percorsi relativi al progetto (`output/<run>/...`). Una cartella
  configurata fuori dal progetto diventa il suo nome; un altro percorso sotto
  la home diventa `~/...`;
- `in_corso` (in `script_show.py` e in `RunBusy.details`) non contiene più il
  `pid` del processo, che resta solo nel file `_lavoro.json`.

### Percorsi: cosa si può passare

Dalla revisione di sicurezza C0+C1 i tool controllano da soli dove stanno i
percorsi che ricevono, quindi la difesa non dipende dall'adattatore:

- `--run-dir` (e `--session`, che vale come la sua cartella) deve essere una
  **sottocartella diretta di `output/`**, cioè una run dell'Archivio. Si può
  passare il percorso assoluto o il **solo nome della cartella**. `..`, percorsi
  fuori da `output/` e link simbolici che portano fuori danno exit 1
  `PathNotAllowed`. Una run inesistente dà exit 1 e non crea cartelle.
- i **documenti** (`--document`, `--documento`, argomento di `doc_extract.py`)
  si indicano **per nome** e devono stare nella libreria `documenti/`. File
  nascosti, file senza estensione gestita e percorsi che escono dalla libreria
  (anche tramite link simbolici) sono rifiutati. Un nome non viene mai cercato
  nella cartella corrente.
- i file passati per percorso (`--script`, `--voices`, `--out`,
  `--audio-paths`, `--script-json`, `--text-file`, `--save-to`) devono stare
  dentro `output/`.
- `--trusted-paths` toglie gli ultimi due vincoli ed esiste **solo per l'uso
  locale** (subagent, CLI). **L'adattatore non deve mai passarlo**, e in
  modalità web è comunque rifiutato (`NotAllowedInWebMode`).

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
| `RunBusy` | 1 | Sulla run è già in corso un'operazione lunga | `in_corso: {operazione, dal, …}` (senza `pid`) |
| `PathNotAllowed` | 1 | Un percorso esce dalla cartella prevista (vedi "Percorsi") | `campo` (`run_dir`, `save_to`, `script`, ...) |

### Introdotti con le correzioni di sicurezza C0+C1

| error_type | exit | Quando | `details` |
|---|---|---|---|
| `DocumentTooLarge` | 1 | Il documento supera un limite di `documents:` in `config.yaml` | `limite` (`byte`, `pagine`, `caratteri`, `byte_scompattati`, `byte_xml`, `parti_zip`, `contenuto_pagine`, `secondi`), `massimo`, `valore`, `file` |
| `VersionRequired` | 1 | `script_approve.py` senza `--version` (né `--current`) | `current_version` |
| `NotAllowedInWebMode` | 1 | Argomento riservato all'uso locale in modalità web | `argomenti` |
| `InvalidArguments` | 1 | Argomenti non validi per il comando | `messaggio` |

I limiti dei documenti (default: 20 MB, 500 pagine, 2.000.000 di caratteri;
per un DOCX 100 MB scompattati, 15 MB di parti XML, 2000 parti; per un PDF
50 MB di contenuto delle pagine decodificato e 60 secondi) si controllano
**prima** di leggere il contenuto, tranne caratteri, contenuto delle pagine e
tempo, che si contano durante l'estrazione: un PDF si ferma alla prima pagina
oltre il limite. La UI mostra "Documento troppo
grande" con il limite superato.

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
un'altra operazione per più di 30 secondi. In `details`: `file` (solo il nome)
e `timeout_seconds`. Di norma non succede: le operazioni protette durano
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
cartella: una scansione o un documento troppo grande non lasciano bozze vuote in
Archivio. Restituisce `run` (nome della cartella), `run_dir` ed `episodio` (la
stessa vista di `episode_config.py`, sotto). Due bozze con lo stesso titolo
create nello stesso minuto ottengono due cartelle diverse (suffisso `-2`,
`-3`, ...).

L'estrazione del documento avviene **fuori** dal lock di `episodio.json`:
mentre un PDF lungo viene letto, gli altri utenti della stessa bozza non
aspettano.

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
  "run": "2026-10-06_17-26_il-trimestre",
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
| 1 | `EpisodeConfigError` (`details.campo`), voce inesistente (`VoiceLibraryError`), `DocumentNotExtractable`, `DocumentTooLarge` |
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
`in_corso: {"operazione": "scrittura_copione" | "revisione_copione", "dal"}`,
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
  "run": "2026-10-06_17-26_il-trimestre",
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

**`script_approve.py --version V --by U`**: `--version` è **obbligatorio** per
approvare. Senza, il comando non approva ed esce con exit 1 `VersionRequired`
(`details.current_version`). Esiste anche `--current` (approva la versione
corrente, qualunque sia), riservato al subagent: l'adattatore passa sempre la
versione che l'utente ha letto.

```json
{"ok": true, "approved": true, "version": 7, "sha256": "sha256:3512…cc35",
 "at": "2026-10-06T17:31:27", "by": "Utente A", "run": "2026-10-06_17-26_il-trimestre",
 "session_file": "output/<run>/_script_session.json"}
```

**`--revoke --by U`** restituisce l'approvazione ritirata (`null` se non ce
n'era una):

```json
{"ok": true, "approved": false,
 "revoked": {"version": 7, "sha256": "sha256:3512…cc35", "at": "2026-10-06T17:31:27", "by": "Utente A"},
 "revoked_by": "Utente A", "current_version": 7, "run": "2026-10-06_17-26_il-trimestre",
 "session_file": "output/<run>/_script_session.json"}
```

| exit | Comandi del passo 4 |
|---|---|
| 0 | fatto (per `script_write`/`script_revise` anche con `errors` non vuoto: la versione è salvata) |
| 1 | `ScriptInvalid`, `ScriptHasErrors`, `VersionConflict`, `VersionRequired`, `NothingToChange`, `TurnNotFound`, `RunBusy`, `ScriptWriterError` (API: rifiuto, risposta non valida, rete), cast incompleto (prima di qualunque chiamata a pagamento) |
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
