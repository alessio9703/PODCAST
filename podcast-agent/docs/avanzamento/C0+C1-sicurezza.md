# Revisione di sicurezza · C0+C1

Intervallo esaminato: `33068cc..HEAD` (+ modifiche non committate: solo `.DS_Store` e
`.claude/agents/security-reviewer.md`, non rilevanti)
File esaminati: 49 (esclusi i `.DS_Store`). Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 2 |
| Media | 1 |
| Bassa | 3 |

Il gate di approvazione (sha256), i lock e le scritture atomiche introdotti in C0/C1 sono
solidi: nessun percorso nuovo fa arrivare al TTS testo non approvato, e i lock non hanno
ordini incrociati. I due problemi alti riguardano invece i **percorsi**: i nuovi comandi del
passo 1 rendono raggiungibile dal web una risoluzione del documento che legge qualsiasi file
(compreso `.env` con le chiavi), e nessun tool verifica che `--run-dir` stia dentro `output/`.
Vanno corretti prima di scrivere l'adattatore.

## Problemi

### [ALTA-1] `--documento` / `--document` leggono qualsiasi file, compreso `.env`
- **Dove**:
  - `src/pdf_reader.py:68-85` (`locate_document`: percorsi assoluti e `..` accettati; un
    nome "nudo" che non c'è in `documenti/` viene cercato **nella cartella corrente**,
    righe 76-78);
  - `src/pdf_reader.py:95` (un file **senza estensione** viene letto come testo: `.env`,
    `id_rsa`, `passwd`);
  - punti nuovi che lo espongono al web: `src/episode_setup.py:56-58` (`set_document`, usato
    da `tools/episode_config.py --documento` e `tools/run_init.py:40,43 --document`),
    `tools/doc_extract.py:45` e `:67` (`preview` nel JSON), `tools/script_write.py:69`;
  - `src/episode_config.py:404` salva il percorso assoluto in `episodio.json`.
- **Voce della checklist**: 1. Path traversal; 6. Segreti.
- **Scenario** (verificato con file finti in una cartella temporanea):
  - `doc_extract.py .env` lanciato con cwd che contiene un `.env` → exit 0, JSON con
    `"preview": "ANTHROPIC_API_KEY=sk-ant-FINTA-123"`. Il nome non contiene né `/` né `..`:
    un filtro dell'adattatore sui separatori **non lo ferma**. Se l'adattatore lancia i
    tool dalla radice del progetto (com'è naturale), è il `.env` vero con le chiavi Anthropic
    ed ElevenLabs;
  - `doc_extract.py ../elsewhere/id_rsa` → contenuto nella `preview`;
  - `episode_config.py --run-dir R --documento /percorso/assoluto/.env` → il testo finisce in
    `R/documento_estratto.txt`, il percorso in `episodio.json`, e al passo 4
    `script_write.py --run-dir R` lo **manda al modello** e lo salva in
    `_script_session.json` (`document_text`);
  - estensioni non gestite (`config.yaml`) danno `UnsupportedDocument` e i file inesistenti
    `Documento non trovato: /abs/...`: si può sondare il filesystem.
- **Proposta di correzione**:
  1. In `src/pdf_reader.py` aggiungere `locate_in_library(name, documents_dir)` che:
     accetta solo un nome semplice (`Path(name).name == name`, niente separatori, non
     inizia con `.`), costruisce `candidate = documents_dir / name`, rifiuta
     `candidate.is_symlink()`, verifica
     `candidate.resolve().is_relative_to(documents_dir.resolve())` e **non** ripiega sulla
     cartella corrente.
  2. Usarla in `episode_setup.set_document`, `run_init.py`, `doc_extract.py` e
     `script_write.py --document`. Il comportamento attuale (percorso libero) resta solo
     dietro un flag esplicito per l'uso da CLI e dal subagent (es. `--percorso-libero`),
     da documentare nel contratto come "mai dall'adattatore".
  3. Togliere `suffix == ""` da `extract_text` (`pdf_reader.py:95`): un file senza
     estensione è `UnsupportedDocument`.
  4. Nei messaggi d'errore e in `episodio.json` riportare solo il nome, non il percorso
     assoluto (vedi anche BASSA-2).

### [ALTA-2] Nessun tool verifica che `--run-dir` / `--session` stiano dentro `output/`
- **Dove**: `tools/_common.py:193-194` (`resolve_path` risolve e basta). Lo usano senza altri
  controlli:
  - `tools/episode_config.py:76`;
  - `tools/script_write.py:132`, `tools/script_revise.py:39` (anche `--session`);
  - `tools/script_approve.py:52-57` (anche `--session`);
  - `tools/script_edit.py:38`, `tools/script_show.py:35`, `tools/script_versions.py:58`;
  - `tools/music_set.py:56`.
  
  Inoltre `src/fileio.py:91` crea le cartelle mancanti (`mkdir(parents=True)`) quando prende
  un lock.
- **Voce della checklist**: 1. Path traversal.
- **Scenario** (verificato in una cartella temporanea):
  - `episode_config.py --run-dir <cartella qualsiasi esistente> --titolo x` → exit 0;
    nella cartella compaiono `episodio.json`, `documento_estratto.txt` ed
    `episodio.json.lock`. Il contenuto è in parte scelto dall'utente (titolo, nomi,
    descrizioni);
  - `script_approve.py --run-dir /qualunque/percorso/nuovo` crea l'intero albero di
    cartelle e un `.lock` (session_lock → `file_lock` → `mkdir(parents=True)`), poi fallisce;
  - `script_show.py --run-dir ../altra_istanza/output/<run>` legge copione e sessione di
    una run che l'utente non vede in Archivio;
  - `script_edit.py` / `script_versions.py --restore` scrivono `conversazione.json` e
    `_script_session.json` in qualunque cartella che contenga una sessione.

  Basta che l'adattatore inoltri l'identificativo della run arrivato dal browser (es.
  `?run=../../x`): è proprio il valore che il modello di minaccia lascia scegliere
  all'utente. La difesa deve stare nei tool, che sono il confine del contratto.
- **Proposta di correzione**:
  - In `tools/_common.py` aggiungere `resolve_run_dir(value, config, *, must_exist=True)`.
    Deve:
    - risolvere il percorso;
    - verificare `run.parent == config.output_dir.resolve()` (figlio diretto di
      `output/`);
    - rifiutare i link simbolici (`Path(value).is_symlink()` prima del resolve);
    - con `must_exist`, richiedere `is_dir()`;
    - in caso di errore sollevare un `DomainError(error_type="RunDirNotAllowed")`.
  - Per `--session`: stessa verifica sul `parent` e `name == SESSION_FILENAME`.
  - Usarla in tutti i tool elencati sopra al posto di `resolve_path`.
  - In `src/run_lock.session_lock` e `work_lock` non creare cartelle: se `run_dir` non
    esiste, errore.
  - Aggiungere un test che passi `..`, un assoluto fuori da `output/` e un symlink, per ogni
    tool del passo 1-4.

### [MEDIA-1] Nessun limite di dimensione o di pagine sui documenti; estrazione sotto lock
- **Dove**:
  - `src/pdf_reader.py:137-155`: tutte le pagine, senza limite;
  - `src/pdf_reader.py:182-190`: `read_text` del file intero, fino a tre volte con
    codifiche diverse;
  - `src/pdf_reader.py:168-174` (DOCX): python-docx legge `document.xml` per intero dallo
    zip;
  - `src/episode_setup.py:126-132`: l'estrazione avviene **dentro** il lock di
    `episodio.json`;
  - `tools/run_init.py:40` e `:43`: lo stesso documento si estrae due volte;
  - `src/script_session.py:189-190`, `src/script_authoring.py:152,162`: il testo
    **intero** (il troncamento a `max_document_chars` vale solo per il prompt) finisce in
    `_script_session.json`, che viene riscritto a ogni modifica di battuta, approvazione e
    ripristino.
- **Voce della checklist**: 3. File non fidati.
- **Scenario**:
  - Misurato: un PDF di **2,5 MB con 20.000 pagine vuote** occupa l'estrazione per circa
    9 s, solo per arrivare a `scansione`. Pagine con contenuto, o un numero di pagine
    maggiore, scalano linearmente.
  - Per tutto quel tempo `episodio.json.lock` resta preso: un secondo utente sulla stessa
    bozza riceve `LockTimeout` dopo 30 s.
  - Un `.txt` da 500 MB porta il processo a diversi GB: la stringa, poi `split()` per il
    conteggio delle parole, poi la copia in `documento_estratto.txt`, poi il JSON della
    sessione, riscritto a ogni `script_edit`.
  - Un DOCX con un `document.xml` molto comprimibile (bomba zip) ha lo stesso effetto.
  - Le decompressioni interne ai PDF sono invece limitate da pypdf 6.18.1
    (`ZLIB_MAX_OUTPUT_LENGTH` e simili), e python-docx non risolve le entità XML.
- **Proposta di correzione**:
  - In `src/pdf_reader.py` aggiungere limiti da `config.yaml`, controllati **prima** di
    leggere: es. `documents.max_bytes` (20 MB), `max_pages` (500), `max_chars`
    (2.000.000).
  - Errore dedicato: `DocumentTooLarge` (exit 1, con `details` su limite e valore).
  - Per i PDF: `len(reader.pages)` prima del ciclo. Per i DOCX: somma di `file_size` delle
    voci in `zipfile.ZipFile(path).infolist()` prima di aprirlo con python-docx. Per il
    testo: `stat().st_size`.
  - In `apply_changes` estrarre **prima** di prendere `locked_update` e dentro il lock solo
    registrare.
  - In `run_init.py` riusare il `Document` già estratto.
  - Nella sessione salvare `document_text` già troncato a `max_document_chars`, oppure
    solo un riferimento a `documento_estratto.txt`.

### [BASSA-1] `script_approve.py --version` è facoltativo
- **Dove**: `tools/script_approve.py:46` e `:80`; `src/script_editing.py:44-45`.
- **Voce della checklist**: 8. Garanzie di dominio.
- **Scenario**: il contratto chiede `--version V`, ma il tool approva anche senza.
  1. L'utente A guarda la v3.
  2. Un `script_revise.py` dell'utente B crea la v4. Il testo della v4 può essere stato
     influenzato da una prompt injection nel documento.
  3. Un adattatore che omette `--version` per errore approva la v4, che A non ha mai letto.

  Il gate sullo sha256 la considera poi approvata. Dipende da un errore dell'adattatore,
  quindi è difesa in profondità.
- **Proposta di correzione**: rendere `--version` obbligatorio per l'approvazione, cioè
  quando manca `--revoke` (errore se manca). Per l'uso del subagent si può tenere un flag
  esplicito `--current` documentato.

### [BASSA-2] Percorsi assoluti e informazioni interne nei JSON e negli errori
- **Dove**:
  - `src/pdf_reader.py:81-85` ("Documento non trovato: /percorso/assoluto");
  - `src/episode_config.py:404` (`documento.percorso` in `episodio.json` e nella vista di
    `episode_config.py`);
  - `tools/doc_extract.py:58` (`path`);
  - `src/run_lock.py:66` (`pid` in `_lavoro.json`, restituito in
    `RunBusy.details.in_corso` e in `script_show.in_corso`);
  - `session_file` e `run_dir` assoluti in quasi tutti i tool.
- **Voce della checklist**: 7. Lock e file temporanei (informazioni nei file); 1 (sondaggio
  del filesystem).
- **Scenario**: l'utente vede nel browser la struttura delle cartelle del server
  (`/Users/<nome>/Library/CloudStorage/...`) e il pid dei processi. Combinato con ALTA-1,
  messaggi diversi per "non esiste" e "estensione non supportata" permettono di sapere
  quali file esistono.
- **Proposta di correzione**: nei campi destinati alla UI restituire nomi relativi
  (`documento.nome`, `run` = nome della cartella). Togliere `pid` dalla vista
  (`current_work`) e tenerlo solo nel file. Nei messaggi di `locate_document` usare solo
  `raw.name`. Le chiavi esistenti si possono tenere per il subagent, ma il contratto deve
  dire all'adattatore di non mostrarle.

### [BASSA-3] Corsa nella creazione della cartella della run
- **Dove**: `src/run_layout.py:34-37`. `exists()` e poi `mkdir(parents=True)` senza
  `exist_ok`.
- **Voce della checklist**: 7. Lock e file temporanei (TOCTOU).
- **Scenario**:
  1. Due utenti creano nello stesso minuto un episodio con lo stesso titolo.
  2. Entrambi vedono la cartella libera, uno dei due `mkdir` fallisce con
     `FileExistsError`.
  3. Il secondo riceve un errore generico (exit 1). Non si perdono dati, ma è un
     disservizio senza un `error_type` utile.
- **Proposta di correzione**: ciclo `try: run_dir.mkdir(parents=False) / except
  FileExistsError: suffix += 1`. Il `mkdir` atomico è il controllo, non `exists()`.
  `output_root.mkdir(parents=True, exist_ok=True)` si fa una volta prima del ciclo.

## Verificato e a posto
- **2. Sottoprocessi.** `src/ffmpeg_tool.media_duration_seconds` (nuovo):
  - argomenti in lista, nessun `shell=True`, `timeout=30` su ffprobe e su ffmpeg;
  - i percorsi arrivano da `MusicLibrary`, cioè da `config.music_dir`, che `Config` risolve
    in assoluto (`src/config.py:80`): un nome di file che inizia con `-` diventa
    `/…/musiche/intro/-x.mp3` e non è letto come opzione.
  
  Nessun altro sottoprocesso nel diff.
- **4. Prompt injection.** Testo del documento, istruzioni di revisione, nomi pronunciati e
  descrizioni dei ruoli entrano nel prompt. Nomi e descrizioni sono ridotti a una riga
  (`episode_config.py:257,272`), quindi non possono iniettare blocchi strutturati.
  - Tutto ciò che il modello restituisce diventa solo una **versione** della sessione
    (`script_authoring.py:176,217`), mostrata all'utente.
  - `approve()` lega l'approvazione allo sha256 del copione corrente.
    `approval_problem()` lo confronta con `conversazione.json` letto dal disco al momento
    della sintesi (`tools/_approval.py:62-71`).
  - Una nuova stesura o revisione cambia l'hash e l'approvazione decade. Lo stesso vale per
    il ripristino, che in più la ritira esplicitamente (`script_editing.py:152`), e per i
    cambi di cast (`episode_setup.py:148`).
  - Il nome del file d'uscita passa da `slugify`, che tiene solo caratteri alfanumerici.
  
  Una injection non può quindi far sintetizzare nulla che l'utente non abbia visto e
  approvato (salvo BASSA-1).
- **5. Tag dei retake.** Nel diff non c'è codice di retake. Le tag oggi entrano solo
  attraverso il testo del copione (modello o `script_edit.py`), quindi passano
  dall'approvazione sha256.
- **6. Segreti.**
  - `classify_api_error` (`src/script_writer.py`) usa solo `status`, `message` e
    `retry-after`, mai la richiesta né gli header; i test C1 lo controllano.
  - `ConfigError` per la chiave mancante stampa solo il nome della variabile.
  - `load_dotenv` non stampa nulla. `_lavoro.json` e la sessione non contengono chiavi.
  - `client_factory` non è impostato in `config.yaml`.
  
  L'unica fuga trovata passa dalla lettura dei documenti (ALTA-1).
- **7. Lock e file temporanei.**
  - `file_lock`: `flock` non bloccante con scadenza, rilascio nel `finally`, fd chiuso
    sempre.
  - I lock non sono mai annidati in ordini diversi:
    - `work_lock` e `session_lock` sono **in sequenza**, non annidati
      (`script_authoring.py:137-142`, `202-208`);
    - `apply_changes` rilascia il lock di `episodio.json` prima di prendere quello della
      sessione (`episode_setup.py:126-148`);
    - nessun deadlock possibile.
  - Il contenuto viene riletto dentro il lock (`registry.record`, `edit_turn`,
    `restore_version`, `revise_draft`).
  - Temporanei con `mkstemp` (nome unico, permessi 0600) nella stessa cartella, rimossi
    in caso di errore. Effetto collaterale: i JSON riscritti ora hanno permessi 0600
    invece di 0644, cosa più restrittiva e non un problema di sicurezza; da tenere
    presente se un altro utente di sistema deve leggerli.
  - `_lavoro.json` si considera solo mentre il lock è tenuto.
- **8. Garanzie di dominio.**
  - Gate sha256 intatto, e rafforzato (`script_approve.py:83-92` controlla anche il file
    su disco).
  - Versioni con errori non approvabili (`ScriptHasErrors`).
  - Voci risolte senza indovinare: `resolve_voice` accetta solo la corrispondenza esatta
    a meno di maiuscole e accenti e rifiuta gli indici numerici. Il resto dà
    `AmbiguousName` o un errore.
  - Registro delle voci aggiornato sotto lock.
  - Nessun bypass nuovo: `--no-approval-gate` era già presente ed è invariato.
  - Il cast incompleto si ferma prima di spendere (`Cast.check_before_spending`).

## Da verificare nelle prossime fasi
- **Retake (whitelist di tag)**: verificare che le indicazioni di resa siano mappate su tag
  da configurazione e che nessun testo libero arrivi al TTS fuori dal copione approvato.
- **Consenso e clonazione**: consenso registrato prima di ogni clonazione, nessun retry,
  `voices_prepare.py` e `synthesize.py` chiamati dall'adattatore **mai** con
  `--no-approval-gate`.
- **Caricamenti** (`documenti/`, `voci_disponibili/`, `musiche/`), a carico
  dell'adattatore:
  - nome del file ridotto a un nome semplice, senza `..`, separatori o punto iniziale;
  - niente symlink;
  - dimensione massima in streaming;
  - niente sovrascrittura silenziosa di un file con lo stesso nome. Una voce o un brano
    sostituiti cambiano la persona o la musica di un copione già approvato: per le voci
    serve uno sha256 nel registro o nel consenso; per le musiche c'è già il controllo
    sha256.
  - Valutare se far girare ffmpeg (`media_duration_seconds`) su file caricati con
    `-protocol_whitelist file` e un formato forzato: un file "mp3" che contiene una
    playlist o un `ffconcat` potrebbe far aprire a ffmpeg altri file o URL.
- **Argomenti da non esporre mai all'utente web**, da elencare nella sezione "a carico
  dell'adattatore" del contratto (C5):
  - `--config`: `script.client_factory` importa ed esegue un modulo qualsiasi;
  - `--save-to` di `doc_extract.py`: scrive ovunque;
  - `--text-file` e `--document` di `script_write.py`;
  - `--session`;
  - `--script-json` di `script_save.py`;
  - `--script` di `synthesize.py` ed `estimate.py`;
  - `--no-approval-gate`.
- **Archivio**: l'elenco delle run deve derivare da `output/` e l'adattatore deve passare ai
  tool solo run di quell'elenco (in aggiunta alla correzione di ALTA-2). Lo stato "in
  corso" deve derivare da `is_locked`, come previsto.
- **Librerie**: lock e rilettura anche per i metadati di voci e musiche, come per il
  registro.
- **`tools/lessons.py --propose`**: chiama l'API fuori da `classify_api_error`. Verificare
  che i suoi errori non riportino header o richiesta, se l'adattatore lo esporrà.


---

## Esito delle correzioni (aggiunto da chi ha implementato, non dal revisore)

Data: 2026-10-07. Le due alte sono corrette, come da regola, prima di C2. Medie e basse
restano aperte, da decidere insieme.

### ALTA-1 · corretta
- `src/pdf_reader.locate_document(..., confined=True)` (modalità dei tool per default):
  - il documento deve risolvere, link simbolici compresi, dentro `documenti/`;
  - un nome nudo si cerca **solo** lì, mai nella cartella corrente;
  - file nascosti e file senza un'estensione gestita danno `UnsupportedDocument`, e il
    controllo avviene prima di guardare se il file esiste;
  - il messaggio "non trovato" riporta solo il nome.
- Usata da `doc_extract.py`, `run_init.py`, `episode_config.py` (`set_document`),
  `script_write.py --document` e `run_finalize.py --document`.
- Il percorso libero resta solo dietro `--trusted-paths`, per subagent e CLI, documentato
  nel contratto come "mai dall'adattatore". `.claude/agents/podcast-agent.md` lo usa per
  `doc_extract.py` e `run_finalize.py`.
- `run.py` non passa dai tool e resta invariato (modalità libera, file senza estensione
  compresi): è uso locale.
- Differenza rispetto alla proposta: dentro la libreria si accettano anche sottocartelle e
  link simbolici che **restano** nella libreria. Il controllo è sul percorso risolto.

### ALTA-2 · corretta
- `src/paths.confine_run_dir`, usata tramite `tools/_common.run_dir_arg` da **tutti** i tool
  con `--run-dir`/`--session`. Il percorso risolto (link simbolici compresi) deve essere una
  sottocartella diretta di `output/`; altrimenti `PathNotAllowed`.
- `--session` vale come la sua cartella: il nome del file viene ignorato e si usa
  sempre `_script_session.json`.
- `src/fileio.file_lock` non crea più cartelle: se manca la cartella, errore.
- Anche gli altri file passati per percorso (`--script`, `--voices`, `--out`,
  `--audio-paths`, `--script-json`, `--text-file`, `--save-to`) devono stare in
  `output/`, salvo `--trusted-paths`.
- Verifica: nessun tool chiama più `resolve_path` su un valore da input.

### Test
- `test_web_integration.py`, sezione `[sicurezza]`, 24 controlli nuovi:
  - `.env`, file senza estensione, `../`, percorso assoluto e link simbolico verso fuori,
    anche dopo `--documento` e `run_init --document`;
  - `--save-to` fuori;
  - `--trusted-paths` che funziona;
  - `--run-dir` fuori, con `..`, con un link simbolico, uguale a `output/` stessa;
  - `--session` fuori, nessuna cartella creata, sei tool del passo 1-4;
  - il solo nome della run.
- La riproduzione originale del revisore (`doc_extract.py .env` con un `.env` nella
  cartella corrente) ora dà `UnsupportedDocument` senza contenuto, anche lanciata dalla
  cartella del progetto.
- `test_tools_cli.py`: aggiunto `--trusted-paths` in due chiamate (`doc_extract.py` con
  documento e `--save-to` fuori dal progetto, `run_finalize.py --document`). Riproducono
  il flusso locale del subagent, che ora richiede il flag. Nessun altro cambiamento.
- Tutte le suite verdi.

### Aperte (da decidere)
- **MEDIA-1**: limiti di dimensione e di pagine; estrazione fuori dal lock; doppia
  estrazione in `run_init`; testo intero nella sessione.
- **BASSA-1**: `--version` obbligatorio per approvare.
- **BASSA-2**: percorsi assoluti e `pid` nei JSON e negli errori.
- **BASSA-3**: corsa nella creazione della cartella della run.
