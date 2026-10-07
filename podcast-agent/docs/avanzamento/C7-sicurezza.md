# Revisione di sicurezza · C7

Intervallo esaminato: `d61ee5e..269b7a6` (nessuna modifica non committata)
File esaminati: 31. Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 1 |
| Media | 2 |
| Bassa | 4 |

C7 chiude bene B1 (run.py passa da `prepare_voices`: lock, marcatore, hash), il precheck
web (provato con abbreviazioni, `--opt=val`, `--` e prefissi comuni: nessun aggiramento),
N2, N3, N4 e N5. Il punto debole è la gestione delle revoche nel registro. `lift_revocations`
toglie un'intera revoca, compresi voice_id e hash di **altre** registrazioni, appena si
registra il consenso su uno qualsiasi degli hash elencati. In più la revoca conosce solo il
voice_id della riga attuale del registro, non quelli clonati prima. Entrambi gli scenari
sono stati riprodotti con script nello scratchpad.

## Problemi

### [ALTA-1] Il consenso su una nuova registrazione fa decadere la revoca della registrazione precedente e del suo voice_id
- **Dove**: `src/registry.py:235-253` (`lift_revocations`), `src/voice_prep.py:327-333`
  (`grant_consent`), `src/voice_prep.py:336-361` (`revoke_voice_consent`, che mette nella
  stessa voce di revoca hash di registrazioni diverse).
- **Voce della checklist**: 8. Garanzie di dominio (consenso e revoche).
- **Scenario** (riprodotto, script `lift.py` nello scratchpad):
  1. `Mario Rossi.wav` = registrazione A, consenso su A, clonata come `X_ID` e usata in
     run1.
  2. Dalla libreria Voci si carica una nuova `Mario Rossi.wav` = B. Può essere una nuova
     registrazione o, per errore, la voce di un'altra persona. Il registro tiene ancora
     A → `X_ID`.
  3. La persona di A revoca il consenso (`voice_meta.py --revoke-consent`).
     `revoke_voice_consent` scrive **una sola** voce di revoca:
     `audio_sha256: [B, A]`, `voice_id: [X_ID]`.
  4. Viene registrato il consenso su B (`voice_meta.py --consent --expect-sha256 B`).
     `lift_revocations("Mario Rossi", B)` trova B nella voce e la toglie **tutta**:
     spariscono anche l'hash A e `X_ID`.

  Effetti verificati:
  - `check_not_revoked(..., voice_map={"Mario Rossi": "X_ID"}, require_consent=True)`
    passa. Un retake dal web nella run1 sintetizza di nuovo con la voce clonata di A, che
    ha revocato il consenso.
  - Se si ricarica A, fuori dal web (`run.py`, subagent) la voce risulta `reuse`, stato
    `superato`, non bloccata: `X_ID` si riusa per un episodio nuovo.

  Il docstring di `lift_revocations` promette il contrario ("Le revoche di registrazioni
  diverse restano: i loro voice_id non tornano usabili"). Bastano azioni che l'adattatore
  passa normalmente (caricamento, revoca, consenso).
- **Proposta di correzione**:
  - in `revoke_voice_consent` registrare la revoca in modo granulare: una voce per ogni
    hash, con il voice_id **solo** accanto all'hash da cui è stato clonato
    (`entry["audio_hash"]`). In alternativa, una struttura
    `{"audio_sha256": h, "voice_ids": [...]}` per hash;
  - in `lift_revocations(voice, audio_hash)` togliere solo l'hash consentito, e i voice_id
    il cui hash di clonazione è proprio quell'hash. Tutto il resto resta;
  - test in `test_web_integration.py`: la sequenza sopra deve lasciare `X_ID` revocato e
    `check_not_revoked` deve dare `ConsentRequired`.

### [MEDIA-1] La revoca non copre i voice_id clonati prima della riga attuale del registro
- **Dove**: `src/voice_prep.py:347-356` (`voice_ids=[entry.get("voice_id")]`, solo la riga
  attuale), `src/registry.py:172-207` (`record` sovrascrive la riga e perde il voice_id
  precedente), `src/voice_prep.py:306-324` (il controllo su `voice_map`).
- **Voce della checklist**: 8. Garanzie di dominio (revoche).
- **Scenario** (riprodotto, script `oldid.py`):
  1. A viene clonata come `X_ID` (run1). La registrazione viene sostituita con A2 (stessa
     persona), consenso su A2, nuova clonazione `Y_ID`: la riga del registro ora dice
     `Y_ID`.
  2. Revoca: nel registro va solo `voice_id: [Y_ID]`.
  3. Procedura del README: si cancellano audio e `.json`. Poi si carica una nuova
     `Mario Rossi.wav` e si registra il consenso.
  4. Retake dal web nella run1 (`voci.json` → `X_ID`): `check_not_revoked` passa e si
     sintetizza con `X_ID`. Con `Y_ID` invece è bloccato.

  Stesso effetto per un voice_id clonato su un altro provider prima di un cambio di
  provider, o se la riga del registro è stata tolta prima della revoca. Inoltre
  `voice_meta.py --revoke-consent` richiede che il file sia ancora in libreria: dopo la
  cancellazione la revoca nel registro non si può più fare. Servono condizioni
  particolari (ricaricamento con lo stesso nome più nuovo consenso), quindi gravità media.
- **Proposta di correzione**:
  - in `VoiceRegistry.record`, prima di sovrascrivere, conservare i voice_id precedenti
    della voce in uno storico, per esempio `entry["precedenti"]: [{voice_id, audio_hash,
    provider}]`;
  - in `revoke_voice_consent`, mettere nella revoca anche lo storico e i voice_id di quello
    speaker trovati nei `voci.json` delle run in `output/`;
  - documentare nel contratto e nel README che la revoca va fatta **prima** di cancellare i
    file.

### [MEDIA-2] Una voce chiamata `__revoche__` si clona (a pagamento) e poi fallisce la registrazione
- **Dove**: `src/registry.py:190-191` (il `ValueError` scatta in `record`),
  `src/voice_prep.py:526-527` (si arriva a `record` **dopo** `provider.clone_voice`),
  `src/voice_library.py:100-101` (il nome `__revoche__` è accettato).
- **Voce della checklist**: 8. Garanzie di dominio (spesa, nessun retry); 1. nomi da input.
- **Scenario**:
  - si carica `voci_disponibili/__revoche__.wav`, si registra il consenso e si produce;
  - `plan_voices` la dà `clone` (`_voices()` esclude la chiave) e il consenso c'è;
    `clone_voice` crea e fa pagare la voce, poi `record` solleva `ValueError`;
  - il voice_id va perso: la voce resta orfana sul provider e la run si blocca. Il
    marcatore resta, quindi non c'è un retry automatico, ma ogni `--confirm-clone-retry`
    paga un'altra clonazione che finisce nello stesso modo.
- **Proposta di correzione**: rifiutare il nome riservato **prima** di spendere. In
  `VoiceLibrary.scan` il file va fra gli scartati, oppure `plan_voices` e
  `resolve_library_voice` sollevano `VoiceLibraryError` per `REVOCATIONS_KEY`, magari per
  ogni nome che comincia e finisce con `__`. Test con un file `__revoche__.wav`: nessuna
  chiamata a `clone_voice`.

### [BASSA-1] Revoca e consenso concorrenti: il consenso può cancellare la revoca appena scritta
- **Dove**: `src/voice_prep.py:327-333` e `:336-361`. I due passi (metadati sotto il lock
  del `.json`, registro sotto il lock del registro) non sono atomici insieme.
- **Voce della checklist**: 7. Lock.
- **Scenario**: l'utente U1 registra il consenso (metadati scritti) mentre U2 revoca
  (metadati, poi registro). Poi U1 esegue `lift_revocations` e toglie la revoca di U2.
  Risultato: metadati "revocato", registro senza revoca. Finché il `.json` esiste il
  blocco regge. Dopo la cancellazione dei file prevista dal README la revoca nel registro
  non c'è più, e si torna allo scenario di MEDIA-1. Non c'è deadlock, perché i lock non
  sono annidati.
- **Proposta di correzione**: in `grant_consent` e `revoke_voice_consent` prendere un lock
  per voce che copra i due passi, per esempio il lock di `meta_path(voice.path)` tenuto
  anche durante la scrittura del registro, sempre nello stesso ordine (metadati, poi
  registro). In alternativa, in `lift_revocations` togliere solo le revoche con `il`
  precedente al consenso appena scritto.

### [BASSA-2] `__revoche__` malformato: le revoche si ignorano in silenzio
- **Dove**: `src/registry.py:157-159` (`revocations()` restituisce `[]` se il valore non è
  una lista), `:231` (`record_revocation` lo sovrascrive).
- **Voce della checklist**: 8. Garanzie di dominio (fail-open).
- **Scenario**:
  - un `voices_registry.json` modificato a mano, o di prima di C7 con una voce registrata
    davvero come `__revoche__` (prima `record` lo accettava): il valore è un dict, e tutte
    le revoche risultano assenti;
  - un elemento non dict nella lista viene scartato in silenzio.
- **Proposta di correzione**: se la chiave esiste e non è una lista di oggetti, sollevare
  `RuntimeError`, come già fa `_load` per un JSON non valido (nel dubbio ci si ferma).

### [BASSA-3] Link simbolici: restano directory collegate, hardlink e un TOCTOU fra scansione e lettura
- **Dove**: `src/voice_library.py:86-101`, `src/music_library.py:163-176`,
  `src/voice_prep.py:494` (`plan.voice.path.read_bytes()` segue un link creato dopo la
  scansione).
- **Voce della checklist**: 1. Path traversal.
- **Scenario**:
  - `voci_disponibili/` stessa, o `musiche/intro/`, come link a un'altra cartella;
  - un hardlink a un file esterno;
  - un file sostituito da un link fra `scan()` e `read_bytes()`.

  In tutti questi casi il contenuto esterno arriva al provider come campione. Serve però
  accesso al filesystem: l'upload HTTP dell'adattatore non crea link.
- **Proposta di correzione**:
  - leggere i byte da clonare con `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`;
  - controllare che `voices_dir.resolve()` e le cartelle di categoria non siano link, o
    confinarle con `confine_to`;
  - documentare nel contratto che l'adattatore scrive i caricamenti solo come file
    regolari.

### [BASSA-4] Dopo un'attesa sul lock di clonazione non si ricontrolla l'approvazione
- **Dove**: `src/voice_prep.py:470-473`. `should_stop` (che ora include
  `stop_or_unapproved`, `src/production.py:349-356`) si controlla **prima** di
  `file_lock(..., timeout=600)`, non dopo.
- **Voce della checklist**: 8. Gate di approvazione (N2).
- **Scenario**: una produzione aspetta fino a 10 minuti che un'altra finisca di clonare la
  stessa voce. Intanto l'approvazione viene ritirata (`script_approve --revoke`). Se nel
  frattempo la voce non è stata clonata, ad esempio perché l'altra produzione è fallita,
  si clona lo stesso. La spesa era nella stima approvata e la sintesi si ferma alla prima
  battuta: per questo la gravità è bassa.
- **Proposta di correzione**: dentro il lock, dopo `registry.reload()`, richiamare
  `should_stop()` e uscire senza clonare se è vero.

## Verificato e a posto
- **1. Path traversal**:
  - `music_set` e `music_choice` risolvono i nomi solo contro la libreria (`resolve`) e
    scrivono un file fisso della run, già confinata da `run_dir_arg`;
  - `music_choice_view` confronta i nomi letti da `musiche.json` con i brani della
    libreria e non costruisce percorsi;
  - `mix.py` in modalità web emette percorsi `output/<run>/…` (`ui_path`);
  - link scartati nelle due librerie (N4), con i limiti in BASSA-3.
- **2. Sottoprocessi**: nessuna nuova chiamata a ffmpeg/ffprobe nel diff;
  `test_ffmpeg_input_confined` resta valido.
- **3. File non fidati**: niente di nuovo. Unica aggiunta, `caricata_il` legge solo
  `stat()`.
- **4/5. Prompt injection e retake**:
  - `script_write` dal web non accetta più cast, formato e durata da riga di comando (N6);
  - il retake resta legato al gate sullo sha256 (`enforce`) e ora chiede anche il
    consenso registrato dal web.
- **6. Segreti**:
  - gli eventi (`EventLog.emit`) e i `warnings` di produce e mix passano da `redact()`
    (N3);
  - `doctor --web` riporta solo se la variabile esiste;
  - nessun `str(exc)` nuovo verso stdout con dati del provider.
- **7. Lock**:
  - `set_music_choice` rilegge dentro il lock;
  - `record_revocation` e `lift_revocations` rileggono dentro il lock del registro, e
    nessun lock è annidato con un altro in ordine diverso;
  - `stop_or_revoked` rilegge il registro a ogni battuta. Un registro corrotto solleva un
    errore e la produzione si ferma (fail-closed).
- **8. Garanzie**:
  - **nessun retry sulla clonazione**: B1 fa passare `run.py` da `prepare_voices`
    (marcatore, lock per voce, hash dei byte clonati); `fail()` su `VoiceProviderError`;
  - **consenso in `run.py`**: si chiede solo per le voci da clonare, si registra con
    `expect_sha256` = hash mostrato nel piano (file cambiato → `RecordingChanged` →
    `fail`); "revocato", compreso quello solo nel registro, blocca sempre prima della
    domanda, quindi `run.py` non può togliere una revoca;
  - **riuso dal web**: richiede il consenso registrato (`reuse_requires_consent`, che vale
    di default se `web_mode()`), anche nel ricontrollo di `prepare_voices`;
  - **gate dell'approvazione**: `stop_or_unapproved` rilegge la sessione prima di ogni
    clonazione e di ogni battuta (N2);
  - sha256 delle musiche e cache dei segmenti non sono toccati.
- **Precheck web** (`tools/_common.py:202-229`). Provati con uno script:
  - fermati da `NotAllowedInWebMode`: `--config=x`, `--conf=x`, `--c x`, `--trusted`,
    `--no-a`, `--sc`, `--s`, `--se`, `--by --config`;
  - rifiutati da argparse: `-config x`, `--CONFIG`, `--config​`, token dopo `--`;
  - `--by=--config=x` diventa solo il valore di `--by`.

  Il controllo dopo argparse resta come rete di sicurezza. `require_by_in_web` copre
  approvazione e ritiro, modifica, revisione, stesura, ripristino, retake (non in prova) e
  nome pronunciato.
- **`with_registry`**:
  - la revoca vale per voice_id **o** per hash, indipendentemente dal nome: la stessa
    registrazione caricata con un altro nome resta revocata;
  - `lift_revocations` richiede che coincidano anche i nomi, quindi non sblocca le revoche
    di un'altra voce.

## Da verificare nelle prossime fasi
- Dopo ALTA-1 e MEDIA-1, un test di regressione per ciascuno degli scenari riprodotti: la
  sequenza sostituzione → revoca → consenso, e il voice_id storico dopo una riclonazione.
- `retake --choose` e `mix` dal web non richiedono il consenso registrato, solo l'assenza
  di revoca. È coerente con la decisione N1 (non sintetizzano), ma va ricordato se il mix
  dovesse mai generare audio.
- `music_set` non prende il `work_lock` della run: una produzione in corso usa la scelta
  già validata in `preflight`, quindi oggi non c'è effetto. Da ricontrollare se il mix
  dovesse rileggere `musiche.json`.
- Il contratto dovrebbe dire esplicitamente che la revoca va registrata prima di
  cancellare i file della voce (vedi MEDIA-1).
