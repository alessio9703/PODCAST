# Contratto d'interfaccia · podcast-agent per l'adattatore web

> Versione completa (fase C5, 2026-10-07; aggiornata in C7 con le correzioni della
> revisione finale, `docs/avanzamento/C7.md`). Copre tutte le schermate del wireframe in
> `docs/wireframe/wireframe/`: per ogni azione il comando, gli argomenti, un esempio reale di JSON,
> gli exit code, se è lunga e se costa. In fondo: cosa resta a carico dell'adattatore e
> cosa non è supportato, con il motivo. Lo storico delle fasi è in `docs/avanzamento/`.
>
> Indice: §0 primo requisito (`PODCAST_AGENT_WEB=1`, `doctor.py --web`) · §1 come si chiama un comando (modalità web compresa) · §2 i quattro esiti ·
> §3 gli `error_type` · §4 concorrenza · Azioni per schermata (indice delle azioni, passi
> 1–7, Confronto, Archivio, librerie, Impostazioni, Lezioni, Diagnostica) · A carico
> dell'adattatore · Non supportato.

## 0. Primo requisito: `PODCAST_AGENT_WEB=1`

L'adattatore lancia **ogni** comando con la variabile d'ambiente
`PODCAST_AGENT_WEB=1`. È il requisito da cui dipendono tutti gli altri: senza, i
comandi non rifiutano gli argomenti riservati all'uso locale (`--config`,
`--trusted-paths`, `--no-approval-gate`, `--no-consent-gate`, i file passati per
percorso; vedi "Modalità web" più sotto), e il riuso di una voce clonata non
chiede il consenso registrato (C7, N1).

All'avvio, prima di accettare richieste, l'adattatore lancia:

```
PODCAST_AGENT_WEB=1 <progetto>/.venv/bin/python tools/doctor.py --web
```

`--web` aggiunge il controllo **obbligatorio** `web_mode`: se la variabile non
arriva al sottoprocesso (configurazione sbagliata del servizio, ambiente
ripulito) il comando esce con `1` e `"ok": false`, e l'adattatore non deve
partire. Il controllo è nel JSON di `doctor.py`:

```json
{"name": "web_mode", "ok": false, "required": true,
 "detail": "PODCAST_AGENT_WEB NON impostata: l'adattatore deve lanciare ogni tool con PODCAST_AGENT_WEB=1, altrimenti --config, --trusted-paths, --no-approval-gate e simili restano accettati"}
```

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

Il comando non fa niente prima del rifiuto. In modalità web **non si usano per
intero** `voices_prepare.py` e `synthesize.py` (`NotAllowedInWebMode` con
`details.comando`): preparazione delle voci, sintesi e chiusura della run fanno
parte di `produce.py`, legato al copione approvato. Per lo stesso motivo
`run_finalize.py` (`--voices` obbligatorio) non si usa. `NotAllowedInWebMode` indica un
errore dell'adattatore, non dell'utente: va registrato nei log, non mostrato
come campo da correggere. Anche `script_save.py` (`--script-json` obbligatorio) non
si usa; la stima dall'interfaccia è `estimate.py --run-dir` (senza `--script`).

### Campi da mostrare e campi da non mostrare

Le risposte di successo contengono ancora percorsi **assoluti** del server,
che servono al subagent: `run_dir`, `session_file`, `script_file`,
`episodio_file`, `musiche_file`, `musiche_usate_file`, `output`, `path`,
`documento.percorso`, `voices_dir`, `music_dir`, `timings_file`,
`segnali_file`, `voci_usate`, `text_file`, `audio_file`, `events_file`. In
modalità web `voices_list.py` e `music_list.py` danno le cartelle per nome e
`voices_list.py` omette `path`, `voice_id` e `cache_reason`. **L'adattatore non
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

### Introdotti in C2

| error_type | exit | Quando | `details` |
|---|---|---|---|
| `ConsentRequired` | **3** | Una voce da usare non ha il consenso valido: **nessuna voce è stata clonata** (o, a produzione in corso, un consenso è stato revocato: ci si ferma) | `voci: [{speaker, azione, stato, file}]` (+ `done`, `total`, `missing` se la sintesi era partita) |
| `CloneOutcomeUnknown` | **3** | Una clonazione precedente della stessa voce è finita con un errore o è stata interrotta: la voce potrebbe esistere sul provider. **Non si ritenta da soli** | `speaker`, `dal`, `provider` |
| `RecordingChanged` | **3** | `voice_meta.py --consent --expect-sha256 H`: la registrazione non è più quella che l'utente ha visto. Niente registrato | `voce`, `atteso`, `attuale` |
| `ConsentByRequired` | 1 | `voice_meta.py --consent`/`--revoke-consent` senza `--by` | `campo: "by"` |
| `VoiceMetaError` | 1 | File dei metadati di una voce illeggibile | `file` |
| `ApprovalRequired` | 1 | `produce.py` su un copione non approvato, o diverso da quello approvato | `approval_problem` |
| `ScriptMissing` | 1 | Stima o produzione di una run senza copione | — |
| `ProductionInterrupted` | 1 | Produzione fermata (SIGTERM/Ctrl-C) fra una battuta e l'altra | `done`, `total`, `missing` |

`ConsentRequired` è un esito 3: la UI mostra le caselle "Confermo che … ha dato il
consenso" per le voci in `details.voci`, registra le conferme con `voice_meta.py` e
richiama `produce.py`. `CloneOutcomeUnknown` chiede all'utente di controllare le voci
sul provider (una voce duplicata va cancellata lì) e di confermare con
`voice_meta.py --name VOCE --confirm-clone-retry --by U`; poi si richiama `produce.py`. `stato` dice perché: `mancante`, `superato` (la registrazione è
stata sostituita dopo il consenso) o `revocato` (vale anche per una voce già clonata).

### Introdotti in C3

| error_type | exit | Quando | `details` |
|---|---|---|---|
| `UnknownDirection` | 1 | Retake con un'indicazione di resa che non è in `config.yaml` | `indicazione`, `ammesse` |
| `DirectionNeedsTextEdit` | 1 | Retake con "Pronuncia diversa": va corretto il testo | `indicazione` |
| `ConflictingDirections` | 1 | Due indicazioni opposte insieme (es. più lento e più veloce) | `indicazioni` |
| `TooManyTakes` | 1 | Più take di `retake.max_takes` | `massimo`, `richiesti` |
| `SegmentsMissing` | 1 | `mix.py` senza tutti i segmenti in cache (copione cambiato o episodio vecchio): serve `produce.py` | `missing` |
| `WaveformError` | 1 | Numero di punti fuori scala, file non decodificabile | `campo`, `minimo`, `massimo` |
| `EpisodeNotProduced` | 1 | Forma d'onda di un episodio senza file finale | — |

### Introdotti in C4

| error_type | exit | Quando | `details` |
|---|---|---|---|
| `MusicMetaError` | 1 | Metadati di un brano non validi (sottofondo con parlato, trascrizione senza parlato, testo vuoto o troppo lungo, `--by` mancante, brano inesistente) | `campo` |
| `SpokenNameIncomplete` | 3 | Anche per `voice_meta.py --spoken-name` senza cognome: si conferma con `--accept-incomplete-name` | `campo`, `voce`, `nome` |

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
esempi `output/<run>` sta per la cartella dell'episodio (assoluta nei campi elencati
in §1 "Campi da mostrare e campi da non mostrare", relativa negli altri) e le liste
lunghe sono accorciate con `"…"`.

### Indice: schermata del wireframe → comandi

| Schermata (`docs/wireframe/wireframe/`) | Comandi | Sezione |
|---|---|---|
| Menu laterale (`Sidebar`): Nuovo episodio | `run_init.py` | Passo 1 |
| Archivio (`Main`) | `runs_list.py`, `run_status.py` | Archivio |
| 1 · Documento | `documents_list.py`, `doc_extract.py`, `run_init.py`, `episode_config.py --documento` | Passo 1 |
| 2 · Cast e formato | `episode_config.py`, `voices_list.py` | Passo 2 |
| 3 · Musiche | `music_list.py`, `music_set.py`, `episode_config.py` | Passo 3 |
| 4 · Copione | `script_write.py`, `script_show.py`, `script_edit.py`, `script_revise.py`, `script_approve.py`, `script_versions.py --list` | Passo 4 |
| Confronto versioni | `script_versions.py --diff/--show/--restore` | Pannello Confronto |
| 5 · Stima e consenso | `estimate.py --run-dir`, `voice_meta.py --consent` | Passo 5 |
| 6 · Generazione | `produce.py --events`, `script_show.py` (`in_corso`), `run_status.py` | Passo 6 |
| 7 · Episodio e correzioni | `episode_info.py`, `waveform.py`, `turn_at.py`, `retake.py`, `mix.py`, `script_edit.py`, `music_set.py` | Passo 7 |
| Documenti | `documents_list.py`, `doc_extract.py`, `run_init.py` | Libreria Documenti |
| Voci | `voices_list.py`, `voice_meta.py` | Libreria Voci |
| Musiche | `music_list.py`, `music_meta.py` | Libreria Musiche |
| Avanzate: Lezioni | `lessons.py` | Lezioni |
| Avanzate: Impostazioni | `settings_show.py` | Impostazioni |
| Avanzate: Diagnostica | `doctor.py` | Diagnostica |
| Stati e errori (`Stati`) | exit code e `error_type` | §2, §3 |

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
  `null` se il ruolo non ha voce. `consenso` è lo stato del consenso alla
  clonazione di quella voce (vedi passo 5), `null` se il ruolo non ha voce.
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

## Passo 3 · Musiche

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Elenco brani per sezione | `music_list.py` | no | no |
| Scegli / togli un brano | `music_set.py --run-dir R --intro NOME` / `--none intro` | no | no |
| Salta il passo (nessuna musica) | `music_set.py --run-dir R --none intro outro sottofondo` | no | no |
| Durata totale indicativa | `episode_config.py --run-dir R` → `durata_indicativa` | no | no |

Le durate dei brani mp3/m4a si misurano anche senza `ffprobe` di sistema. Per
ogni brano `music_list.py` dà anche licenza, parlato, trascrizione e "usato in"
(vedi Libreria Musiche).

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

## Passo 5 · Stima e consenso

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Stima (costo, durata, battute da generare, voci) | `estimate.py --run-dir R` | no | no |
| Stato del consenso di una voce | `voice_meta.py --name VOCE` | no | no |
| "Confermo che … ha dato il consenso" | `voice_meta.py --name VOCE --consent --by U --expect-sha256 H` | no | no |
| Clonazione dall'esito incerto: "ho controllato, ritenta" | `voice_meta.py --name VOCE --confirm-clone-retry --by U` | no | no |
| Revoca il consenso | `voice_meta.py --name VOCE --revoke-consent --by U` | no | no |

**`estimate.py --run-dir R`** legge il copione della run e funziona anche prima che le
voci siano pronte. Per le voci già clonate le battute in cache si riconoscono dal
registro; le battute delle voci da clonare contano tutte come da generare. Esempio reale
(dopo una produzione, con costo di clonazione e cambio configurati):

```json
{
  "ok": true,
  "run": "2026-10-07_11-19_produzione",
  "approved": true, "approved_version": 1, "approval_problem": null,
  "turns": 6, "speakers": 2, "chars": 216, "words": 33, "spoken_chars": 216,
  "duration_seconds": 15.8, "duration_label": "0m 00s - 0m 30s",
  "duration_with_music_seconds": 15.8, "duration_with_music_low_seconds": 0.0, "duration_with_music_high_seconds": 30.0,
  "cost_basis": "$10.00 / 1M caratteri", "rendered": "…",
  "voices": [
    {"speaker": "Mario Rossi", "azione": "reuse", "motivo": "gia' clonata, riuso il voice_id esistente",
     "consenso": {"stato": "registrato", "audio_sha256": "sha256:…", "da": "Utente A", "il": "2026-10-07T11:19:17",
                  "revocato_da": null, "revocato_il": null},
     "clonata_il": "2026-10-07T11:19:13", "file": "Mario Rossi.wav", "clonazione_incerta": false, "ruolo": null},
    "…"
  ],
  "voices_to_clone": [], "consent_missing": [],
  "turns_to_generate": 0, "turns_to_generate_list": [], "chars_to_generate": 0,
  "cost_tts_usd": 0.0, "clone_cost_usd": 0.5, "clone_cost_known": true, "clone_cost_total_usd": 0.0,
  "cost_usd": 0.0, "cost_complete": true,
  "cost_eur": 0.0, "eur_per_usd": 0.9, "eur_rate_date": "2026-10-01"
}
```

- **Costo stimato**: `cost_usd` è il costo di **questa** produzione (battute da generare
  + voci da clonare). `cost_eur` c'è solo se `config.yaml` ha un cambio datato
  (`currency.eur_per_usd`, `currency.rate_date`), altrimenti è `null` e la UI mostra i
  dollari (decisione D3).
- Il costo di una clonazione sta in `providers.<nome>.clone_cost_usd`. Se è `null`
  (`clone_cost_known: false`) e ci sono voci da clonare, `cost_complete` è `false`: la
  UI scrive che il costo delle clonazioni non è noto.
- **Battute da generare**: `turns_to_generate` di `turns` ("le altre sono già pronte").
- **Voci dell'episodio**: `voices[]`, con `azione` (`reuse` = "Già clonata il
  `clonata_il`", `clone` = "Da clonare per la prima volta"), `ruolo` (da
  `episodio.json`, `null` se manca) e `consenso`. La casella del consenso si mostra per
  le voci in `consent_missing`.
- **Copione approvato**: `approved`, `approved_version`, `approval_problem` (stessa regola
  del gate: sha256 del copione approvato).

**`voice_meta.py`** risponde
`{"voce", "file", "consenso": {"stato", "audio_sha256", "da", "il", "revocato_da", "revocato_il"}, "consenso_revocato", "clonazione_incerta", "clonazione_incerta_tolta"}`.
**`--expect-sha256`** è obbligatorio in modalità web con `--consent`: la UI ripassa il
`consenso.audio_sha256` che ha mostrato (dalla stima o da `voice_meta.py`). Se la
registrazione è cambiata nel frattempo, exit 3 `RecordingChanged` e il consenso non si
registra.
Il nome si risolve senza indovinare: esatto a meno di maiuscole e accenti, altrimenti
exit 3 `AmbiguousName` con `candidates`. Il consenso vale per la registrazione attuale (se
ne salva lo sha256): se il file audio viene sostituito lo stato diventa `superato`. Una
voce con il consenso **revocato** non si clona e non si riusa.

## Passo 6 · Generazione

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Genera l'episodio / riprendi | `produce.py --run-dir R --events` | **sì** | **sì** (clonazione e sintesi) |
| Avanzamento | *adattatore*: legge il file degli eventi mentre il comando gira | — | — |
| Interrompi la generazione | *adattatore*: SIGTERM al processo di `produce.py` | — | — |
| Stato "In generazione" (anche dopo aver chiuso la pagina) | `script_show.py --run-dir R` → `in_corso.operazione == "produzione"` | no | no |

`produce.py` tiene il lock di lavoro della run per tutta la durata (una seconda
produzione, scrittura o revisione sulla stessa run esce con `RunBusy`) e fa, in ordine:

1. **approvazione**: il copione su disco deve essere quello approvato, altrimenti exit 1
   `ApprovalRequired` e niente speso;
2. **musiche**: validate prima di spendere (ffmpeg, brani, sha256);
3. **voci**: riuso o clonazione, solo con il consenso (exit 3 `ConsentRequired`, nessuna
   clonazione fatta), **senza retry** (un errore del provider esce con exit 2: blocco,
   nessun "Riprova"; il rilancio dà exit 3 `CloneOutcomeUnknown` finché l'utente non
   conferma). Il consenso si ricontrolla sui byte effettivamente mandati al provider,
   subito prima di ogni clonazione. Due produzioni con la stessa voce nuova la clonano
   una volta sola;
4. **sintesi** delle sole battute non in cache, **montaggio**, **mix** con le musiche;
5. **chiusura**: `voci_usate.json`, copia del documento, tempi e segnali.

Esempio reale (rilancio con tutto in cache):

```json
{
  "ok": true,
  "run": "2026-10-07_11-19_produzione",
  "output": "output/2026-10-07_11-19_produzione/produzione-di-prova.mp3", "file": "produzione-di-prova.mp3",
  "format": "mp3", "duration_seconds": 6.9, "duration_label": "0m 07s",
  "segments": 6, "generated_turns": [], "reused_turns": [1, 2, 3, 4, 5, 6], "tts_calls": 0,
  "voices": {"riusate": ["Mario Rossi", "Giulia Bianchi"], "clonate": []},
  "approved_version": 1, "documento": "documento_originale.txt",
  "timings_file": "output/2026-10-07_11-19_produzione/_tempi_turni.json", "warnings": [],
  "events_file": "output/2026-10-07_11-19_produzione/_eventi.jsonl"
}
```

`produce.py` è il comando dell'interfaccia: i suoi percorsi (`output`, `timings_file`,
`musiche_usate_file`, `events_file`) sono **relativi**, nella forma `output/<run>/<file>`.

**Eventi** (`--events`: il file è sempre `output/<run>/_eventi.jsonl`, il nome lo fissa
il tool; svuotato all'inizio di ogni produzione; un link simbolico al suo posto dà
`PathNotAllowed`): una riga JSON per evento, ognuna con `evento` e `at`. Anche gli
eventi finali (`interrotta`, `errore`) si scrivono prima di rilasciare il lock. Esempio reale (con
un'intro, voci già clonate):

```
{"evento": "iniziata", "at": "2026-10-07T11:19:22", "totale": 2, "fasi": ["preparazione_voci", "sintesi", "montaggio", "mix", "chiusura"]}
{"evento": "fase_iniziata", "at": "…", "fase": "preparazione_voci"}
{"evento": "voce", "at": "…", "speaker": "Mario Rossi", "azione": "riusata"}
{"evento": "voce", "at": "…", "speaker": "Giulia Bianchi", "azione": "riusata"}
{"evento": "fase_finita", "at": "…", "fase": "preparazione_voci", "riusate": 2, "clonate": 0}
{"evento": "fase_iniziata", "at": "…", "fase": "sintesi"}
{"evento": "battuta", "at": "…", "n": 1, "totale": 2, "speaker": "Mario Rossi", "cached": false, "eta_seconds": null}
{"evento": "battuta", "at": "…", "n": 2, "totale": 2, "speaker": "Giulia Bianchi", "cached": false, "eta_seconds": 0.0}
{"evento": "fase_finita", "at": "…", "fase": "sintesi"}
{"evento": "fase_iniziata", "at": "…", "fase": "montaggio"}
{"evento": "fase_finita", "at": "…", "fase": "montaggio"}
{"evento": "fase_iniziata", "at": "…", "fase": "mix"}
{"evento": "fase_finita", "at": "…", "fase": "mix"}
{"evento": "fase_iniziata", "at": "…", "fase": "chiusura"}
{"evento": "fase_finita", "at": "…", "fase": "chiusura"}
{"evento": "finita", "at": "…", "file": "con-musica.mp3", "duration_seconds": 3.8, "tts_calls": 2}
```

| `evento` | Campi | Uso nello schermo "Generazione" |
|---|---|---|
| `iniziata` | `totale`, `fasi` | Quante battute, quali fasi (`mix` compare solo con musiche) |
| `fase_iniziata` / `fase_finita` | `fase` (+ `riusate`, `clonate` alla fine di `preparazione_voci`) | Pronta / In corso / In attesa; "1 riusata · 2 clonate" |
| `voce` | `speaker`, `azione` (`riusata`, `clonata`) | Dettaglio della preparazione voci |
| `battuta` | `n`, `totale`, `speaker`, `cached`, `eta_seconds` | "[n] di [totale] · ~[m] min rimanenti" (`eta_seconds` è `null` finché non c'è una misura) |
| `avviso` | `testo` | Avvisi (es. battuta sospetta, brano senza licenza) |
| `finita` | `file`, `duration_seconds`, `tts_calls` | "Ascolta l'episodio" |
| `interrotta` | `done`, `total`, `missing` | "Potrai riprendere dall'Archivio" |
| `errore` | `error_type`, `error` | Lo stesso errore del JSON finale |

Il file degli eventi si apre solo dopo che il lock è stato preso: un secondo lancio
respinto con `RunBusy` non lo tocca. **Revoca a produzione in corso**: il consenso si
ricontrolla fra una battuta e l'altra; se una voce viene revocata la produzione si ferma
con exit 3 `ConsentRequired` (con `done`/`total`/`missing`), evento `errore`. Un errore prima di quel punto (approvazione,
consenso, percorsi) è solo nel JSON su stdout.

**Interruzione.** SIGTERM (o Ctrl-C) ferma la produzione fra una battuta e l'altra: la
battuta in corso finisce e si salva, una clonazione già partita finisce. Esito exit 1
`ProductionInterrupted`:

```json
{"ok": false, "error_type": "ProductionInterrupted",
 "error": "Produzione interrotta: 3 battute pronte su 8. Le battute pronte restano salvate: rilanciando si generano solo le mancanti.",
 "exit_code": 1, "details": {"done": 3, "total": 8, "missing": [4, 5, 6, 7, 8]}}
```

Per riprendere si rilancia lo stesso comando: si pagano solo le battute in `missing`. Un
secondo SIGTERM ferma subito (esito `Interrupted`), senza aspettare la battuta in corso;
durante una clonazione e la sua registrazione i segnali sono rinviati alla fine di
quel passo. I file finali (episodio, master di sola voce) si scrivono su un temporaneo e
prendono il nome definitivo solo a codifica finita: un'interruzione non lascia un
episodio troncato. Ogni esecuzione di ffmpeg ha un limite di 15 minuti.

| exit | `produce.py` |
|---|---|
| 0 | episodio pronto |
| 1 | `ApprovalRequired`, `ScriptMissing`, `RunBusy`, `ProductionInterrupted`, `Interrupted`, `PathNotAllowed` (`_eventi.jsonl` link simbolico), errori delle musiche (anche ffmpeg oltre il limite di tempo), errore di sintesi di una battuta (`AssemblyError`: si può rilanciare, si pagano solo le mancanti) |
| 2 | errore del provider durante la **clonazione** (`ProviderQuotaError`, `ProviderAuthError`, …): blocco, nessun "Riprova" |
| 3 | `ConsentRequired`, `CloneOutcomeUnknown`, `MusicChangedError` (brano sostituito: si conferma con `--accept-changed-music`) |

## Passo 7 · Episodio e correzioni

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Intestazione, sezioni, trascrizione, file | `episode_info.py --run-dir R` | no | no |
| Forma d'onda | `waveform.py --run-dir R --points N` (N fra 50, 100, 200, 400, 800, 1600) | no (la prima volta qualche secondo) | no |
| Scarica mp3 | *adattatore*: il file in `file_finale.percorso` | — | — |
| Clic su un punto → battuta | dalla `trascrizione` (`inizio_finale`/`fine_finale`), oppure `turn_at.py --run-dir R --at 4:12` | no | no |
| "Come è detta": stima | `retake.py --run-dir R --turn N --takes K [--direction "Più calmo" …] --dry-run` | no | no |
| Genera K take | `retake.py --run-dir R --turn N --takes K [--direction …] --by U` | **sì** | **sì** |
| Ascolta in contesto (tutti i take, originale compreso) | `retake.py --run-dir R --list N [--with-music]` | no | no |
| Usa il take e rimonta | `retake.py --run-dir R --choose N=K`, poi `mix.py --run-dir R` | no | no |
| "Cosa dice": correggi il testo | `script_edit.py --run-dir R --turn N --text=… --expected-version V`, poi `script_approve.py --version V+1`, poi `produce.py --run-dir R --events` (paga solo la battuta cambiata) | sì (produce) | sì (una battuta) |
| Cambia musiche | `music_set.py --run-dir R …`, poi `mix.py --run-dir R` | no | no |

**`episode_info.py`** (esempio reale, trascrizione e file accorciati):

```json
{
  "ok": true,
  "run": "2026-10-07_12-22_episodio-c3",
  "titolo": "Un altro titolo",
  "stato": "pronto", "motivi": [], "verificabile": true, "in_corso": null,
  "copione_approvato": true, "formato": "conversazione",
  "durata_seconds": 8.9, "durata_label": "0:09",
  "voci": [{"speaker": "Mario Rossi", "ruolo": "voce", "nome_pronunciato": null}, "…"],
  "numero_voci": 2, "montato_il": "2026-10-07T12:22:44",
  "file_finale": {"nome": "episodio-c3.mp3", "percorso": "output/2026-10-07_12-22_episodio-c3/episodio-c3.mp3",
                  "formato": "mp3", "dimensione_bytes": 215083},
  "sezioni": [{"tipo": "intro", "inizio": 0.0, "fine": 0.5}, {"tipo": "parlato", "inizio": 1.3, "fine": 8.9, "battute": 6}],
  "trascrizione": [
    {"n": 1, "speaker": "Mario Rossi", "ruolo": "voce", "presentatore": false, "nome_pronunciato": null,
     "testo": "Ciao a tutti, oggi parliamo di ricavi.", "testo_parlato": "Ciao a tutti, oggi parliamo di ricavi.",
     "tag": [], "inizio_finale": 1.5, "fine_finale": 2.26, "inizio_label": "0:02"},
    "…"
  ],
  "tempi_non_disponibili": null,
  "file": [{"nome": "_audio_paths.json", "percorso": "output/<run>/_audio_paths.json", "dimensione_bytes": 253,
            "modificato_il": "2026-10-07T12:22:38", "interno": true}, "…"]
}
```

- **`stato`** è derivato: `pronto`, `da_aggiornare` (con `motivi`: `copione`, `take`,
  `musiche`, cioè cosa è cambiato dopo il montaggio, e `non_approvato` se il copione
  attuale non è approvato), `in_corso` (un'operazione lunga sulla
  run: `in_corso.operazione`), `non_prodotto`. `verificabile: false` per le run montate
  prima di C3 (senza `_montaggio.json`): se il file c'è risultano `pronto`.
- **Intestazione** "Intervista · [mm:ss] · 3 voci · montato il [gg/mm]": `formato`,
  `durata_label`, `numero_voci`, `montato_il`.
- **Barra delle sezioni**: `sezioni[]`, in secondi nel file finale; `parlato.battute` è il
  numero di battute.
- **Trascrizione**: `inizio_finale`/`fine_finale` sono i secondi **nel file finale**
  (intro compresa): sono quelli del player. `null` se l'episodio non è prodotto o se i
  tempi non sono disponibili: `tempi_non_disponibili` è allora
  `{"codice", "messaggio"}` (`timeline_assente`, `timeline_vecchia`,
  `timeline_illeggibile`: si rimonta con `mix.py`; `timeline_diversa`: si produce di
  nuovo).
- **File**: `file[]` senza file nascosti, lock, link simbolici, cache della forma d'onda e
  file con dati del server (`_lavoro.json`, `_audio_paths.json`, `voci.json`,
  `voci_usate.json`, `*.percorso.txt`); `interno: true` per i file di lavoro (nome con
  `_` davanti). **L'adattatore serve per il download solo `file_finale` e i file con
  `interno: false`**, risolvendo il `percorso` dentro `output/<run>`.
- Il **nome del file finale** viene dal titolo dell'episodio (`episodio.json`) per le run
  nuove, e resta quello anche se il titolo cambia dopo (G7).

**`waveform.py`** (esempio reale con `--points 20`):

```json
{"ok": true, "run": "2026-10-07_12-41_episodio-c3", "file": "episodio-c3.mp3",
 "sha256": "sha256:64b1…95df", "points": 20, "duration_seconds": 8.9,
 "peaks": [0.235, 0.235, 0.0, 0.292, 0.292, 0.296, "…"], "cached": false}
```

I picchi (0–1) coprono l'intero file finale. Sono ammesse solo poche risoluzioni (50, 100,
200, 400, 800, 1600): la UI sceglie la più vicina e ridimensiona in pagina. Sono in cache in `_forma_onda_<N>.json`,
legata allo sha256 del file: dopo un rimontaggio si ricalcolano da soli (`cached: false`).

**Retake** ("Come è detta"): il testo resta quello approvato.
- **"Come deve cambiare?"** → `--direction` (ripetibile) con le etichette di
  `config.yaml` (`retake.indicazioni`; di base: Più lento, Più veloce, Più energico, Più
  calmo, Più serio, Più sorridente, Più pause). Ogni indicazione aggiunge la sua tag
  **solo** al testo inviato per quei take: copione e approvazione non cambiano. Due
  opposte insieme danno `ConflictingDirections`. "Pronuncia diversa" dà
  `DirectionNeedsTextEdit`: la UI porta su "Cosa dice".
- **"Altre indicazioni, con parole tue"**: **non supportato**. Nessun testo libero arriva
  al sintetizzatore fuori dal copione approvato (decisione D1). L'adattatore non lo passa.
- **Nuovi take**: `--takes K`, al massimo `retake.max_takes` (3). La stima (`--dry-run`)
  conta anche le tag (`chars`, `cost_usd`, `cost_eur`).
- **Ascolta in contesto**: `--list N` restituisce `take_list[]` con, per ogni take
  (originale compreso), `label` ("Take originale", "Take 1", …), `scelto`,
  `indicazioni`, `preview` (battuta prima + take + battuta dopo; `preview_ui` è lo stesso
  valore) e, con `--with-music`, `preview_with_music` se la run ha un sottofondo. Nessuna
  sintesi. Le anteprime dipendono dai segmenti montati: dopo una correzione o un take
  diverso accanto se ne monta una nuova.
- Nel JSON di `retake.py` **tutti i percorsi** (`file`, `files`, `preview`, `previews`,
  `previews_with_music`) sono relativi: `output/<run>/…`.
- **Usa il take e rimonta**: `--choose N=K` poi `mix.py`. Lo stato passa da
  `da_aggiornare` (`motivi: ["take"]`) a `pronto`.
- Generazione e scelta tengono il lock di lavoro della run; il gate di approvazione si
  attraversa dentro il lock, sul copione che si sintetizza. Una voce con il consenso
  revocato non si usa nemmeno per un retake o per le anteprime di `--list` (exit 3
  `ConsentRequired`).

**`mix.py`** rimonta dalla cache, con le musiche scelte o la sola voce (G1); zero sintesi,
tiene il lock di lavoro (operazione `montaggio`) e controlla l'approvazione dentro il lock,
sul copione che monta. Con una voce revocata non rimonta (exit 3 `ConsentRequired`). Se mancano segmenti (copione cambiato
dopo l'ultima produzione) esce con `SegmentsMissing`: serve `produce.py`.

| exit | Comandi del passo 7 |
|---|---|
| 0 | fatto |
| 1 | `WaveformError`, `EpisodeNotProduced`, `UnknownDirection`, `DirectionNeedsTextEdit`, `ConflictingDirections`, `TooManyTakes`, `SegmentsMissing`, `RunBusy`, gate di approvazione (`retake.py`/`mix.py` su un copione non approvato), `ScriptMissing` |
| 2 | errore del provider durante la sintesi di un take |
| 3 | `ConsentRequired` (retake, anteprime o rimontaggio con una voce revocata), `MusicChangedError` |

## Archivio

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Elenco degli episodi | `runs_list.py [--status bozze\|da_approvare\|in_produzione\|pronti] [--search T]` | no | no |
| Stato di un episodio (una riga) | `run_status.py --run-dir R` | no | no |
| Nuovo episodio | `run_init.py --title T [--document NOME]` | no | no |

Esempio reale (tre righe):

```json
{"ok": true,
 "episodi": [
   {"run": "2026-10-07_13-53_proposta-nome", "titolo": "Proposta nome", "documento": null, "formato": "conversazione",
    "voci": 1, "stato": "bozza_documento", "etichetta": "Bozza · Documento", "gruppo": "bozze", "azione": "Riprendi",
    "passo": 1, "aggiornato_il": "2026-10-07T13:53:48"},
   {"run": "2026-10-07_13-53_con-musica", "titolo": "Con musica", "documento": "report.txt", "formato": null, "voci": null,
    "stato": "bozza_copione", "etichetta": "Bozza · Copione", "gruppo": "bozze", "azione": "Riprendi", "passo": 4,
    "aggiornato_il": "2026-10-07T13:53:38"},
   "…"],
 "totale": 7, "conteggi": {"bozze": 5, "pronti": 2}}
```

Lo **stato è derivato** dai file della run e dai lock (`src/run_state.py`), dalla prima
regola che vale:

| `stato` | Quando | `gruppo` | `azione` | `passo` |
|---|---|---|---|---|
| `in_corso` | un'operazione lunga tiene il lock; per la produzione `battuta`/`battute_totali` dagli eventi | `in_produzione` | Segui l'avanzamento | 6 |
| `pronto` | file finale coerente con la run (per le run di prima, senza registro di montaggio: il file c'è e il copione, se c'è una sessione, è ancora quello approvato) | `pronti` | Ascolta | 7 |
| `da_aggiornare` | file finale, copione approvato, nessuna battuta da generare, ma take o musiche cambiati | `pronti` | Rimonta | 7 |
| `interrotta` | approvato, voci pronte, battute mancanti (`battute_mancanti`, `null` se non calcolabile) | `in_produzione` | Riprendi | 6 |
| `stima_e_consenso` | approvato | `in_produzione` | Vai alla stima | 5 |
| `copione_da_approvare` | copione salvato, non approvato | `da_approvare` | Rivedi e approva | 4 |
| `bozza_copione` | musiche scelte (anche "nessuna") | `bozze` | Riprendi | 4 |
| `bozza_musiche` | cast completo | `bozze` | Riprendi | 3 |
| `bozza_cast` | documento registrato | `bozze` | Riprendi | 2 |
| `bozza_documento` | solo il titolo | `bozze` | Riprendi | 1 |
| `illeggibile` | file della run rotti: la riga c'è, con `errore` | `problemi` | — | — |

- L'elenco viene **solo** dalle sottocartelle di `output/` (niente nascoste, niente link
  simbolici): l'adattatore passa ai comandi solo i `run` di questo elenco.
- Ordinato dal più recente (`aggiornato_il`). `conteggi` per gruppo, sull'intero
  Archivio.
- La ricerca guarda titolo e nome del documento (senza maiuscole né accenti).

## Libreria Documenti

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Elenco | `documents_list.py [--filter mai_usati\|gia_usati\|con_problemi] [--search T]` | no (estrae da solo solo i file di testo, entro ~20 s) | no |
| Un documento, estratto subito | `documents_list.py --name=NOME` | per un PDF lungo, fino a un'estrazione intera | no |
| Carica documento | *adattatore*: salva il file in `documenti/` (vedi fondo) | — | — |
| Anteprima | `doc_extract.py NOME` | no | no |
| Crea episodio | `run_init.py --title T --document NOME` | no | no |

Esempio reale (una riga):

```json
{"nome": "report.txt", "tipo": "TXT", "dimensione_bytes": 59, "caricato_il": "2026-10-07T13:53:15",
 "sha256": "sha256:1d61…4bab", "estraibile": true, "motivo": null, "pagine": null, "parole": 10, "caratteri": 59,
 "usato_in": 3, "episodi": ["2026-10-07_13-53_con-documento", "…"]}
```

- `estraibile: false` con `motivo` (`scansione`, `protetto`, `vuoto`, `illeggibile`,
  `troppo_grande`): è la riga "Testo non estraibile" del wireframe (filtro
  `con_problemi`).
- L'estrazione è in cache in `documenti/.estrazioni/<sha256>.json`: un file sostituito
  con lo stesso nome si ri-estrae. L'elenco estrae da solo **solo i file di testo**
  (entro un budget di tempo) e non aspetta un'estrazione in corso: PDF e DOCX non ancora
  estratti tornano con `in_attesa: true` (campi `null`), e `in_attesa` in cima dice
  quanti sono. La UI li estrae uno alla volta con `--name=NOME` (subito dopo il
  caricamento, o in background), poi richiama l'elenco.
  Gli sha256 sono in cache (`.cache_file.json`, validi finché file, dimensione e data non
  cambiano).
- Una riga che non si riesce a leggere ha `errore` (l'elenco non fallisce).
- `usato_in`/`episodi`: dallo sha256 registrato nelle run; per le run di prima, dal nome.
- `scartati[]`: file con un formato non gestito.

## Libreria Voci

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Elenco | `voices_list.py [--filter clonate\|da_clonare\|consenso_da_confermare] [--search T]` | no | no |
| Nuova voce | *adattatore*: salva la registrazione in `voci_disponibili/`, poi `voice_meta.py --name VOCE --spoken-name "Nome Cognome" --consent --expect-sha256 H --by U` | no | no |
| Registra il consenso | `voice_meta.py --name VOCE --consent --expect-sha256 H --by U` | no | no |

Esempio reale (una voce; omessi `path`, `voice_id`, `cache_reason`, che restano per il subagent):

```json
{"name": "Voce Fabio", "spoken_name": "Fabio Verdi", "name_complete": true, "nome_pronunciato": "Fabio Verdi",
 "nome_confermato": true, "file": "Voce Fabio.wav", "extension": ".wav", "size_mb": 0.13,
 "durata_campione_seconds": 1.5, "cached": false, "stato": "Da clonare", "clonata_il": null,
 "consenso": {"stato": "mancante", "audio_sha256": "sha256:d83f…350f", "da": null, "il": null,
              "revocato_da": null, "revocato_il": null},
 "usata_in": 0, "episodi": []}
```

- "Campione": `durata_campione_seconds`. "Stato": `stato` (`Già clonata` con `clonata_il`,
  `Da clonare`). "Usata in": `usata_in` (episodi prodotti, da `voci.json`).
- `nome_pronunciato` è quello confermato con `voice_meta.py --spoken-name` (senza
  cognome: exit 3 `SpokenNameIncomplete`, salvo `--accept-incomplete-name`); se manca,
  `spoken_name` è solo una proposta dal nome del file. Il passo 2 lo propone come nome
  per il ruolo, senza salvarlo da solo.
- Con `--spoken-name` e `--consent` insieme, un nome incompleto ferma tutto prima del
  consenso. Nome e `--by`: al massimo 100 caratteri.
- Una voce con i metadati rotti compare con `errore` (`stato: "Illeggibile"`). La voce si clona solo alla prima produzione che la usa, dopo la stima.

## Libreria Musiche

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Elenco | `music_list.py [--filter strumentali\|con_parlato\|licenza_mancante] [--search T]` | no | no |
| "Ho la licenza per questo brano" | `music_meta.py --category C --name N --licenza "testo" --by U` (togliere: `--no-licenza`) | no | no |
| Parlato e trascrizione | `music_meta.py --category C --name N --parlato true\|false [--trascrizione "…"] --by U` | no | no |
| Carica brano | *adattatore*: salva il file in `musiche/<categoria>/` | — | — |

Esempio reale (un brano):

```json
{"name": "Piano calmo", "extension": ".wav", "duration_seconds": 0.5, "has_licenza": true,
 "licenza": "CC-BY 4.0, Autore X", "licenza_da": "Utente A", "licenza_il": "2026-10-07T13:53:49",
 "parlato": false, "trascrizione": null, "usable": true, "error": null, "usato_in": 1,
 "episodi": ["2026-10-07_13-53_con-musica"]}
```

- Stesse regole della libreria: un sottofondo non può avere parlato, la trascrizione vale
  solo con parlato (`MusicMetaError`). Trascrizione al massimo 500 caratteri (entra nel
  prompt del copione, su una riga e senza virgolette doppie), licenza 2000, `--by` 100. La licenza non si verifica: si registra chi l'ha
  dichiarata e quando.
- `usato_in`: episodi che hanno scelto il brano (`musiche.json`).
- Le modifiche ai metadati sono atomiche e sotto lock (nessuna modifica persa con più
  utenti).

## Impostazioni

`settings_show.py` → `{"impostazioni": {provider, copione, audio, musica, documenti,
retake, valuta, lezioni, cartelle}}`. Sola lettura, solo una **whitelist** di chiavi
(`src/settings_view.py`): mai chiavi API, nomi di variabili d'ambiente, percorsi assoluti
(le cartelle per nome). Le impostazioni si cambiano in `config.yaml`, non dall'interfaccia.

## Lezioni

| Azione | Comando | Lunga | Costa |
|---|---|---|---|
| Lezioni attive, proposte, rifiutate | `lessons.py --list` | no | no |
| Schemi ricorrenti | `lessons.py --evidence [--last N]` | no | no |
| Proponi lezioni (stima prima) | `lessons.py --propose --dry-run`, poi `lessons.py --propose` | sì | **sì** (API Anthropic) |
| Accetta / rifiuta | `lessons.py --accept ID --by U [--text=…] [--remove-id ID]` / `--reject ID --by U --reason=…` | no | no |
| Revisione, andamento | `lessons.py --review`, `lessons.py --trend` | no | no |

- Il registro delle lezioni è condiviso: accetta, rifiuta, revisione e proposte si
  scrivono sotto lock e in modo atomico (anche `lezioni.md`). `lezioni_md` nel JSON è
  relativo (`prompts/lezioni.md`).
- Una lezione accettata entra nel prompt di **tutti** gli episodi successivi, di tutti
  gli utenti: va riservata a chi amministra il progetto (decisione dell'adattatore). In
  modalità web `--by` è obbligatorio (`InvalidArguments`), e resta nel registro
  (`accettata_da`, `rifiutata_da`).
- Testo di una lezione: al massimo 400 caratteri, nessuna riga che inizi con `#` o `---`;
  `lezioni.md` al massimo 4000 caratteri (`LessonsError`, nulla scritto).
- `--accept` può dare exit 3 `needs_user_input` (serve togliere una lezione: si richiama con
  `--remove-id`).
- Gli errori dell'API in `--propose` hanno gli stessi `error_type` della scrittura del
  copione (§3, "Errori dell'API del modello"): chiave o credito → exit 2, rate limit,
  sovraccarico e rete → exit 1 con `retry_after_seconds`.

## Diagnostica

`doctor.py` (nessun argomento) → `{"ok", "checks": [{"name", "ok", "required", "detail"}],
"ffmpeg_path"}`. Fa eccezione al contratto degli exit code: 0 se tutti i controlli
obbligatori passano, 1 altrimenti, sempre con il JSON. Percorsi relativi al progetto
(`.`) o alla home (`~`), anche su stderr. `doctor.py --web` aggiunge il controllo
obbligatorio `web_mode` (§0): l'adattatore lo lancia all'avvio.

---

# A carico dell'adattatore

I comandi di `tools/` fanno da soli i controlli di dominio e di sicurezza descritti sopra
(percorsi confinati, gate di approvazione, consenso, lock, modalità web). Restano
all'adattatore le cose che i comandi non possono sapere: chi è l'utente, cosa può fare,
come arrivano e come escono i file, come si gestiscono i processi.

## Come lanciare i comandi

- Sempre con `PODCAST_AGENT_WEB=1`, con il python della `.venv` del progetto e la cartella
  del progetto come cartella corrente. Mai una shell: argomenti in lista.
- Mai gli argomenti riservati all'uso locale (§1 "Modalità web"); li rifiuterebbero
  comunque i comandi (`NotAllowedInWebMode`). Mai `voices_prepare.py`, `synthesize.py`,
  `run_finalize.py`, `script_save.py`.
- I valori scritti dall'utente sempre nella forma `--opzione=valore`.
- `--run-dir` sempre con il `run` di un episodio dell'elenco `runs_list.py`; i nomi di
  documenti, voci e brani sempre da quelli dei rispettivi elenchi.
- `--by` sempre con l'utente autenticato, mai con un campo scritto dall'utente.
- Leggere stdout per intero a processo finito (un oggetto JSON); stderr si registra nei log
  o si mostra in un pannello "dettagli" (non contiene percorsi assoluti).
- Ogni `error_type` che non è in §3 si tratta come un errore generico del suo exit code.

## Processi, tempi e coda

| Comando | Durata tipica | Timeout consigliato | Note |
|---|---|---|---|
| elenchi, viste, `*_meta.py`, `music_set.py`, `episode_config.py` (senza documento), `script_edit/approve/versions/show`, `estimate.py`, `run_status.py`, `settings_show.py`, `turn_at.py` | < 2 s | 30 s | `documents_list.py` (senza `--name`) fino a ~25 s: estrae solo file di testo |
| `doc_extract.py`, `run_init.py --document`, `episode_config.py --documento` | secondi | 120 s | estrazioni: limite interno di 60 s fra una pagina e l'altra, ma una pagina non si interrompe da dentro |
| `documents_list.py --name` | secondi | 240 s | un'estrazione intera (fino a ~60 s + una pagina) più l'eventuale attesa di un'estrazione dello stesso file già in corso (fino a 120 s) |
| `waveform.py` (prima volta), `mix.py`, `retake.py --list` | secondi | 15 min | ffmpeg ha un limite interno di 15 minuti |
| `script_write.py`, `script_revise.py` | 1–5 min | 10 min | chiamata al modello: costa |
| `produce.py`, `retake.py --turn` | minuti | nessun timeout duro | costano; si seguono con gli eventi / `run_status.py` |
| `lessons.py --propose` | < 1 min | 5 min | costa |
| `voice_meta.py --confirm-clone-retry` | < 1 s, fino a 10 min | 11 min | aspetta il lock della clonazione di quella voce |

- I comandi lunghi vanno lanciati come **lavori in background** (la coda dei lavori è
  dell'adattatore). Due operazioni lunghe sulla stessa run sono già impedite dai comandi
  (`RunBusy`): la coda può limitarsi a serializzare per run, o a mostrare l'errore.
- Lo stato "in corso" non va tenuto dall'adattatore: si legge da `run_status.py` (o
  `script_show.py` → `in_corso`), che lo derivano dal lock.
- **Interrompi**: un SIGTERM al processo. Non mandarne un secondo mentre l'ultimo evento è
  la preparazione delle voci (`fase_iniziata: preparazione_voci` senza `fase_finita`): la
  clonazione in corso finisce comunque, e un'interruzione forzata a quel punto porta a
  `CloneOutcomeUnknown`. Se un processo va ucciso (SIGKILL), il lock si libera da solo.
- `in_corso` di una revisione del copione contiene `istruzione`, cioè la richiesta scritta
  dall'utente: decidere se mostrarla agli altri utenti della stessa run.

## Caricamenti

Le tre librerie sono cartelle: un caricamento è la scrittura di un file lì dentro. Regole:

- **Nome**: ridotto a un nome semplice (niente `/`, `\`, `..`, caratteri di controllo),
  **mai con il punto iniziale** (in `documenti/` `.estrazioni` e `.cache_file.json` sono
  riservati, in `voci_disponibili/` i file nascosti sono copie temporanee) e **mai con il
  trattino iniziale** (verrebbe letto come un'opzione). In più, gli argomenti
  posizionali si passano sempre dopo `--`: `doc_extract.py -- NOME`.
- **Il nome vale senza l'estensione**: `Sigla.wav` e `Sigla.mp3` sono lo stesso brano,
  `Mario Rossi.wav` e `Mario Rossi.m4a` la stessa voce (il confronto è quello dei
  comandi: minuscole, senza accenti, `_` e `-` come spazi). Un caricamento che collide
  con un nome esistente è una **sostituzione**: richiede la conferma e il file vecchio si
  toglie insieme. Se restano due file con lo stesso nome, i comandi non scelgono: la
  voce o il brano non si usano (errore esplicito, `errore: "Doppione"` negli elenchi).
- **Estensioni ammesse** (le altre si rifiutano al caricamento):
  - `documenti/`: `.pdf`, `.docx`, `.txt`, `.md`, `.markdown`, `.rst`, `.text`;
  - `voci_disponibili/`: quelle di `audio.accepted_extensions` (`.wav`, `.mp3`, `.m4a`,
    `.flac`, `.ogg`, `.opus`, `.webm`). **Mai `.json`, `.lock`, `.tmp`**: un `.json`
    caricato varrebbe come metadati della voce, consenso compreso;
  - `musiche/intro|outro|sottofondo/`: le stesse estensioni audio. **Mai `.json`,
    `.lock`, `.tmp`**: un `.json` caricato varrebbe come metadati del brano, licenza
    compresa.
- **Contenuto**: oltre all'estensione si controlla la **firma** del file (i primi byte:
  `RIFF…WAVE`, `ID3`/frame MP3, `ftyp` per m4a, `fLaC`, `OggS`, `%PDF`, `PK` per DOCX).
  Sulle registrazioni e sui brani gira ffmpeg: un file "mp3" che in realtà è una playlist
  o un `ffconcat` non deve entrare in libreria. (I comandi aprono comunque ogni file con
  `-protocol_whitelist file` e, per le estensioni note, con il formato forzato: un
  `ffconcat` travestito da mp3 non viene letto come playlist.)
- **Dimensione**: limite in streaming, prima di scrivere su disco. Per i documenti lo
  stesso `documents.max_bytes` di `config.yaml` (20 MB); per le registrazioni una soglia
  adatta a 10–30 secondi (es. 20 MB): la registrazione si legge per intero in memoria al
  momento della clonazione; per i brani una soglia ragionevole (es. 50 MB).
- **Scrittura atomica**: in un file temporaneo nella stessa cartella, con nome che
  comincia con `.`, poi rinominato. Niente link simbolici.
- **Nessuna sovrascrittura silenziosa**: un file con lo stesso nome si sostituisce solo
  con una conferma esplicita dell'utente. Effetti di una sostituzione: una voce passa a
  consenso `superato` (va riconfermato) e alla prossima produzione va clonata di nuovo; un
  brano già usato fa uscire `produce.py`/`mix.py` con `MusicChangedError` finché non si
  conferma `--accept-changed-music`; un documento si ri-estrae.
- **Dopo il caricamento** di una voce: `voice_meta.py --name … --spoken-name=… --consent
  --expect-sha256=… --by=…` (lo sha256 è quello che l'utente ha visto, da `voices_list.py`
  o `voice_meta.py`). Durata consigliata 10–30 s: `voices_list.py` la riporta
  (`durata_campione_seconds`), l'adattatore può avvisare.
- **Cancellazione** (non c'è un comando): una voce si cancella **insieme** al suo `.json`
  (e sparisce il consenso); un file non si cancella mentre una run che lo usa è
  `in_corso`. Cancellare un episodio vuol dire cancellare la sua cartella in `output/`,
  solo se `run_status.py` non dice `in_corso`.

## Download

- Si servono solo: `file_finale.percorso` di `episode_info.py`, i file di `file[]` con
  `interno: false` (`episodio.json` e `musiche_usate.json`, che hanno percorsi del server,
  non sono in `file[]`), le anteprime dei take (`preview`, `preview_with_music`), i
  documenti della libreria (per l'anteprima).
- I percorsi sono relativi (`output/<run>/…`): vanno risolti dentro la cartella
  `output/<run>` del run richiesto, rifiutando `..` e link simbolici.
- Un documento della libreria si serve per **nome** (uno di `documents_list.py`), risolto
  dentro `documenti/`: niente nomi con il punto iniziale, niente link simbolici.

## Autorizzazioni

Nessun comando sa chi è l'utente: queste decisioni sono dell'adattatore.

| Azione | Perché conta |
|---|---|
| Registrare o **revocare** il consenso di una voce | la revoca blocca ogni produzione, ogni retake e ogni rimontaggio con quella voce |
| `--confirm-clone-retry` | autorizza una spesa che potrebbe duplicare una voce sul provider |
| Cambiare il nome pronunciato di una voce | lo pronuncia il presentatore negli episodi di tutti |
| Dichiarare la licenza di un brano, scriverne la trascrizione | la trascrizione entra nei prompt del copione degli altri |
| Accettare o rifiutare una lezione | una lezione accettata entra nel prompt di **tutti** gli episodi |
| `produce.py`, `retake.py --turn`, `script_write.py`, `script_revise.py`, `lessons.py --propose` | costano |
| `--accept-changed-music` (`produce.py`, `mix.py`) | conferma di usare un brano sostituito dopo un montaggio |
| `--accept-incomplete-name` (`voice_meta.py`), `--accetta-nome-incompleto` (`episode_config.py`, passo 2) | conferma un nome da pronunciare senza cognome |
| Vedere le run degli altri | `runs_list.py` elenca tutte le run di `output/` |

I **flag di conferma** (`--accept-changed-music`, `--accept-incomplete-name`,
`--accetta-nome-incompleto`, `--confirm-clone-retry`) si passano **solo** dopo l'esito 3
che li chiede e dopo che l'utente ha risposto, mai d'ufficio.

## Ambiente

- `PODCAST_AGENT_WEB=1` in **ogni** sottoprocesso, verificato all'avvio con
  `doctor.py --web` (§0).
- Chiavi in `.env` del progetto (`FISH_AUDIO_API_KEY`, `ANTHROPIC_API_KEY`): mai passate
  sulla riga di comando.
- `config.yaml` si modifica solo da chi amministra il progetto (`settings_show.py` è in
  sola lettura). Cambiare il provider o i parametri di sintesi rende "da generare" le
  battute già in cache.
- `doctor.py` dopo ogni installazione o aggiornamento; `doctor.py --web` a ogni avvio dell'adattatore.

---

# Non supportato

| Cosa | Dove nel wireframe | Perché |
|---|---|---|
| "Altre indicazioni, con parole tue" nei retake | passo 7 | nessun testo libero arriva al sintetizzatore fuori dal copione approvato (decisione D1): solo le indicazioni della whitelist |
| Formati monologo e tavola rotonda | passo 2 | il web supporta conversazione e intervista con presentatore (D4); gli altri restano in `run.py` |
| Consenso per singolo episodio | passo 5 | il consenso vale per la voce e per la sua registrazione (D11) |
| Modificare le impostazioni | Impostazioni | `config.yaml` è dell'amministratore; lo schermo è in sola lettura |
| Rinominare il file dell'episodio cambiando il titolo | passo 7 | il nome resta quello del primo montaggio (G7), così i link non si rompono |
| Cancellare episodi, documenti, voci, brani con un comando | menu "⋯" delle librerie | non c'è un comando: se l'adattatore lo fa, regole in "Caricamenti" |
| Pronuncia diversa come retake | passo 7 | si corregge il testo ("Cosa dice"), con una nuova approvazione |

