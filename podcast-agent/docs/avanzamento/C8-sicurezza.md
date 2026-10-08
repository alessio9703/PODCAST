# Revisione di sicurezza · C8

Intervallo esaminato: `main..rifinitura` (`e67f7b0..87f694c`), nessuna modifica non committata.
File esaminati: 16 (più, per contesto, `src/errors.py`, `src/redaction.py`, `src/production.py`,
`src/voice_prep.py`, `src/music_mixer.py`, `src/run_state.py`, `src/config.py`). Data: 2026-10-08.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 0 |
| Bassa | 4 |

N28 non cambia l'esito di nessuna eccezione che il codice oggi solleva davvero. Ho
confrontato `_classify` con `_stable(_classify(...))` per tutte le classi d'errore di `src/` e
`tools/`, istanziandole in una copia nello scratchpad: l'exit code cambia solo per le classi
base o interne che nessuno solleva senza `error_type` (`DomainError`, `NeedsUserDecision`,
`ModelAccessError`, `ModelTemporarilyUnavailable`, `NeedsRemoval`, `SynthesisStopped`). Tutte
le classi a esito 2 (provider, `ModelAuthError`, `ModelCreditExhausted`) e a esito 3 restano
come prima. Le rinomine non toccano percorsi che prendono decisioni su consenso, approvazione o
retry. Il fermo del provider finto non è raggiungibile in produzione. Restano quattro problemi
di difesa in profondità e di coerenza.

## Problemi

### [BASSA-1] Il ripiego di `_stable` ignora l'exit code della classificazione: un esito 2 o 3 non elencato diventa 1
- **Dove**: `podcast-agent/tools/_common.py:311-326` (`_stable`), chiamata a `:367`.
- **Voce della checklist**: 8. Garanzie di dominio (esito 2 "non ritentare", esito 3 "chiedi
  all'utente").
- **Scenario**: oggi non succede, perché tutte le sottoclassi di `VoiceProviderError` e di
  `NeedsUserDecision` sono nell'elenco. Succede se domani si aggiunge una sottoclasse senza
  metterla in `CATALOG`, oppure se la si mette per sbaglio in `INTERNAL_EXCEPTIONS`
  (`tests/test_web_integration.py:3984`). Esempi: una `ProviderVoiceLimitError(VoiceProviderError)`
  nel provider, oppure una `NeedsUserDecision(..., error_type="NomeNuovo")`.
  `_classify` restituisce correttamente 2 o 3. `_stable` però butta via quel codice e
  restituisce `InternalError`, exit 1, che il contratto §2 legge come "correggibile".
  L'adattatore propone "Riprova" su un errore del provider, oppure non pone la domanda
  all'utente. Per la clonazione il danno è contenuto: il marcatore `CloneOutcomeUnknown` resta
  (`src/voice_prep.py:748-765`) e il secondo tentativo esce con 3. Per la sintesi e per le
  domande all'utente non c'è un'altra rete. Il controllo "ESITI" del test guarda solo le classi
  che sono **già** nell'elenco: una classe a esito 2 o 3 messa fra le interne passa.
- **Proposta di correzione**: in `_stable`, quando `kind` non è nell'elenco, scegliere il nome
  di ripiego in base al codice di `_classify`, non solo al tipo Python:
  - `code == EXIT_PROVIDER`: `VoiceProviderError` (o un `ExternalServiceError` nuovo, con exit 2);
  - `code == EXIT_NEEDS_USER`: mantenere 3 con un nome generico nuovo nell'elenco
    (es. `UserDecisionRequired`, exit 3);
  - solo con `code == 1` usare `InvalidInput`, `NotFound` o `InternalError`.

  In aggiunta, nel test: nessuna classe in `INTERNAL_EXCEPTIONS` può derivare da
  `VoiceProviderError` o da `NeedsUserDecision`, salvo la base stessa.

### [BASSA-2] L'evento `errore` di `produce.py` porta ancora il nome grezzo della classe, diverso dal JSON finale
- **Dove**: `podcast-agent/src/production.py:716-719`. Il contratto dice il contrario in
  `docs/CONTRATTO_INTERFACCIA.md:983` ("Lo stesso errore del JSON finale") e in §3 ("non arriva
  mai con il nome di una classe Python").
- **Voce della checklist**: 8 (coerenza degli esiti usati dalla UI); 6 solo per la forma:
  il testo è comunque ripulito con `redact`.
- **Scenario**: dopo `iniziata` il brano dell'intro viene sostituito mentre la sintesi è in
  corso. Al mix, `render` richiama `preflight` (`src/music_mixer.py:246`), che solleva
  `MusicChangedError`. In `_eventi.jsonl` finisce `{"evento":"errore","error_type":"MusicChangedError"}`,
  mentre il JSON su stdout dice `MusicChanged`, exit 3. Lo stesso succede per qualunque
  eccezione imprevista dopo `iniziata` (`OSError`, `KeyError`, …): l'evento porta il nome
  Python, lo stdout `InternalError`. La schermata "Generazione" che segue gli eventi non
  riconosce il nome e, nell'evento, non ha l'exit code per ripiegare. Il JSON finale resta
  corretto e decide l'esito, quindi nessuna garanzia cade.
- **Proposta di correzione**: spostare la normalizzazione in `src/`, per esempio una funzione
  `stable_kind(exc) -> tuple[int, str]` in `src/error_catalog.py` che `_common._stable` usa a
  sua volta. A `production.py:718` usare `stable_kind(exc)[1]` invece di
  `getattr(exc, "kind", None) or type(exc).__name__`. Aggiungere un test: un brano sostituito
  dopo `iniziata` dà lo stesso `error_type` nell'evento e nel JSON.

### [BASSA-3] `InternalError` nasconde il nome della classe anche su stderr e nei log
- **Dove**: `podcast-agent/tools/_common.py:368` (`note(f"ERRORE [{kind}] {exc}")` con `kind` già
  ripiegato in `InternalError`).
- **Voce della checklist**: 6 / robustezza (diagnostica).
- **Scenario**: una clonazione fallisce con un'eccezione non avvolta dal provider, per esempio
  un errore di `requests` sfuggito al wrapping o un `OSError` sullo snapshot. JSON e stderr
  dicono solo `InternalError` e il testo. Per `KeyError` il testo è solo `'campo'`. Il resoconto
  C8 (§3) dice che "per il debug serve lo stderr", ma su stderr il nome della classe non c'è più.
  Proprio nel caso più delicato (esito incerto di una spesa) l'amministratore perde
  l'informazione più utile. Non c'è fuga di dati, solo diagnostica più povera.
- **Proposta di correzione**: per i soli ripieghi (`InternalError`, `InvalidInput`, `NotFound`)
  scrivere su stderr anche `type(exc).__name__`, per esempio
  `note(f"ERRORE [{kind}] ({type(exc).__name__}) {exc}")`. Il nome di una classe non è un dato
  sensibile e stderr passa comunque da `_redact`. Il JSON resta com'è.

### [BASSA-4] Il contratto indica ancora `InvalidArguments` per un `--by` mancante in `lessons.py`
- **Dove**: `podcast-agent/docs/CONTRATTO_INTERFACCIA.md:1331` ("In modalità web `--by` è
  obbligatorio (`InvalidArguments`)"). Il codice ora dà `ByRequired`
  (`tools/lessons.py:240`).
- **Voce della checklist**: 8 (le lezioni entrano nel prompt di tutti gli episodi; la firma è
  la difesa della revisione C4, MEDIA-2).
- **Scenario**: un adattatore scritto seguendo quella riga e che riconosce solo
  `InvalidArguments` tratta `ByRequired` come errore generico, exit 1. Il comando rifiuta
  comunque e non scrive niente, quindi nessuna garanzia cade: è un disallineamento della
  documentazione, che però contraddice la tabella §3 e la tabella "Rinominati in C8".
- **Proposta di correzione**: sostituire con `(ByRequired, details.campo: "by")`. Il test
  `test_c8_error_catalog` controlla solo la tabella §3: si può aggiungere un controllo che i
  nomi rinominati (colonna "Prima") non compaiano più nel contratto fuori da quella tabella.

## Verificato e a posto

- **1. Path traversal**: C8 non aggiunge input né percorsi. `--run-dir` di `script_save`
  continua a passare da `run_dir_arg`, `--script-json` da `file_arg`, e `script_save.py` resta
  vietato in modalità web.
- **2. Sottoprocessi**: nessun sottoprocesso nuovo o cambiato nel diff.
- **3. File non fidati**: nessun cambiamento all'estrazione dei documenti né ai limiti.
- **4. Prompt injection**: nessun testo nuovo verso il modello. Gli `errors` di `ScriptInvalid`
  vanno solo nel JSON d'errore, non in un prompt.
- **5. Tag dei retake**: nessun cambiamento. `UnknownDirection`, `DirectionNeedsTextEdit` e
  `ConflictingDirections` restano exit 1, con gli stessi nomi.
- **6. Segreti e percorsi nei `details`**: `_emit_error` passa sia `error` sia `details` da
  `_redact` (`tools/_common.py:345-348`). Quindi `ScriptInvalid.details.errors` e
  `TimelineError.details.code` (una stringa fissa del codice) sono ripuliti, e anche il testo
  di `InternalError`. Il messaggio di un'eccezione imprevista è lo stesso di prima di C8: è
  cambiato solo il nome. Nessuna chiave entra nei messaggi nuovi. Le chiavi del modello
  restano classificate da `script_writer` prima del bordo.
- **7. Lock e temporanei**: nessun cambiamento in `src/fileio.py` né nei lock. Il fermo del
  provider finto usa un file scelto dal test, con un'attesa di al massimo 120 s.
- **8. Garanzie di dominio**:
  - **Exit code dall'elenco**: per ogni classe sollevata davvero, `_stable` restituisce lo
    stesso exit di `_classify`. Ho controllato tutte le classi di `src/` e `tools/` con uno
    script nello scratchpad.
  - **Esito 2**: `VoiceProviderError` e le sue 5 sottoclassi, `ModelAuthError` e
    `ModelCreditExhausted` sono a 2 nell'elenco.
  - **Esito 3**: `ConsentRequired`, `CloneOutcomeUnknown`, `RecordingChanged`,
    `RevokedMeanwhile`, `SpokenNameIncomplete`, `AmbiguousName`, `MusicChanged` e
    `LessonRemovalRequired` sono a 3. Nessun `NeedsUserDecision` è sollevato senza
    `error_type` o con un nome fuori elenco.
  - **Nessun retry sulla clonazione**: non dipende dall'exit code. Qualunque eccezione dentro
    `provider.clone_voice` lascia il marcatore, e il tentativo successivo esce con
    `CloneOutcomeUnknown` (3) prima di spendere (`src/voice_prep.py:736-766`).
  - **Rinomine e `--by`**: `--confirm-clone-retry`, il consenso (`_require_by` solleva sempre
    `VoiceMetaError`; cambia solo `error_type`, quindi gli `except VoiceMetaError` di
    `voice_prep.py:356,463` e `voice_meta.py:141` si comportano come prima), `music_meta` e
    `lessons --accept/--reject` rifiutano **prima** di scrivere, come prima.
  - **Nessun codice confronta i nomi vecchi**: nessun punto di `src/`, `tools/` o `run.py`
    confronta un `error_type` per stringa o cattura `InvalidArguments` o `NeedsUserInput`.
    `run.py` cattura per classe (`DomainError`, `NeedsUserDecision`), e le classi sono le
    stesse. Nelle istruzioni del subagent i nomi vecchi non compaiono; la regola "exit 2 non
    ritentare" è stata solo allargata.
  - **`require_by_in_web`**: da `InvalidArguments` a `DomainError(error_type="ByRequired")`.
    Nessuno catturava `InvalidArguments` per proseguire.
- **Fermo del provider finto** (`tests/fake_provider.py`): vive solo in `tests/`. Nessun
  riferimento in `src/`, `tools/`, `run.py` o `config.yaml`. Un provider si sceglie solo con
  `providers.<nome>.class` in `config.yaml` (amministratore) o con `--config`, che è vietato in
  modalità web. Le opzioni `hold_file`/`hold_after` esistono solo nel costruttore della classe
  finta. Il provider vero non ha niente di simile.

## Da verificare nelle prossime fasi

- **N30**: quando i `raise ValueError`/`FileNotFoundError` rimasti nei tool diventano nomi
  precisi (es. `RunNotFound`), vanno aggiunti all'elenco con l'exit giusto. Vale la stessa
  regola di BASSA-1: un nome dimenticato non deve far perdere un esito 2 o 3.
- **Errori non avvolti del provider vero**: un'eccezione di `requests` o un `OSError` dentro
  `clone_voice` o `tts` esce come `InternalError`, exit 1, e non come esito 2. Per la
  clonazione il marcatore protegge; per la sintesi un rilancio paga solo le battute mancanti.
  Vale la pena verificare in `src/providers/fish_audio_provider.py` che ogni eccezione di rete
  sia tradotta in una sottoclasse di `VoiceProviderError`. La situazione è precedente a C8,
  che ha cambiato solo il nome.
- Se in futuro l'adattatore leggerà lo stato finale dagli eventi (ripresa dopo un riavvio del
  server), BASSA-2 diventa rilevante: va chiusa prima.
