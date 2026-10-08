# Revisione di sicurezza · C7 (seconda tornata)

Intervallo esaminato: `269b7a6..dff2236` (correzioni di ALTA-1, MEDIA-1, MEDIA-2 in `06acc06`
e seconda tornata in `dff2236`; nessuna modifica non committata)
File esaminati: 14 (codice: `run.py`, `src/registry.py`, `src/voice_prep.py`,
`src/voice_library.py`, `src/music_library.py`, `tools/voice_meta.py`; più test e documenti).
Data: 2026-10-08.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 4 |
| Bassa | 5 |

Le correzioni chiudono bene gli scenari esatti di ALTA-1, MEDIA-2, BASSA-3 e BASSA-4 del
primo rapporto. Non ci sono deadlock (nessun lock preso in ordine inverso) e non ci sono
regressioni sul gate sha256, sul divieto di retry, sui nomi esatti, sullo sha256 delle
musiche o sulla cache.

Restano quattro buchi, tutti riprodotti con script nello scratchpad (`sec2/s1.py`…`s4.py`):
- la revoca **non** vince quando arriva prima che parta il processo del consenso: è il
  caso realistico, quello di una pagina o di un prompt di `run.py` aperti da minuti;
- uno `__storico__` malformato non ferma stima e piano, ma solo `record()`, cioè **dopo** la
  clonazione pagata;
- la revoca di una voce cancellata è impossibile se in libreria c'è un nome simile;
- un voice_id clonato mentre arriva la revoca non finisce nella revoca.

## Problemi

### [MEDIA-1] La revoca non vince se arriva prima che il consenso parta (vista vecchia): il consenso la cancella
- **Dove**:
  - `src/voice_prep.py:364-374` (`grant_consent`): `before` si calcola all'avvio del
    processo, non quando l'utente ha visto lo stato;
  - `run.py:383-400`: piano calcolato prima di `input()`, `grant_consent` dopo;
  - `tools/voice_meta.py:115-116`.
- **Voce della checklist**: 8. Garanzie di dominio (consenso/revoca); 7. Lock (TOCTOU).
- **Scenario** (riprodotto, `sec2/s1.py`):
  1. La UI (o `run.py`, passo 6) mostra Mario Rossi con consenso `mancante`, sha256 H.
  2. Un altro utente revoca (`voice_meta.py --revoke-consent`): metadati `revocato`, revoca
     per H nel registro.
  3. Il primo utente conferma: `voice_meta.py --consent --expect-sha256 H --by Operatore`.
     `_revocation_marks` prima e dopo il lock vede già la revoca: i due valori coincidono,
     quindi nessun `RevokedMeanwhile`. `record_consent` cancella `consenso_revocato` e
     `lift_revocations` toglie la revoca per H. Esito: exit 0, stato `registrato`,
     `__revoche__` assente.

  Il controllo copre solo i millisecondi di attesa sul lock. In `run.py` la finestra è
  tutto il tempo del prompt `input()` (minuti), dal web il tempo fra il caricamento della
  pagina e il clic. Chi conferma non ha mai visto la revoca, che sparisce in silenzio: la
  voce torna clonabile e riusabile. È proprio il caso che la decisione dell'utente
  ("vince SEMPRE la revoca") voleva coprire. Il test `test_c7_followup` codifica come
  corretto ("un consenso dato DOPO la revoca vale") un caso indistinguibile da questo.
  Gravità media e non alta: un operatore afferma esplicitamente il consenso, e servono due
  utenti sulla stessa voce nella stessa finestra.
- **Proposta di correzione**:
  - legare il consenso allo **stato visto**, come già si fa per lo sha256. Nuovo argomento
    `grant_consent(..., expect_revoked: str | None)`, cioè il `revocato_il` che l'utente ha
    visto (None = "non era revocata"). Dentro il lock di `_consent_lock_target` si
    ricalcola la revoca attuale: `consenso_revocato.il` dei metadati, più
    `revocation_for(audio_hash=H)` e le revoche con `voce == name` nel registro. Se c'è una
    revoca che non coincide con quella attesa → `RevokedMeanwhile`;
  - in `voice_meta.py` aggiungere `--expect-revoked-il` (obbligatorio dal web quando la UI
    mostrava `revocato`, assente altrimenti);
  - in `run.py:400` passare `plan.consent.get("revocato_il")` (None, perché le revocate
    falliscono prima);
  - test: revoca completa **prima** di `voice_meta --consent` con la vista vecchia →
    exit 3 `RevokedMeanwhile`, revoca ancora presente.

### [MEDIA-2] `__storico__` malformato: stima e consenso passano, la produzione clona (a pagamento) e poi perde il voice_id
- **Dove**:
  - `src/registry.py:253-256` (`record()` chiama `self.history()` **dopo**
    `provider.clone_voice`, `src/voice_prep.py:646-647`);
  - `src/registry.py:177-196`: lo storico si valida solo quando qualcuno lo legge.
    `plan_voices` e `with_registry` leggono solo `__revoche__`.
- **Voce della checklist**: 8. Garanzie di dominio (spesa, nessun retry, "registro
  malformato → ci si ferma").
- **Scenario** (riprodotto, `sec2/s2.py`):
  - `voices_registry.json` contiene `"__storico__": {"x": 1}`. Può essere modificato a
    mano, oppure provenire da un registro con una voce davvero chiamata `__storico__`,
    che `record()` accettava fino a `269b7a6`;
  - `estimate.py`: exit 0;
  - `produce.py`: `clone_voice("Mario Rossi")` viene chiamata (registrata in
    `clonazioni.log`), poi `record()` → exit 1 `RegistryCorrupt`. Il voice_id appena
    pagato è perso e il marcatore `.clonazione-*.json` resta;
  - nuovo `produce.py`: exit 3 `CloneOutcomeUnknown`. Ogni `--confirm-clone-retry` paga
    un'altra clonazione che finisce allo stesso modo.

  `C7.md` §7 dice che stima, produzione e consenso si fermano: vale solo per
  `__revoche__`. È lo stesso schema del MEDIA-2 del primo rapporto, spostato sull'altra
  chiave riservata.
- **Proposta di correzione**:
  - validare **entrambe** le chiavi riservate in `VoiceRegistry._load()`, con la logica
    di `_reserved_list` applicata a `REVOCATIONS_KEY` e `HISTORY_KEY`, così ogni lettura
    del registro fallisce subito;
  - in alternativa chiamare `registry.history()` in `plan_voices` e, dentro il lock di
    clonazione, subito dopo `registry.reload()` e prima di `clone_voice`;
  - test: storico malformato → `estimate` exit 1 e `produce` exit 1 con
    `clone_attempts == []`.

### [MEDIA-3] La revoca di una voce già cancellata è impossibile se in libreria c'è un nome simile
- **Dove**: `tools/voice_meta.py:81-89` (si ripiega su `revoke_missing_voice` solo per
  `VoiceLibraryError`), `src/voice_prep.py:154-175` (`resolve_library_voice` solleva
  `AmbiguousName` se trova candidati per sottostringa o `difflib`).
- **Voce della checklist**: 8. Garanzie di dominio (revoche, chiusura di MEDIA-1 del
  primo rapporto).
- **Scenario** (riprodotto, `sec2/s3.py`):
  - `Mario Rossi` clonata (`fake-0001`), poi audio e `.json` cancellati;
  - se in libreria c'è `Maria Rossi.wav` (o `Mario Rossini.wav`),
    `voice_meta.py --name "Mario Rossi" --revoke-consent --by Persona` esce con exit 3
    `AmbiguousName`, candidato `Maria Rossi`. Nessuna revoca scritta, anche se il
    registro conosce esattamente "Mario Rossi";
  - la UI può solo proporre il candidato, cioè revocare **un'altra persona**;
  - il voice_id resta senza revoca. Se poi si carica una nuova `Mario Rossi.wav` con
    consenso, il retake delle run vecchie torna a usarlo (lo scenario di MEDIA-1 del primo
    rapporto).

  In una libreria con molti nomi italiani simili il caso è frequente. Il contratto (§
  "Revoche nel registro") promette che la revoca "funziona lo stesso per il nome esatto
  che il registro conosce".
- **Proposta di correzione**: in `voice_meta.py`, con `--revoke-consent`, controllare
  **prima** di `resolve_library_voice`. Se il registro conosce esattamente `args.name`
  (`registry.knows`) e nessun file della libreria ha `voice.name == args.name` (confronto
  esatto, non normalizzato), andare direttamente a `revoke_missing_voice`. In alternativa
  aggiungere un flag esplicito `--revoke-missing` che salta la risoluzione in libreria.
  Test con `Maria Rossi.wav` presente.

### [MEDIA-4] Un voice_id clonato mentre arriva la revoca non entra nella revoca e torna usabile dopo un ricaricamento
- **Dove**:
  - `src/voice_prep.py:399-405` e `:419-426`: la revoca fotografa `clones_of` e le run
    al momento della revoca;
  - `src/voice_prep.py:588-647`: la clonazione non prende il lock del consenso, e
    `record()` scrive il voice_id dopo;
  - `src/registry.py:196-204` (`revocation_for(voice_id=…)` guarda solo le liste
    `voice_id` delle revoche);
  - `src/voice_prep.py:317-322` (`check_not_revoked`).
- **Voce della checklist**: 7. Lock (race); 8. Garanzie di dominio (revoche).
- **Scenario** (simulato in ordine, `sec2/s4.py`):
  1. `produce.py` ha letto il campione B e il consenso è valido: `clone_voice` è in
     corso.
  2. La persona revoca. `revoke_voice_consent` scrive la revoca per B con
     `voice_id: []`, perché Y non è ancora nel registro.
  3. La clonazione finisce: `record()` scrive B → `Y_ID` e `voci.json` della run contiene
     `Y_ID` (`production.py:792`). La produzione si ferma alla prima battuta, ma Y esiste.
  4. Si cancellano audio e `.json` (procedura del README), si carica una nuova
     `Mario Rossi.wav` C e si registra il consenso.
  5. Retake dal web su quella run: `check_not_revoked(voice_map={"Mario Rossi": "Y_ID"},
     require_consent=True)` **passa**. Si sintetizza con una voce clonata da una
     registrazione revocata.

  Serve una revoca durante una clonazione (secondi) più un ricaricamento, quindi gravità
  media.
- **Proposta di correzione**: rendere la revoca per registrazione valida anche per i
  voice_id clonati **dopo**. Due modi, meglio entrambi:
  - in `VoiceRegistry.record()`, dentro il lock del registro: se esiste una revoca con
    `audio_sha256 == entry.audio_hash`, aggiungere `voice_id` alla sua lista, nella stessa
    scrittura dello storico;
  - in `revocation_for(voice_id=V)`: se V compare in `__storico__` con hash H e c'è una
    revoca per H, restituire quella revoca.

  Test con l'ordine dello scenario.

### [BASSA-1] Le voci delle revoche e dello storico non sono validate campo per campo
- **Dove**: `src/registry.py:177-188` (si controlla solo "lista di dict"), `:196-204`.
- **Voce della checklist**: 8. Garanzie di dominio (fail-open).
- **Scenario**:
  - una revoca nel formato del primo C7 (`269b7a6`, `audio_sha256` come lista) o
    modificata a mano supera `_reserved_list`, ma `audio_hash == item["audio_sha256"]` non
    è mai vero: la revoca per hash si ignora in silenzio (resta solo quella per
    voice_id);
  - un `voice_id` stringa invece di lista fa un confronto per sottostringa
    (`"abc" in "abc123"`), che blocca troppo.

  Secondo `C7.md` §7.3 il registro reale non contiene revoche, quindi oggi non ci sono
  effetti.
- **Proposta di correzione**: in `_reserved_list` richiedere per le revoche `voce: str`,
  `audio_sha256: str | None` e `voice_id: list[str]`, e per lo storico
  `voice_id: str` e `audio_sha256: str | None`. Altrimenti `RegistryCorrupt`.

### [BASSA-2] `_read_sample`: su Windows legge in modalità testo; rifiuta cartelle delle voci collegate; nessun controllo `S_ISREG`
- **Dove**: `src/voice_prep.py:448-472`.
- **Voce della checklist**: 1. Path traversal (robustezza); 3. File non fidati.
- **Scenario**:
  - **Windows**: `os.open` senza `os.O_BINARY` apre il descrittore in modalità testo del
    CRT. CRLF diventa LF e il byte 0x1A tronca la lettura. Lo sha256 dei byte letti non
    coincide più con quello del consenso (`file_hash` legge in binario), quindi stato
    `superato` → `ConsentRequired`: su Windows nessuna voce si clona più. Fallisce in modo
    sicuro, ma è una regressione. Da confermare su una macchina Windows; il ramo
    `os.name == "nt"` in `fileio.py` indica che la piattaforma è considerata.
  - **Cartella collegata**: un `voci_disponibili` che è un link (per esempio a un volume
    montato) passa la scansione e la stima ("da clonare"), poi alla produzione
    `path.parent.is_symlink()` rifiuta. La run si blocca dopo l'approvazione.
  - **FIFO**: un file sostituito con una FIFO fra scansione e lettura blocca `os.open` per
    sempre, tenendo il lock di clonazione. Serve accesso al filesystem.
  - Copie con hardlink fatte da backup e ripristini (`cp -al`, `rsync --link-dest`)
    vengono rifiutate. È voluto, ma va documentato.
- **Proposta di correzione**:
  - `flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)`;
  - dopo `fstat`, `if not stat.S_ISREG(info.st_mode): raise VoiceLibraryError`;
  - decidere con l'utente se una cartella delle voci collegata va ammessa (confrontando
    solo `resolve()`). In ogni caso fare lo stesso controllo in `VoiceLibrary.scan` (o in
    `plan_voices`), così l'errore arriva alla stima e non dopo l'approvazione.

### [BASSA-3] `_clones_in_runs` legge solo `voci_usate.json`, non gestisce i JSON patologici e prende l'hash dal registro di fine run
- **Dove**: `src/voice_prep.py:378-396`, `src/run_layout.py:69` (`audio_hash` preso dal
  registro al momento della chiusura).
- **Voce della checklist**: 8. Garanzie di dominio (revoche); 3. File non fidati.
- **Scenario**:
  - una run interrotta (revoca, stop, approvazione ritirata) ha `voci.json` ma non
    `voci_usate.json`, che si scrive solo alla chiusura. I suoi voice_id clonati prima
    dello storico non entrano nella revoca, ma il retake usa proprio `voci.json`;
  - un `voci_usate.json` molto annidato solleva `RecursionError`, che non è fra le
    eccezioni catturate. `revoke_voice_consent` esce dopo aver scritto i metadati, senza
    revoca nel registro, e così ogni revoca successiva finché il file resta. Serve
    scrittura in `output/`;
  - un file enorme viene letto tutto in memoria. Anche qui serve scrittura in `output/`;
  - se la registrazione viene riclonata **durante** una produzione, `voci_usate.json`
    accoppia il voice_id vecchio con l'hash nuovo. Per le clonazioni registrate dopo C7
    lo storico compensa; per quelle precedenti, un consenso sul nuovo hash toglierebbe
    la revoca del voice_id vecchio.
- **Proposta di correzione**:
  - leggere anche `voci.json` (speaker → voice_id, hash `None`);
  - aggiungere `RecursionError` alle eccezioni catturate e saltare i file oltre una
    dimensione massima (per esempio 1 MB);
  - in `write_voices_used`, prendere l'hash dai risultati di `prepare_voices` (hash dei
    byte clonati o riusati) invece che dal registro al momento della chiusura.

### [BASSA-4] `run.py`: `RegistryCorrupt` al passo 6 esce con un traceback
- **Dove**: `run.py:369-372` (si catturano solo `VoiceLibraryError` e `VoiceMetaError`;
  `RegistryCorrupt` è un `DomainError`).
- **Voce della checklist**: 8 (robustezza dei messaggi).
- **Scenario**: con un registro con revoche malformate, `run.py` si ferma con uno stack
  trace invece del messaggio guidato. Non si spende niente (fallisce in modo sicuro).
- **Proposta di correzione**: catturare anche `DomainError` in quel `except` e chiamare
  `fail(str(exc))`.

### [BASSA-5] Dal web un brano con un nome numerico non si può scegliere
- **Dove**: `src/music_library.py:258-262`.
- **Voce della checklist**: 1. Nomi da input (correttezza della risoluzione).
- **Scenario**: `musiche/intro/2024.mp3` dal web dà `--intro=2024` → `(None, [])` →
  `MusicChoiceError`. Il brano esiste ma non è selezionabile. Non ci sono problemi di
  sicurezza, perché ci si ferma.
- **Proposta di correzione**: in modalità web, per una query di sole cifre, provare la
  corrispondenza esatta del nome (`_normalize(t.name) == normalized`) e saltare solo
  l'indice. In alternativa documentare il limite nel contratto. Per coerenza lo stesso
  vale per `resolve_library_voice`, dove però il comportamento è precedente a C7.

## Verificato e a posto
- **Chiusura del primo rapporto**:
  - **ALTA-1**: revoche una per registrazione, `lift_revocations` toglie solo
    `voce == name and audio_sha256 == H`, le revoche con hash `None` non si tolgono mai.
    La sequenza A → X, sostituzione con B, revoca, consenso su B lascia X revocato (test
    `test_c7_security_fixes`, `test_c7_followup`, `test_c7_run_py`: verdi, rieseguiti
    singolarmente);
  - **MEDIA-1**: storico scritto da `record()` sotto lo stesso lock; revoca dallo storico
    e dalle run; `revoke_missing_voice` per il nome esatto. Restano i limiti di MEDIA-3,
    MEDIA-4 e BASSA-3;
  - **MEDIA-2**: `__revoche__`/`__storico__` scartati in `scan` (con `strip`), prima di
    ogni spesa;
  - **BASSA-1**: corretta solo per l'attesa sul lock (vedi MEDIA-1);
  - **BASSA-2**: corretta per `__revoche__`, non per `__storico__` prima della spesa
    (MEDIA-2);
  - **BASSA-3**: `O_NOFOLLOW`, `st_nlink` sul descrittore e cartella diretta (vedi
    BASSA-2);
  - **BASSA-4**: `should_stop()` ricontrollato dentro il lock di clonazione, prima del
    `reload` e della spesa.
- **Interleaving della revoca coperti**:
  - consenso in attesa sul lock mentre si revoca → `RevokedMeanwhile`;
  - revoca in attesa mentre si registra un consenso → alla fine revocata;
  - `revoke_missing_voice` cambia le revoche con `voce == name`, quindi
    `_revocation_marks` la vede;
  - `record_consent` e `lift_revocations` hanno un solo chiamante fuori dai test,
    `grant_consent` (usato anche da `run.py`), e nessun percorso li chiama senza lock;
  - una revoca con un altro nome sulla stessa registrazione non si toglie (`lift`
    filtra per nome, `revocation_for` per hash).
- **Deadlock**:
  - ordini: consenso → metadati → registro (consenso e revoca); consenso → registro
    (`revoke_missing_voice`); clonazione → registro (`prepare_voices`);
  - nessun percorso prende il lock del consenso o della clonazione tenendo il lock del
    registro o dei metadati, e nessun lock viene ripreso nello stesso processo;
  - i `.consenso-*.lock` sono ignorati da git (`*.lock`).
- **`RegistryCorrupt` e stati a metà**:
  - revoca con storico rotto: metadati già revocati e registro intatto (si sbaglia verso
    la revoca);
  - consenso con revoche rotte: niente scritto, perché `_revocation_marks` solleva prima
    del lock;
  - `record_revocation` e `lift_revocations` rileggono e validano dentro il lock prima di
    scrivere;
  - l'unico stato a metà pericoloso è quello di MEDIA-2.
- **`run.py`**: chiede il consenso anche per il riuso
  (`reuse_requires_consent=True` sia nel piano sia in `prepare_voices`), registra con
  `expect_sha256` e fallisce su `RevokedMeanwhile` (è un `NeedsUserDecision`). Una voce
  revocata si ferma prima della domanda.
- **Modalità web**: nessun cambiamento al precheck. Il ripiego di `voice_meta.py` scatta
  solo con `--revoke-consent`, cioè verso la revoca, mai verso il consenso.
- **Garanzie invariate**:
  - gate sha256 e `stop_or_unapproved`;
  - nessun retry: marcatore e `CloneOutcomeUnknown` restano, provato in `s2.py`;
  - nomi esatti: la revoca di una voce mancante richiede il nome esatto noto al
    registro;
  - sha256 delle musiche e cache dei segmenti: non toccati dal diff.
- **Segreti**: nessun nuovo output con dati del provider. I messaggi nuovi contengono
  solo nomi di voce e di file.
- **Sottoprocessi, prompt, retake**: niente di nuovo nel diff.

## Da verificare nelle prossime fasi
- Rieseguendo l'intera `test_web_integration.py` in questa revisione è fallito un controllo
  legato ai tempi del flusso completo in modalità web ("interrotta: battute pronte e
  mancanti": la produzione è finita prima dell'interruzione). Non riguarda il diff, ma il
  test è instabile sotto carico: da rendere deterministico.
- Confermare su Windows il punto `O_BINARY` di BASSA-2 (e aggiungere un test che
  confronti `_read_sample` con `file_hash` su un file che contiene 0x1A e CRLF).
- `tools/voices_prepare.py` (subagent) riusa ancora senza consenso fuori dal web: è
  coerente con la decisione, che riguarda `run.py`, ma va ricordato se l'utente vuole la
  stessa regola per il subagent.
- Dopo MEDIA-1, aggiornare il test "consenso dato DOPO la revoca vale" perché passi lo
  stato visto (`--expect-revoked-il`).
- `musiche/` stessa come link e gli hardlink fra i brani non sono controllati. I brani
  non vanno al provider, ma finiscono nell'audio dell'episodio: da decidere se
  confinarli come i campioni.
