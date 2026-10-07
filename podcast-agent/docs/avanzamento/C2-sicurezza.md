# Revisione di sicurezza · C2

Intervallo esaminato: `b3c6ed8..dde378c` (nessuna modifica non committata rilevante: solo `.DS_Store`, ignorati)
File esaminati: 24. Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 1 |
| Media | 4 |
| Bassa | 5 |

Le garanzie principali reggono: il gate di approvazione di `produce.py` controlla e usa lo
stesso oggetto copione, non c'è nessun retry esplicito, `--no-consent-gate` e
`--no-approval-gate` sono rifiutati in modalità web e `produce.py` non ha bypass. Il punto
debole è il **consenso**. Si controlla una volta sola, all'inizio, e non di nuovo subito
prima di ogni clonazione: una voce si può clonare dopo una revoca, o da una registrazione
diversa da quella approvata (ALTA-1, riprodotto). Restano da correggere anche la gestione
delle clonazioni dall'esito incerto (MEDIA-1) e `--events`, che può sovrascrivere i file
della run (MEDIA-2).

## Problemi

### [ALTA-1] Consenso controllato all'inizio e non subito prima di ogni clonazione: si clona dopo una revoca, o un audio diverso da quello con il consenso
- **Dove**:
  - `src/voice_prep.py:218-220`: `plan_voices` e `check_consent` una volta sola, prima di tutte le clonazioni;
  - `src/voice_prep.py:246-266`: dentro il lock per voce si rilegge il registro ma **non** il consenso, e `provider.clone_voice(name, str(plan.voice.path))` rilegge il file dal disco in quel momento;
  - `src/registry.py:143`: `record()` ricalcola lo sha256 del file **dopo** la clonazione.
- **Voce della checklist**: 8. Garanzie di dominio (consenso prima di ogni clonazione); 7. Lock (TOCTOU).
- **Scenario**: `produce.py` su una run con due voci nuove, Anna e Bruno, entrambe con il consenso registrato. Il controllo passa per tutte e due, poi parte la clonazione di Anna (con Fish Audio dura decine di secondi; un processo che aspetta il lock della stessa voce può restare fermo fino a 600 s).
  - Nel frattempo Bruno revoca il consenso dall'interfaccia (`voice_meta.py --revoke-consent`), oppure `Bruno.wav` viene sostituito da un'altra registrazione.
  - Finita Anna, Bruno viene clonato lo stesso, con il consenso revocato e/o dall'audio nuovo che nessuno ha approvato.
  - Riprodotto con uno script in una cartella temporanea fuori dal progetto (provider finto, `prepare_voices` reale). Risultato: `{'Anna': 'cloned', 'Bruno': 'cloned'}`, audio clonato per Bruno `b'audio-di-un-altra-persona'`, stato del consenso di Bruno `revocato`.
  - Effetto collaterale: `registry.record` salva l'hash del file **attuale**. Se il file cambia durante la clonazione, il registro associa il `voice_id` clonato dal vecchio audio allo sha256 del nuovo, e le produzioni successive lo riusano per la registrazione nuova (il riuso controlla il consenso solo per lo stato `revocato`).
- **Proposta di correzione**: in `prepare_voices`, dentro `with file_lock(_clone_lock_target(...))` e dopo il ricontrollo del registro:
  1. leggere **una volta** i byte della registrazione (o copiarli in un temporaneo con `mkstemp` nella stessa cartella) e calcolarne lo sha256;
  2. ricaricare i metadati (`load_meta`) e richiedere `consenso.audio_sha256 == sha appena calcolato` e nessuna revoca, altrimenti `ConsentRequired` (exit 3) per quella voce, senza clonare;
  3. clonare **da quei byte/temporaneo**, non dal percorso della libreria;
  4. passare lo sha già calcolato a `registry.record` (nuovo parametro `audio_hash=`) invece di ricalcolarlo.

  Il lock dei metadati non va tenuto durante la chiamata al provider: basta rileggere i metadati, perché la revoca è un `os.replace` atomico. Non si crea un problema di ordine dei lock. Aggiungere un test `[C2]` con il provider finto: `clone_delay_seconds` sulla prima voce, poi una revoca e la sostituzione del file della seconda mentre la prima si clona. Attesi exit 3 e una sola riga in `clonazioni.log`.

### [MEDIA-1] Clonazione dall'esito incerto ripetuta: da un secondo processo in attesa del lock, o dal rilancio dopo un secondo segnale
- **Dove**: `src/voice_prep.py:246-266`; `tools/produce.py:56-60` (il secondo segnale alza `KeyboardInterrupt` ovunque); contratto, passo 6 ("Un secondo SIGTERM ferma subito").
- **Voce della checklist**: 8. Nessun retry sulla clonazione; 7. Lock.
- **Scenario**:
  - (a) Due produzioni (P1 e P2, anche di utenti diversi) usano la stessa voce nuova. P1 prende il lock per voce e clona. Il provider risponde con un errore dopo aver creato la voce (timeout di rete, 5xx dopo il commit): P1 esce con exit 2 come previsto. P2 era in attesa del lock: lo prende, rilegge il registro, non trova la voce e **clona di nuovo**. In pratica è il retry automatico che la regola vieta: voce duplicata a pagamento sul provider, senza che nessuno abbia visto l'errore di P1.
  - (b) L'utente preme due volte "Interrompi" (o l'adattatore passa a un secondo SIGTERM come dice il contratto) mentre `provider.clone_voice` è in corso, oppure fra il ritorno di `clone_voice` e `registry.record`. Il `KeyboardInterrupt` interrompe la clonazione: sul provider la voce può esistere, nel registro no. Il rilancio della produzione la clona di nuovo.
- **Proposta di correzione**:
  - Prima di chiamare `clone_voice`, dentro il lock per voce, scrivere in modo atomico un marcatore `clonazione_in_corso` (es. `.clonazione-<digest>.json` accanto al lock, con nome, sha256, `pid` e ora); toglierlo solo dopo `registry.record` riuscito.
  - Chi prende il lock, trova il marcatore e non trova la voce nel registro non clona: esce con un `NeedsUserDecision` (exit 3, es. `CloneOutcomeUnknown`) che chiede di verificare sul provider (`list_cloned_voices`) e di confermare esplicitamente. In alternativa, se il provider lo consente, cerca da solo una voce con quel nome creata dopo l'ora del marcatore e la registra.
  - Nel contratto: l'adattatore non manda un secondo SIGTERM mentre l'ultimo evento è `fase_iniziata: preparazione_voci` senza la `fase_finita` corrispondente. In alternativa, `produce.py` blocca i segnali (`signal.pthread_sigmask`) durante `clone_voice` + `record`.

### [MEDIA-2] `--events` può puntare a qualunque file della run e lo tronca: `conversazione.json`, `_script_session.json`, segmenti
- **Dove**: `tools/produce.py:52` (`confine_to(args.events, run_dir)` controlla solo che il file stia nella run); `src/production.py:110-111` (`self.path.open("w")`).
- **Voce della checklist**: 1. Path traversal (file da input); 8. Garanzie (perdita di dati della run).
- **Scenario**:
  - `produce.py --run-dir output/R --events output/R/_script_session.json` passa il gate, prende il lock e al primo evento **svuota** la sessione (tutte le versioni del copione, l'approvazione, lo storico) e ci scrive JSONL. Il run successivo non si apre più.
  - Con `--events output/R/conversazione.json` il copione approvato diventa JSONL. Con `--events output/R/segmenti/<chiave>.wav` si corrompe un segmento della cache, che poi finisce nel montaggio.
  - `--events` non è fra gli argomenti rifiutati in modalità web, a differenza di `--out`/`--voices`/`--script`, che lo sono perché "i nomi dei file li fissa il tool".
  - Gravità media e non alta: il valore lo sceglie l'adattatore e il contratto suggerisce `_eventi.jsonl`. Basta però un nome costruito male, o preso da un campo, per perdere i dati della run.
- **Proposta di correzione**: il nome lo fissa il tool. `--events` diventa un flag (`store_true`) che scrive sempre `output/<run>/_eventi.jsonl`. Se si vuole tenere il percorso, accettare solo un nome semplice (niente separatori, non nascosto) con estensione `.jsonl`, diverso dai file noti della run, e direttamente nella cartella della run (`resolved.parent == run_dir`). Aprire con `os.open(..., O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW)`.

### [MEDIA-3] Il consenso si registra sullo sha256 del file al momento del clic, non su quello che l'utente ha visto
- **Dove**: `src/voice_meta.py:112-128` (`record_consent` calcola `file_hash(audio_path)` al momento della chiamata); `tools/voice_meta.py:42-43`.
- **Voce della checklist**: 8. Consenso prima della clonazione; 7. TOCTOU.
- **Scenario**: al passo 5 l'utente A vede la voce "Mario Rossi" (registrazione X, stato `mancante`) e spunta "Confermo che Mario Rossi ha dato il consenso". Fra la stima e il clic, `Mario Rossi.wav` viene sostituito con la registrazione Y di un'altra persona: un caricamento in Librerie, C4, o un altro utente. `voice_meta.py --consent` registra il consenso con lo sha256 di **Y**, lo stato diventa `registrato` e `produce.py` clona Y. Il meccanismo `superato` serve proprio a questo caso, ma qui non scatta perché l'hash si prende dopo la sostituzione. Richiede una sostituzione concorrente (dipende dalla politica dei caricamenti, C4), per questo è media.
- **Proposta di correzione**:
  - Esporre lo sha256 (o un suo prefisso) della registrazione nella vista che la UI mostra: `estimate_run().voices[].audio_sha256`, `consent_view`, `episode_view`.
  - Aggiungere `voice_meta.py --consent --expect-sha256 H`, obbligatorio in modalità web. `record_consent` calcola l'hash **dentro** il lock e, se è diverso da `H`, esce con exit 3 (`RecordingChanged`) senza scrivere.

### [MEDIA-4] Produzione interrotta o bloccata durante la codifica: episodio troncato con il nome finale, lock tenuto senza limite
- **Dove**:
  - `src/music_mixer.py:348-350` e `788-797`: ffmpeg scrive direttamente su `<slug>.mp3` con `-y`;
  - `src/audio_assembler.py:697-722`: `_encode_mp3`, stesso schema, e il master wav a `:672` scritto senza temporaneo;
  - `src/music_mixer.py:628` e `src/audio_assembler.py:721`: `subprocess.run` **senza timeout**.

  Il codice non è nuovo, ma con C2 entra nel percorso web (`produce.py`), con l'interruzione e lo stato derivato dal lock.
- **Voce della checklist**: 2. Sottoprocessi (timeout); 7. File temporanei; interruzione (punto chiesto da C0+C1).
- **Scenario**:
  - (a) Si rigenera un episodio già prodotto e lo si interrompe con un secondo SIGTERM durante la codifica (o con un SIGKILL dell'adattatore). `subprocess.run` uccide ffmpeg e `<slug>.mp3`, che prima era l'episodio buono, resta troncato con il nome definitivo. L'Archivio lo mostrerà come episodio.
  - (b) Un ffmpeg appeso (file musicale patologico, disco pieno, NFS) tiene `produce.py` vivo per sempre. Il lock di lavoro non si libera: la run resta "In generazione" e ogni altra operazione esce con `RunBusy`.
- **Proposta di correzione**:
  - Codificare su un temporaneo nella stessa cartella (`.<slug>.mp3.<rand>.tmp`) e fare `os.replace` sul nome finale; stessa cosa per il wav di sola voce in `_assemble_from_wav`.
  - Mettere `timeout=` a tutte le `subprocess.run` di ffmpeg, proporzionato alla durata (es. 60 s + 2× la durata dell'audio), con `MusicMixerError`/`AssemblyError` alla scadenza.

### [BASSA-1] Gli eventi finali si scrivono dopo il rilascio del lock, su un file aperto senza `O_APPEND`
- **Dove**: `tools/produce.py:75-83` (`interrotta`/`errore` emessi dopo l'uscita da `produce()`, quindi fuori da `work_lock`); `src/production.py:111` (`open("w")`).
- **Voce della checklist**: 7. Lock.
- **Scenario**: P1 viene interrotta e rilascia il lock. Prima che P1 scriva `interrotta`, l'adattatore (o un altro utente) lancia P2 sulla stessa run: P2 tronca `_eventi.jsonl` e scrive `iniziata`. P1 scrive poi al **suo** offset, oltre la fine del file troncato: il file di P2 si riempie di byte nulli e di una riga `interrotta` che non è sua, e la UI di P2 mostra "interrotta". La finestra è piccola.
- **Proposta di correzione**: emettere `interrotta`/`errore` dentro `produce()`, ancora sotto il lock (un `try/except BaseException` attorno al corpo del `with work_lock`), oppure aprire il file in append (`"a"` dopo un troncamento esplicito sotto lock).

### [BASSA-2] Una revoca durante la produzione non ferma la sintesi
- **Dove**: `src/production.py:520-576`; `src/voice_prep.py:225-232` (il riuso non ricontrolla).
- **Voce della checklist**: 8. Consenso.
- **Scenario**: la persona revoca il consenso mentre una produzione lunga è in sintesi. Tutte le battute restanti vengono generate e montate con la sua voce. Il comportamento dichiarato ("una voce revocata non si riusa") vale solo dall'avvio della produzione successiva.
- **Proposta di correzione**: ricontrollare `consent_state` delle voci usate fra una battuta e l'altra (costa uno stat e un hash per battuta, oppure un controllo ogni N battute o ogni 30 s). Se una voce risulta revocata, fermarsi come per `should_stop` con un `ConsentRequired`. In alternativa, documentare nel contratto che la revoca vale dalle produzioni successive.

### [BASSA-3] Percorsi assoluti nel JSON di `produce.py`, diversi dall'esempio del contratto
- **Dove**: `src/production.py:601, 613` (`output`, `timings_file`), `:212` (`musiche_usate_file`); `tools/produce.py:87` (`events_file`).
- **Voce della checklist**: 6. Segreti / informazioni esposte.
- **Scenario**: `run_dir` è risolto, quindi `output` è `/Users/<nome>/.../output/<run>/x.mp3`. Il contratto mostra invece `"output": "output/<run>/..."`: un adattatore che inoltra il JSON alla UI rivela il percorso del server. È la BASSA-2 di C0+C1, ancora aperta, estesa al comando nuovo.
- **Proposta di correzione**: in `produce.py` (comando solo web) emettere i percorsi relativi a `output/` (o solo `file`), oppure correggere l'esempio del contratto e scrivere che l'adattatore non inoltra quei campi.

### [BASSA-4] Metadati di voce malformati: eccezione generica invece di `VoiceMetaError`
- **Dove**: `src/voice_meta.py:76-80`, `:87-88`.
- **Voce della checklist**: 8 / robustezza.
- **Scenario**: un `Mario.json` scritto a mano con `"consenso": "si"` (o una lista) dà `AttributeError` in `consent_state`. Stima, vista dei ruoli e produzione falliscono con un `error_type` non stabile. Non è un aggiramento: con un oggetto `consenso` scritto a mano e con lo sha256 giusto il consenso risulta invece `registrato` (vedi "Da verificare").
- **Proposta di correzione**: in `load_meta`/`consent_state` validare i tipi (`consenso` è `dict` o `None`, `audio_sha256` è una stringa `sha256:`), altrimenti `VoiceMetaError`.

### [BASSA-5] `voices_prepare.py` in modalità web clona qualunque voce con il consenso, contro qualunque run approvata
- **Dove**: `tools/voices_prepare.py:50-62` (`enforce(args)` verifica che il copione della run sia approvato, ma non che `--speakers` siano quelli del copione).
- **Voce della checklist**: 8. Gate di approvazione (spesa).
- **Scenario**: `voices_prepare.py --speakers "Persona X" --run-dir output/<run approvata>`, senza `--out` (quindi ammesso in modalità web), clona Persona X anche se non compare nel copione approvato. Il consenso c'è, quindi non è una clonazione senza consenso, ma la spesa non è legata a un copione approvato. Il comportamento esisteva già prima di C2; ora che il percorso web è `produce.py`, lo strumento non serve all'interfaccia.
- **Proposta di correzione**: rifiutare `voices_prepare.py` in modalità web (come `synthesize.py`, già inutilizzabile senza `--script`/`--voices`), oppure richiedere che `set(args.speakers) <= set(copione approvato.speakers)` quando c'è `--run-dir`.

## Verificato e a posto
- **1. Path traversal**:
  - `--run-dir` di `produce.py`, `estimate.py` e `voice_meta.py` passa da `run_dir_arg`;
  - i metadati delle voci si ricavano solo da file della libreria (`resolve_library_voice` / `plan_voices`, nomi esatti), mai dal nome in input;
  - il lock per voce ha un nome derivato dallo sha256 del nome (`.clonazione-<16 hex>`), quindi caratteri strani o `../` nel nome non escono dalla cartella del registro;
  - `_archive_document` usa `locate_document(confined=True)`;
  - `--events` resta dentro la run (ma vedi MEDIA-2).
- **2. Sottoprocessi**: nessun sottoprocesso nuovo; quelli esistenti usano argomenti in lista, senza `shell=True` (manca il timeout: MEDIA-4).
- **3. File non fidati**: niente di nuovo nel diff (i metadati JSON delle voci sono piccoli e nati dal tool).
- **4. Prompt injection**: `produce.py` sintetizza solo l'oggetto copione caricato da `conversazione.json`, dopo averne verificato lo sha256 contro l'approvazione. È lo **stesso oggetto** in memoria per tutta la produzione, quindi non c'è finestra fra controllo e lettura, e modificare il file durante la produzione non cambia ciò che viene sintetizzato.
- **5. Tag dei retake**: `retake.py` non è nel diff; il percorso `produce` non accetta testo libero.
- **6. Segreti**:
  - errori ed eventi `errore` passano da `_redact`;
  - `estimate_run` toglie `voice_id` da `voices[]`;
  - i messaggi d'errore nuovi usano solo nomi di file;
  - nessuna chiave in eventi, `_lavoro.json` o metadati.
- **7. Lock**:
  - `record_consent`/`revoke_consent` fanno lettura, modifica e scrittura sotto `file_lock` del `.json`, con rilettura dentro e scrittura atomica (test con 8 scritture concorrenti);
  - lock di lavoro non bloccante preso per primo, poi lock per voce, poi lock del registro (dentro `record`, con rilettura): l'ordine è sempre lo stesso e il lock dei metadati non è annidato, quindi niente deadlock;
  - i lock si rilasciano in `finally` e con la morte del processo (flock);
  - il file degli eventi si apre dopo il lock, quindi `RunBusy` non lo tocca (verificato dal test);
  - `.json`/`.lock`/`.tmp` accanto alle voci sono esclusi dalla libreria.
- **8. Garanzie di dominio**:
  - gate sha256 in `produce.py` senza bypass;
  - `--no-consent-gate` e `--no-approval-gate` in `WEB_FORBIDDEN_ARGS`;
  - `check_consent` blocca **tutte** le clonazioni se manca il consenso di una sola voce (test: zero tentativi);
  - lo sha256 nel consenso rende innocui nomi duplicati (`Mario.wav` + `Mario.mp3` → stesso `Mario.json`, ma l'hash non torna: `superato`) e spazi finali nel nome;
  - nessun retry esplicito (un solo tentativo nel log, test);
  - musiche validate (preflight sha256) prima di clonare;
  - segmenti scritti in modo atomico e interruzione fra una battuta e l'altra senza file a metà (test);
  - `synthesize.py` è di fatto inutilizzabile in modalità web (`--script`/`--voices` vietati);
  - `run.py` resta senza consenso per decisione D5: è solo uso locale, non esposto all'adattatore.
- Test: `tests/test_web_integration.py` → 394 controlli superati.

## Da verificare nelle prossime fasi
- **Retake (C3)**:
  - `retake.py` usa `voci.json` della run: deve rifiutare le voci con il consenso `revocato` (come `produce`) e non riceve mai testo libero, solo tag dalla whitelist di configurazione;
  - lo stesso per `mix.py`/rimontaggi che rigenerano audio.
- **Librerie (C4), caricamenti in `voci_disponibili/`**:
  - accettare solo le estensioni audio, mai `.json`: un `<voce>.json` caricato con `consenso.audio_sha256` giusto vale come consenso registrato;
  - niente sostituzione silenziosa di una registrazione (MEDIA-3);
  - cancellare voce e `.json` insieme.
- **Adattatore**:
  - `--by` di `voice_meta.py` deve essere l'utente autenticato, non un campo libero;
  - chi può registrare o revocare il consenso, e di quali voci, è una decisione di autorizzazione: oggi qualsiasi chiamante può revocare tutte le voci, cioè bloccare ogni produzione;
  - l'adattatore non manda un secondo SIGTERM durante la preparazione delle voci (MEDIA-1);
  - `--events` sempre con il nome fisso (MEDIA-2).
- **Consenso per episodio** (dubbio aperto in `C2.md`): se si decide un consenso "a fargli dire questo copione", va legato allo sha256 del copione approvato e controllato insieme al gate.
- Ancora aperte da C0+C1: MEDIA-1 (limiti dei documenti), BASSA-1, BASSA-2 (percorsi assoluti: vedi BASSA-3 sopra), BASSA-3.


---

## Esito delle correzioni (aggiunto da chi ha implementato, non dal revisore)

Data: 2026-10-07. Come da regola, l'alta è corretta prima di C3; medie e basse restano da
decidere con l'utente.

### ALTA-1 · corretta
- `src/voice_prep.prepare_voices`, dentro il lock per voce e dopo il ricontrollo del
  registro:
  1. legge **una volta** i byte della registrazione e ne calcola lo sha256;
  2. ricontrolla il consenso **su quello sha256** (`consent_state(..., audio_sha256=...)`):
     se è revocato, mancante o registrato su un altro audio, `ConsentRequired` (exit 3) per
     quella voce, senza clonarla;
  3. clona da una **copia privata** di quei byte (`mkstemp` accanto alla registrazione,
     nome con il punto davanti, cancellata subito dopo), non dal file della libreria;
  4. passa lo sha256 già calcolato a `registry.record(..., audio_hash=...)` (parametro
     nuovo), così il voice_id resta legato all'audio davvero clonato.
- Test `[sicurezza C2]` in `test_web_integration.py` (7 controlli), con il provider finto
  lento sulla prima voce:
  - revoca della seconda voce mentre la prima si clona → exit 3 `revocato`, una sola
    clonazione;
  - sostituzione del file della seconda voce → exit 3 `superato`, una sola clonazione;
  - sostituzione del file **durante** la clonazione → il registro tiene lo sha256 dei
    byte clonati, e la registrazione nuova risulta da clonare (`superato`), non riusabile;
  - nessuna copia temporanea lasciata nella libreria.
- Suite: web_integration 401, le altre invariate e verdi.

### Medie e basse · corrette (decisione dell'utente: tutte, prima di C3)
- **MEDIA-1**: marcatore `.clonazione-<digest>.json` accanto al lock per voce, scritto prima
  di chiamare il provider e tolto solo dopo `registry.record`. Se c'è, chi arriva dopo
  (altro processo o rilancio) non clona: exit 3 `CloneOutcomeUnknown`, finché l'utente non
  conferma con `voice_meta.py --confirm-clone-retry --by U`. Clonazione e registrazione
  con SIGTERM/SIGINT rinviati (`pthread_sigmask`, dove esiste). La stima segnala
  `clonazione_incerta`.
- **MEDIA-2**: `--events` è un flag; il file è sempre `_eventi.jsonl` nella run, aperto con
  `O_NOFOLLOW` (un link simbolico dà `PathNotAllowed`) e in append.
- **MEDIA-3**: `consenso.audio_sha256` nella vista; `voice_meta.py --consent
  --expect-sha256 H`, obbligatorio in modalità web: hash calcolato dentro il lock, se
  diverso exit 3 `RecordingChanged` e niente scritto.
- **MEDIA-4**: `fileio.atomic_output` per mp3 finale (mix e montaggio), master wav e
  concatenazione mp3; `FFMPEG_TIMEOUT_SECONDS = 900` su tutte le esecuzioni di ffmpeg del
  montaggio e del mix.
- **BASSA-1**: `interrotta`/`errore` emessi dentro `produce()`, sotto il lock; il messaggio
  passa da `src/redaction.py` (spostato da `tools/_common.py`).
- **BASSA-2**: fra una battuta e l'altra si rileggono i metadati delle voci; una revoca
  ferma la produzione con exit 3 `ConsentRequired` (+ `done`/`total`/`missing`).
- **BASSA-3**: il JSON di `produce.py` usa percorsi `output/<run>/<file>`.
- **BASSA-4**: tipi dei metadati validati: `VoiceMetaError`.
- **BASSA-5**: `voices_prepare.py` e `synthesize.py` rifiutati per intero in modalità web
  (`parse_args(web_allowed=False)`).
- Test: web_integration da 401 a 430; le altre suite invariate e verdi.

