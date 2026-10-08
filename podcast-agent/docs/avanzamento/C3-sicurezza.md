# Revisione di sicurezza · C3

Intervallo esaminato: `4d8ede8..8621966`: le correzioni di medie e basse della revisione C2
(commit `9006245`) e la fase C3 (commit `8621966`). I `.DS_Store` non sono stati considerati.
Nessuna modifica non committata rilevante.
File esaminati: 30 (codice, test, config, contratto e resoconti). Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 2 |
| Bassa | 6 |

Le correzioni di C2 reggono. Il marcatore di clonazione incerta, i segnali rinviati,
`--expect-sha256`, `EventLog`, `atomic_output`, i timeout di ffmpeg e la revoca durante la
sintesi fanno quello che dichiarano, e i test `[sicurezza C2]` passano. Nei retake nessun
testo libero arriva al TTS: le indicazioni passano solo dalla whitelist di `config.yaml` e
la chiave del take resta quella del testo approvato. I due problemi medi sono due buchi
nelle garanzie del rimontaggio:
- `mix.py` controlla il gate su una lettura del copione diversa da quella che poi monta;
- `mix.py` monta anche le voci con il consenso revocato.

Le basse riguardano soprattutto percorsi assoluti e file interni esposti all'adattatore.

Test: `tests/test_web_integration.py` → 488 controlli superati, 0 saltati.

## Problemi

### [MEDIA-1] `mix.py` controlla l'approvazione su una lettura del copione e ne monta un'altra
- **Dove**:
  - `tools/mix.py:52`: `enforce(args)` legge `conversazione.json` dal disco, prima del lock;
  - `tools/mix.py:59`: `RunCache.load` lo rilegge dopo `preflight` e monta quella seconda lettura;
  - `src/script_editing.py:90-115, 128-154`: `edit_turn`/`restore_version` scrivono
    `conversazione.json` sotto il solo `session_lock`, non sotto il lock di lavoro;
  - stesso schema, rovesciato, in `tools/retake.py:91` (`RunCache.load`) e `:185`
    (`enforce`);
  - `src/episode_info.py:148-155`: lo stato non tiene conto dell'approvazione.
- **Voce della checklist**: 8. Gate di approvazione legato allo sha256; 7. TOCTOU.
- **Scenario**:
  - La run ha la versione 2 prodotta in passato (segmenti ancora in cache) e la versione 3
    approvata e prodotta. L'utente A cambia le musiche e preme "Rimonta" (`mix.py`).
    `enforce` vede la v3 approvata.
  - Mentre `preflight` calcola gli sha256 dei brani e li sonda con ffmpeg, l'utente B
    preme "Ripristina v2". `restore_version` scrive la v2 in `conversazione.json` e toglie
    l'approvazione.
  - `RunCache.load` legge la v2. Tutti i suoi segmenti sono in cache, quindi niente
    `SegmentsMissing`: il file finale diventa la v2, senza approvazione.
  - `_montaggio.json` registra lo sha256 della v2, uguale al copione attuale: per
    `episode_info` l'episodio è `pronto` (`motivi: []`) e solo `copione_approvato: false`
    rivela il problema.
  - Non c'è nuova sintesi, e l'audio viene da testi approvati in passato. Il risultato è
    comunque un episodio finale "pronto" montato da un copione non approvato. Nel
    `retake.py` la finestra è di pochi microsecondi e porterebbe a sintetizzare il testo
    letto prima del gate. Va corretto insieme.
- **Proposta di correzione**:
  - In `mix.py` spostare il gate **dentro** `work_lock` e **dopo** `RunCache.load`,
    passando lo stesso oggetto: `gate = enforce(args, cache.script.to_dict())`.
  - In `retake.py` fare lo stesso in `_do_generate`: `enforce(args, cache.script.to_dict())`
    dentro il lock. Il copione sintetizzato è così quello verificato, come già fa
    `produce()`.
  - In `episode_info` restituire `da_aggiornare` con il motivo `copione` (o un motivo
    `non_approvato`) quando `copione_approvato` è `false`, anche se lo sha256 combacia.

### [MEDIA-2] `mix.py` rimonta un episodio con una voce il cui consenso è stato revocato
- **Dove**:
  - `tools/mix.py:54-91`: nessun controllo del consenso;
  - a confronto: `src/voice_prep.py:84-89` + `check_consent` (produce) e
    `tools/retake.py:189-192` (retake), che la bloccano;
  - anche `tools/retake.py:93-95` (`--list`, anteprime dalla cache, senza controllo).
- **Voce della checklist**: 8. Garanzie di dominio (consenso).
- **Scenario**:
  - Mario Rossi revoca il consenso (`voice_meta.py --revoke-consent`).
  - `produce.py` sulla run esce con exit 3 `ConsentRequired`, anche quando tutte le
    battute sono in cache: la revoca vale per il riuso.
  - L'utente invece cambia le musiche, o sceglie un take, e preme "Rimonta": `mix.py`
    genera un **nuovo** file finale con la voce di Mario e lo stato torna `pronto`.
  - Allo stesso modo `retake.py --list` monta anteprime nuove con quella voce.
  - È il punto "lo stesso per `mix.py`/rimontaggi" lasciato da verificare in C2: chiuso
    per il retake, ma non per il rimontaggio, che con G1 diventa un'azione normale dello
    schermo "Episodio".
  - Nessuna clonazione né sintesi, per questo è media e non alta.
- **Proposta di correzione**:
  - In `mix.py`, dentro il lock e dopo `RunCache.load`, rileggere il consenso degli
    speaker del copione: `plan_voices(config, list(cache.script.speakers))`, poi exit 3
    `ConsentRequired` se uno risulta `revocato`, come in `retake.py:189-192`.
  - Lo stesso controllo in `retake.py --list` prima di montare anteprime nuove.
  - Nel contratto, passo 7: "una voce revocata non si rimonta" (exit 3 per `mix.py`).

### [BASSA-1] Percorsi assoluti nel JSON di `retake.py`, fuori dall'elenco del contratto
- **Dove**:
  - `src/retake.py:314-315` e `:395-396` (`file`, `preview` con `str(path)` assoluto);
  - `tools/retake.py:126-127` (`files`, `take_list`), `:205` e `:227-228` (`previews`,
    `previews_with_music`, `takes`);
  - `tools/retake.py:263`: `str(exc)` nei `warnings`, che sono una risposta di successo e
    non passano da `redact`.
- **Voce della checklist**: 6. Segreti / informazioni esposte.
- **Scenario**:
  - `retake.py --list 3` restituisce in `take_list[].file`/`preview`/`files` percorsi come
    `/Users/<nome>/.../output/<run>/segmenti/<chiave>_t2.wav`.
  - Il contratto ("Campi da mostrare e campi da non mostrare") elenca i campi assoluti da
    non mostrare, ma non questi. Un adattatore che inoltra `take_list` così com'è (il
    contratto la descrive come la lista per la UI) rivela la struttura del server.
  - Un errore di ffmpeg nell'anteprima con musica finisce nei `warnings` senza passare da
    `redact`.
- **Proposta di correzione**:
  - Restituire in `take_list` solo `preview_ui`/`preview_with_music_ui` e un `file_ui`
    (`ui_path`).
  - Togliere `file`/`preview`/`files` dalla risposta, oppure aggiungerli all'elenco del
    contratto dei campi da non mostrare.
  - Passare i `warnings` da `redact`.

### [BASSA-2] `tempi_non_disponibili` riporta il percorso assoluto della run
- **Dove**: `src/episode_info.py:191-195`; il messaggio nasce in
  `src/episode_timeline.py:75-76` (`f"Nessuna timeline in {target}."`).
- **Voce della checklist**: 6. Informazioni esposte.
- **Scenario**:
  - Con un provider in mp3, `write_timings` non scrive `_tempi_turni.json`. Lo stesso
    succede in una run montata prima che esistessero i tempi.
  - `episode_info.py` restituisce allora `"tempi_non_disponibili": "Nessuna timeline in
    /Users/<nome>/.../output/<run>/_tempi_turni.json."`.
  - Il contratto dice che questo campo spiega alla UI perché i tempi mancano, quindi viene
    mostrato. Il messaggio suggerisce anche `tools/synthesize.py`, che in modalità web è
    rifiutato.
- **Proposta di correzione**:
  - In `episode_info` passare il messaggio da `redact`, o meglio usare un testo fisso per
    la UI ("Tempi non disponibili: rimonta l'episodio") con un codice
    (`timeline_assente`, `timeline_vecchia`, `timeline_diversa`).
  - Nei messaggi di `TimelineError` usare `target.name`.

### [BASSA-3] L'elenco dei file della run offre all'adattatore file con percorsi assoluti e voice_id
- **Dove**: `src/episode_info.py:106-128` (`_run_files`); `src/run_layout.py:61-76`
  (`voci_usate.json`), `:86-90` (`documento_originale.percorso.txt`);
  `src/production.py:338-339` (`voci.json`, `_audio_paths.json`).
- **Voce della checklist**: 6. Informazioni esposte; 1. Percorsi (link simbolici).
- **Scenario**:
  - `file[]` elenca, con il loro `percorso`, anche i file interni:
    - `_audio_paths.json`: percorsi assoluti delle registrazioni in `voci_disponibili/`;
    - `voci_usate.json`: `audio_file` assoluto, `voice_id` e `audio_hash`;
    - `voci.json`: i `voice_id` del provider;
    - `documento_originale.percorso.txt`: percorso assoluto del documento.
  - La stima toglie di proposito `voice_id` dalla vista (C2). Un adattatore che rende
    scaricabile ogni voce di `file[]` (il contratto non dice di non farlo) li espone tutti.
  - `entry.is_file()` segue i link simbolici: un link nella run verso un file esterno
    comparirebbe come scaricabile. Oggi nessun tool crea link nelle run, quindi questa
    parte è solo difesa in profondità.
- **Proposta di correzione**:
  - Escludere da `file[]` i file interni con dati del server (`_audio_paths.json`,
    `voci.json`, `voci_usate.json`, `*.percorso.txt`), oppure tenere una whitelist di file
    scaricabili (file finale, `conversazione.json`, documento originale, trascrizioni).
  - Saltare i link simbolici (`entry.is_symlink()`).
  - Nel contratto, scrivere che l'adattatore serve solo `file_finale` e i file con
    `interno: false`, risolvendo il `percorso` dentro `output/<run>`.

### [BASSA-4] `final_basename` prende il nome da `_montaggio.json` senza validarlo
- **Dove**: `src/production.py:250-252` (`Path(str(record["file"])).stem`), usato da
  `src/production.py:313` (`run_dir / basename`, poi `with_suffix` in
  `src/audio_assembler.py`) e da `src/music_mixer.py:349`.
- **Voce della checklist**: 1. Path traversal (difesa in profondità).
- **Scenario**:
  - Un `_montaggio.json` con `"file": "."` (o `"/"`) dà uno stem vuoto: `run_dir / ""` è
    la run stessa, e `with_suffix(".mp3")` diventa `output/<run>.mp3`, **fuori dalla
    run**, accanto alle altre cartelle dell'Archivio.
  - Il file lo scrive solo il tool, con `Path(output_path).name`, e l'adattatore non lo
    tocca: serve una manomissione del file o un bug futuro, per questo è bassa.
  - Sullo stesso percorso: un titolo "CON", "NUL" o "AUX" dà `con.mp3`, che su Windows
    (supportato da `_common.py`) è un nome riservato.
- **Proposta di correzione**:
  - Accettare lo stem del registro solo se combacia con lo schema dello slug
    (`^[^\W_][\w-]{0,59}$` senza `.`, o `slugify(stem) == stem`), altrimenti ricadere sul
    titolo.
  - In `slugify` aggiungere un suffisso ai nomi riservati di Windows.

### [BASSA-5] `--points` libero da 10 a 4000: fino a ~4000 file di cache e decodifiche per run
- **Dove**: `src/waveform.py:37-38` (`_forma_onda_{points}.json`), `:78-98`.
- **Voce della checklist**: 7. File temporanei / disservizio.
- **Scenario**:
  - Ogni valore diverso di `--points` decodifica l'intero episodio con ffmpeg e scrive un
    file di cache.
  - Una UI che passa la larghezza in pixel della finestra, o un client che ripete la
    richiesta con valori diversi, accumula fino a 3991 `_forma_onda_N.json` per run (circa
    50 MB) e altrettante decodifiche. Questi file finiscono poi in `file[]`.
- **Proposta di correzione**:
  - Calcolare e mettere in cache una sola risoluzione alta (es. 4000 picchi) e ricampionare
    in memoria ai `points` chiesti.
  - In alternativa, accettare pochi valori fissi (es. 200, 400, 800, 1600).
  - Escludere `_forma_onda_*` da `file[]`.

### [BASSA-6] Anteprime dei take legate al numero di battuta, non alla chiave: `--list` può far ascoltare audio vecchio
- **Dove**:
  - `src/retake.py:365` e `:386-388`: nome `turno-NNN_tK.wav`, riusato se esiste;
  - `tools/retake.py:270-273`: `_con_musica.wav` scritto con `shutil.copy2`, non in modo
    atomico, e in `--list` senza lock.
- **Voce della checklist**: 7. File temporanei; integrità di ciò che l'utente approva.
- **Scenario**:
  - L'utente ascolta i take della battuta 2: si crea `turno-002_t1.wav`.
  - Poi corregge il testo della battuta 2 ("Cosa dice"), approva e produce: la chiave
    cambia e il take 1 nuovo è il testo nuovo.
  - `retake.py --list 2` trova `turno-002_t1.wav` già presente e lo restituisce come
    anteprima del "Take originale": l'utente sente il **testo vecchio** e sceglie su quella
    base.
  - Lo stesso succede se cambia il take scelto della battuta prima o di quella dopo.
  - Due `--list --with-music` insieme scrivono lo stesso `_con_musica.wav` senza
    temporaneo: chi lo serve può leggerlo a metà.
- **Proposta di correzione**:
  - Nominare le anteprime con la chiave del take e quelle delle battute vicine
    (es. `turno-NNN_<key[:12]>_tK_<hash dei vicini>.wav`), oppure rigenerarle sempre in
    `--list` (costano solo un montaggio locale).
  - Scrivere `_con_musica.wav` con `atomic_output`.

## Verificato e a posto
- **1. Path traversal**:
  - `--run-dir` di `episode_info.py`, `waveform.py`, `mix.py`, `retake.py` e
    `turn_at.py` passa da `run_dir_arg`;
  - `final_file` usa `Path(record["file"]).name`, quindi un `_montaggio.json` manipolato
    non fa leggere né servire un file fuori dalla run, salvo un link simbolico già
    presente nella run (BASSA-3);
  - i nomi della cache della forma d'onda vengono da un `int` validato (10–4000);
  - le anteprime usano `numero:03d` e `take` interi;
  - `--choose` accetta solo interi, e il take deve esistere;
  - lo slug del titolo contiene solo caratteri alfanumerici e `-`: niente `..`,
    separatori o nomi nascosti;
  - nessuna collisione con i file della run: `conversazione.json`, la sessione e gli altri
    file di lavoro non sono `.mp3`/`.wav`, `segmenti/` è una cartella e i nomi con `_`
    davanti perdono il trattino basso.
- **2. Sottoprocessi**:
  - tutte le `subprocess.run` di ffmpeg (`waveform._decode`,
    `AudioAssembler._encode_mp3`, `music_mixer._run_ffmpeg`) hanno argomenti in lista,
    niente `shell=True` e un timeout (`FFMPEG_TIMEOUT_SECONDS = 900`; 30 s per
    ffprobe/doctor);
  - gli input sono percorsi assoluti (run risolta, brani della libreria), quindi un nome
    non diventa un'opzione né un protocollo (`concat:`…). `-protocol_whitelist` non serve:
    nessun input viene da un nome scelto dall'utente;
  - `-nostdin` in `waveform`.
- **3. File non fidati**: niente di nuovo. La forma d'onda decodifica solo il file finale
  prodotto dal tool, a 2 kHz mono (circa 14 MB/ora in memoria).
- **4. Prompt injection**: nessun testo del modello nuovo arriva al TTS. `produce`
  sintetizza l'oggetto verificato, e il percorso `script_edit` → approvazione → `produce`
  paga solo la battuta cambiata (test `[C3]`).
- **5. Tag dei retake**:
  - `--direction` passa da `resolve_directions`: etichette normalizzate per gli spazi e
    solo quelle di `retake.indicazioni`. Una sconosciuta dà `UnknownDirection`, "Pronuncia
    diversa" dà `DirectionNeedsTextEdit`, due opposte danno `ConflictingDirections`;
  - le tag di config sono validate (`^\[[^\[\]\n]{1,40}\]$`);
  - al TTS arriva `"<tag whitelist> <testo approvato>"` (il test lo legge da `tts.log`);
  - la chiave del take resta `cache.key(numero)` del testo approvato, quindi copione e
    sha256 non cambiano;
  - non esiste un argomento per il campo libero del wireframe;
  - `--by` va solo in `take_info.json`;
  - `--config` è vietato in modalità web, quindi la whitelist non si cambia dall'adattatore;
  - `max_takes` vale per ogni richiesta;
  - gate di approvazione prima di spendere (ma vedi MEDIA-1); consenso `revocato` →
    `ConsentRequired` dentro il lock;
  - generazione e scelta sotto `work_lock`; `_record_take_info` legge, modifica e scrive
    sotto quel lock.
- **6. Segreti**:
  - nessuna chiave nei file nuovi (`_montaggio.json`, `take_info.json`, cache della forma
    d'onda);
  - `current_work` non espone il `pid`;
  - eventi ed errori di `produce` passano da `redact`, spostato in `src/redaction.py`;
  - `produce.py` usa `ui_path` (BASSA-3 di C2 corretta); `mix.py` aggiunge `file_ui`, e i
    suoi `output`/`musiche_usate_file` assoluti sono nell'elenco del contratto.
- **7. Lock e temporanei**:
  - `atomic_output` crea il temporaneo nella stessa cartella con `mkstemp` (0600, nome
    nascosto, estensione mantenuta), fa `os.replace` solo se il file non è vuoto e lo
    cancella in `finally`;
  - i file finali ereditano 0600, come i segmenti già scritti con `write_bytes_atomic`:
    va bene se adattatore e tool girano con lo stesso utente;
  - `mix.py` tiene `work_lock("montaggio")` e scrive `_montaggio.json` in modo atomico dopo
    il file;
  - un'interruzione fra i due lascia un registro vecchio, che dà `da_aggiornare`: il
    fallimento è prudente;
  - la cache della forma d'onda si scrive in modo atomico ed è legata a sha256 e nome del
    file.
- **8. Garanzie di dominio, correzioni di C2**:
  - **MEDIA-1 (marcatore)**:
    - il marcatore si scrive nel lock per voce, dopo il ricontrollo del consenso sui byte
      e prima della chiamata al provider, e si toglie solo dopo `registry.record`, dentro
      il blocco con i segnali rinviati;
    - chi arriva dopo (altro processo o rilancio) e non trova la voce nel registro esce con
      `CloneOutcomeUnknown` senza clonare;
    - un segnale fra la scrittura del marcatore e il blocco lascia il marcatore: il
      fallimento è prudente;
    - `--confirm-clone-retry` lo toglie sotto lo stesso lock, quindi non durante una
      clonazione, e richiede `--by`;
    - il marcatore è per nome (digest del nome), nella cartella del registro.
  - **`pthread_sigmask`**: blocca SIGTERM/SIGINT solo nel thread principale, ma il
    processo non ha altri thread (verificato: nessun `threading.Thread` o pool in
    `src/`/`tools/`), quindi un segnale diretto al processo resta in sospeso. La chiamata
    di clonazione ha `retry=False` e un timeout di 180 s (Fish), quindi il blocco non è
    illimitato.
  - **MEDIA-3 (`--expect-sha256`)**: hash calcolato dentro il lock dei metadati;
    obbligatorio con `--consent` in modalità web; `RecordingChanged` senza scrivere;
    `audio_sha256` esposto in `consent_view`.
  - **BASSA-4**: `_validate` dà `VoiceMetaError` per tipi sbagliati; `consent_revoked`
    tratta un file illeggibile come revocato.
  - **MEDIA-2 / BASSA-1 (`EventLog`)**:
    - nome fisso `_eventi.jsonl`;
    - `is_symlink()` più `O_NOFOLLOW`; `O_APPEND`; aperto al primo evento, dopo il lock;
    - `interrotta`/`errore` scritti dentro `produce()` sotto il lock.
  - **BASSA-2 (revoca)**:
    - `stop_or_revoked` rilegge i metadati di tutte le voci a ogni battuta, anche quelle
      in cache, e ferma la produzione con `ConsentRequired`;
    - vale solo per una revoca esplicita: una registrazione sostituita (`superato`) non
      ferma, ma il voice_id è già quello clonato con il consenso.
  - **BASSA-5**: `voices_prepare.py` e `synthesize.py` hanno `web_allowed=False`, che
    scatta dopo il controllo degli argomenti vietati; `bootstrap` rifiuta un tool che non
    è passato da `parse_args`.
  - **Altre garanzie**:
    - G1 (`mix.py`) non chiama mai il TTS;
    - `SegmentsMissing` elenca le battute mancanti;
    - nessun retry sulla clonazione nei retake: `verify_voice` si ferma, non riclona.

## Da verificare nelle prossime fasi
- **Adattatore**:
  - `--confirm-clone-retry`: va mostrato solo dopo `CloneOutcomeUnknown`, con
    l'indicazione di controllare le voci sul provider. Va limitato a chi può autorizzare
    una spesa, e `--by` deve essere l'utente autenticato. La chiamata può aspettare fino a
    600 s il lock per voce: serve un timeout lato adattatore.
  - L'adattatore serve file dati da percorsi relativi (`file_finale.percorso`,
    `preview_ui`, `preview_with_music_ui`, `file[].percorso`): deve risolverli dentro
    `output/<run>` (niente `..`, niente link simbolici) e servire solo i file previsti
    (vedi BASSA-3).
  - Il campo libero "Altre indicazioni" non va mai passato a nessun tool: oggi non esiste
    un argomento che lo accetti, e nessuno va aggiunto senza una revisione.
  - `waveform.py`: usare pochi valori di `--points` (vedi BASSA-5).
- **Librerie (C4)**:
  - la lettura di tutta la registrazione in memoria per il ricontrollo del consenso
    (`voice_prep.py:367`) richiede un limite di dimensione ai caricamenti in
    `voci_disponibili/`;
  - restano valide le note di C2: niente `.json` caricati, niente sostituzioni silenziose,
    voce e `.json` si cancellano insieme.
- **Ancora aperte da C0+C1**: MEDIA-1 (limiti dei documenti), BASSA-1, BASSA-3.
- **`in_corso`**: per l'operazione `revisione_copione` contiene l'`istruzione` scritta
  dall'utente (`src/script_authoring.py:208`). Con più utenti va deciso se gli altri
  possono vederla (non è nel diff).


---

## Esito delle correzioni (aggiunto da chi ha implementato, non dal revisore)

Data: 2026-10-07. Nessuna alta. L'utente ha deciso di correggere tutte le medie e le basse
prima di C4.

- **MEDIA-1**: `mix.py` e `retake.py` (generazione) leggono il copione dentro il lock di
  lavoro e attraversano il gate su **quella** lettura (`enforce(args, cache.script…)`).
  `episode_info` aggiunge il motivo `non_approvato`. In più: `mix.py`, `synthesize.py` e
  `retake.py` passavano al gate il `--run-dir` così come ricevuto, quindi con il solo
  nome della run il gate cercava la sessione nella cartella corrente. Ora ricevono il
  percorso già confinato.
- **MEDIA-2**: `voice_prep.check_not_revoked()`; `mix.py` e `retake.py --list` (battuta e
  vicine) escono con exit 3 `ConsentRequired` se una voce è revocata.
- **BASSA-1**: nel JSON di `retake.py` tutti i percorsi sono relativi (`output/<run>/…`),
  `warnings` passano da `redact`. Tre test esistenti (tools_cli, music, web) risolvono ora
  il percorso dalla cartella che contiene `output/`.
- **BASSA-2**: `TimelineError` ha un `code` e i suoi messaggi usano solo nomi;
  `tempi_non_disponibili` è `{codice, messaggio}` con un testo fisso per la UI.
- **BASSA-3**: `file[]` esclude link simbolici, `_audio_paths.json`, `voci.json`,
  `voci_usate.json`, `*.percorso.txt`, `_forma_onda_*`; il contratto dice che si servono
  solo `file_finale` e i file con `interno: false`.
- **BASSA-4**: `final_basename` accetta il nome del registro solo se è già uno slug;
  `slugify` aggiunge `-episodio` ai nomi riservati di Windows.
- **BASSA-5**: `--points` solo fra 50, 100, 200, 400, 800, 1600.
- **BASSA-6**: le anteprime hanno nel nome un hash dei segmenti montati (take e battute
  vicine); `_con_musica.wav` scritto con `atomic_output`.
- Test: web_integration da 488 a 499; le altre suite verdi.

