# Revisione di sicurezza · C0+C1-correzioni

Intervallo esaminato: modifiche non committate rispetto a `HEAD` (`d29ae0c`), cioè `git diff HEAD`
(esclusi i `.DS_Store`).
File esaminati: 36. Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 2 |
| Bassa | 4 |

Le correzioni di ALTA-1/ALTA-2 (già fatte) e di BASSA-1/2/3 sono corrette, e la modalità web
regge: il controllo guarda i valori già interpretati da argparse, quindi abbreviazioni, forma
`--opt=valore` e alias sono coperti, e `bootstrap()` blocca i tool che lo saltano. Non ho trovato
modi per aggirarla. Restano aperti due punti. Il primo: MEDIA-1 della revisione precedente è
corretta solo a metà, perché i limiti su byte, pagine e caratteri non limitano il **costo**
dell'estrazione. Un PDF o un DOCX piccolo e sotto tutti i limiti tiene occupato il processo per
minuti o ore. Il secondo è un insieme di difese in profondità minori.

Test eseguiti: `tests/test_web_integration.py` → 283 controlli superati, 0 falliti. Le prove
che seguono sono state fatte con script nella cartella temporanea della sessione, senza toccare
il progetto.

## Problemi

### [MEDIA-1] PDF piccolo e sotto i limiti: estrazione per ore (nessun limite di tempo né di lavoro)
- **Dove**: `src/pdf_reader.py:261-276` (`_read_pdf`: controllo pagine, poi `page.extract_text()`
  per ogni pagina); `config.yaml:104-114`.
- **Voce della checklist**: 3. File non fidati
- **Scenario**: un PDF di **6 KB** con 4 pagine che puntano tutte allo **stesso** content stream
  Flate (`/Contents 3 0 R`). Lo stream si scompatta in 2 MB di `BT /F1 1 Tf ( ) Tj ET`, cioè testo
  fatto solo di spazi. Risultato misurato: 2,0 s per pagina, 8,5 s in tutto, e alla fine
  `DocumentNotExtractable` (perché `content.strip()` è vuoto, `chars` non cresce mai e il limite
  `max_chars` non scatta). Il costo è lineare, circa 1 s per MB scompattato per pagina. pypdf
  6.18 lascia arrivare uno stream fino a `ZLIB_MAX_OUTPUT_LENGTH` = 75 MB, e uno stream ripetitivo
  da 75 MB compresso pesa qualche centinaio di KB. Con 500 pagine (il massimo ammesso) che lo
  condividono si arriva a circa 500 × 75 s, cioè **10 ore** di CPU per un file di poche centinaia
  di KB che supera `max_bytes`, `max_pages` e `max_chars`. Il lock non è più tenuto (bene), ma il
  processo del tool resta occupato, e con l'adattatore web basta caricare il file e scegliere il
  documento.
- **Proposta di correzione**:
  1. In `_read_pdf` un **budget di lavoro** indipendente dal testo: prima di `extract_text` si
     somma la lunghezza dei content stream decodificati della pagina (`page.get_contents()` →
     `len(get_data())`, contando di nuovo ogni volta gli stream condivisi) e ci si ferma con
     `DocumentTooLarge(limite="contenuto_pagine")` oltre una soglia nuova `documents.max_content_bytes`
     (es. 50 MB in tutto).
  2. Anche una **scadenza** `documents.max_seconds` (es. 60), controllata fra una pagina e
     l'altra con `time.monotonic()`.
  3. Nel contratto, sezione "a carico dell'adattatore" (C5): timeout sul sottoprocesso per
     `doc_extract`, `run_init --document` ed `episode_config --documento`, perché una singola
     pagina non è interrompibile dall'interno.

### [MEDIA-2] DOCX da 200 KB: minuti di CPU e GB di memoria entro `max_unpacked_bytes`
- **Dove**: `src/pdf_reader.py:290-311` (`_check_docx_unpacked`), `src/pdf_reader.py:324-331`
  (`docx.Document` + `document.paragraphs`); `config.yaml:114` (`max_unpacked_bytes: 104857600`).
- **Voce della checklist**: 3. File non fidati
- **Scenario**: un DOCX valido con `word/document.xml` fatto di `<w:p/>` ripetuti. Misurato:
  - 10 MB di XML (file da 50 KB): 20 s, 413 MB di RSS;
  - 30 MB di XML (file da 80 KB): 60 s, 665 MB di RSS.

  Al limite di 100 MB (file da ~200 KB, ben sotto `max_bytes`) si stimano circa 200 s e più di
  1,5 GB. Il costo è in `python-docx`, che costruisce un oggetto per ciascuno dei milioni di
  paragrafi vuoti, quindi `max_chars` non aiuta. Un caso collegato: uno zip da 18 MB con 200.000
  entrate vuote supera il controllo in 5,8 s con 118 MB di picco, poi `python-docx` riapre l'indice.

  Il controllo dello zip, invece, **non è aggirabile**: ho dichiarato `file_size` = 10 su
  un'entrata da 50 MB (indice centrale e header locale), e `zipfile` tronca alla dimensione
  dichiarata e solleva `BadZipFile: Bad CRC-32`, con l'eccezione catturata come `illeggibile`.
  `infolist()` conta anche le entrate duplicate, e con zip64 vale la dimensione dell'extra field,
  che `zipfile` fa rispettare allo stesso modo.
- **Proposta di correzione**: in `_check_docx_unpacked`
  1. un limite separato e più basso per le **parti XML** (`*.xml`, `*.rels`), ad esempio
     `documents.max_xml_bytes` = 15 MB. Le immagini in `word/media/` restano coperte da
     `max_unpacked_bytes`;
  2. un limite sul **numero di entrate** (`len(infolist())`, ad esempio 2.000, mentre un DOCX
     reale ne ha qualche decina);
  3. lo stesso timeout lato adattatore di MEDIA-1.

### [BASSA-1] `script_approve.py --current` è accettato in modalità web
- **Dove**: `tools/script_approve.py:52-58` (gruppo `--version`/`--current`), `:61`
  (`parse_args(parser)` senza `web_forbidden`).
- **Voce della checklist**: 8. Garanzie di dominio (difesa in profondità sul gate)
- **Scenario**: l'adattatore, per un bug o per comodità, passa `--current`. Un utente approva
  mentre un altro ha appena salvato una modifica, e viene approvata una versione che il primo non
  ha letto. Il gate sha256 resta valido, ma lega l'approvazione al testo sbagliato. È proprio il
  caso che BASSA-1 della revisione precedente voleva chiudere, e il contratto dice "L'interfaccia
  passa sempre `--version`", ma il codice non lo impone.
- **Proposta di correzione**: `parse_args(parser, web_forbidden=("--current",))` in
  `script_approve.py`, e una riga in più nella tabella "Modalità web" del contratto.

### [BASSA-2] `--out`, `--voices`, `--audio-paths` non sono nell'elenco web
- **Dove**: `tools/_common.py:111-121` (`WEB_FORBIDDEN_ARGS`); `tools/voices_prepare.py:29,103`;
  `tools/synthesize.py:31`; `tools/run_finalize.py:25-30`.
- **Voce della checklist**: 1. Path traversal (difesa in profondità)
- **Scenario**: la regola dichiarata è "file passati per percorso: l'adattatore lavora per
  `--run-dir`", ma questi tre restano ammessi e sono confinati solo a tutto `output/`, non alla
  run. Esempio: `voices_prepare.py --speakers X --run-dir output/A --out output/B/conversazione.json`
  (A approvata) sovrascrive il copione di **un'altra run B** con la mappa delle voci, e accanto
  scrive `output/B/_audio_paths.json`. Serve che l'adattatore passi un `--out` scelto dall'utente,
  che è poco realistico, da qui la gravità bassa.
- **Proposta di correzione**: aggiungerli a `WEB_FORBIDDEN_ARGS`, oppure, se in C2 servono dal
  web, confinarli alla run (`confine_to(value, run_dir, ...)` invece di `config.output_dir`).
  Quando in C2 arrivano sintesi e stima per `--run-dir`, i nomi dei file vanno fissati dal tool
  (`voci.json`, `_audio_paths.json`) e non letti dagli argomenti.

### [BASSA-3] Errori di argparse: exit 2 senza JSON su stdout
- **Dove**: `tools/_common.py:149` (`parser.parse_args()` dentro `parse_args`); `run_tool`
  (`tools/_common.py:286-301`) non intercetta `SystemExit`.
- **Voce della checklist**: robustezza del contratto (exit 2 = "provider, nessun Riprova")
- **Scenario**: una battuta che comincia con un trattino, normale nei dialoghi in italiano
  (`script_edit.py --text "- Sì, certo"`), o un titolo come `--title "-Speciale"`. argparse
  interpreta il valore come un'opzione, stampa l'uso su stderr ed esce con **2** senza nessun JSON
  (verificato: `script_show.py --run-dir x --bogus` → exit 2, stdout vuoto). L'adattatore lo legge
  come errore del provider, quindi blocco e nessun "Riprova", e trova uno stdout che non rispetta
  il contratto.
- **Proposta di correzione**: in `parse_args`, una sottoclasse o un `parser.error` sostituito che
  solleva `DomainError(error_type="InvalidArguments")`, così si esce con 1 e il JSON. Nel
  contratto: l'adattatore passa i valori liberi sempre nella forma `--opzione=valore`.

### [BASSA-4] Sanificazione dei percorsi incompleta (`_redact` e stderr di successo)
- **Dove**: `tools/_common.py:240-262` (`_redact`); messaggi su stderr con percorsi assoluti in
  `tools/run_finalize.py:54`, `tools/script_write.py:163`, `tools/script_revise.py:43`,
  `tools/script_save.py:181`, `tools/signals.py:41`, `tools/doc_extract.py:63`, `tools/retake.py:162`.
- **Voce della checklist**: 6. Segreti / informazioni (messaggi che rivelano più del necessario)
- **Scenario**:
  - su Windows, che il progetto supporta (vedi la riconfigurazione UTF-8), `str(OSError)` riporta
    il percorso come `repr`, con le barre rovesciate raddoppiate (`'C:\\Users\\...'`), e il
    semplice `replace` su `PROJECT_ROOT` non le trova, quindi il percorso assoluto arriva al
    browser;
  - un percorso fuori dal progetto e fuori dalla home (ad esempio `paths.output_dir: /srv/...`, o
    `/private/var/...` su macOS) passa così com'è;
  - casi solo estetici: `/Users/cmp1250/x` diventa `~0/x`, e una cartella accanto
    `podcast-agent2/` diventa `.2/`.
  - i messaggi di **successo** su stderr non passano da `_redact` e stampano il percorso assoluto
    della run, mentre il contratto prevede di poter mostrare stderr in un pannello "dettagli".

  `_redact` non rompe nulla: tocca solo `error`, `details` e lo stderr degli errori, le chiavi
  non stringa e le tuple sono gestite, e le risposte di successo restano invariate come previsto.
- **Proposta di correzione**: in `_redact` sostituire anche le varianti `repr`
  (`root.replace("\\", "\\\\")`) e le radici delle cartelle configurate (`output_dir`,
  `documents_dir`, `voices_dir`, `music_dir`) con il loro nome. Usare un'espressione regolare con
  il confine del separatore dopo la home. Nelle `note()` elencate usare `run_dir.name` e i
  percorsi relativi, oppure applicare `_redact` dentro `note()`.

## Verificato e a posto

- **Modalità web, aggiramenti**:
  - Il controllo guarda `getattr(args, dest) != action.default`, quindi copre abbreviazioni
    (`--conf=a` → rifiutato, verificato), la forma `=`, gli alias (`option_strings`) e le
    ripetizioni. Gli argomenti vietati hanno tutti default `None` o `False`, che un valore passato
    non può riprodurre.
  - Nessun tool usa `parse_known_args`, `fromfile_prefix_chars` o sottoparser. Nessun argomento
    posizionale è fra quelli vietati: l'unico, `doc_extract.py document`, è confinato a
    `documenti/`.
  - Tutti i 24 tool con argomenti usano `parse_args(parser)`. `doctor.py` non ha argomenti e
    `bootstrap()` rifiuta chi salta il controllo (c'è un test).
  - Valori strani di `PODCAST_AGENT_WEB` (qualunque valore che non sia vuoto, 0, false, no o off)
    accendono la modalità. `.env` non può spegnerla, perché `load_dotenv` non sovrascrive
    l'ambiente e viene chiamata dopo il controllo.
  - Il rifiuto avviene prima di qualunque effetto e produce il JSON (exit 1). `--config` non ha
    alternative: `load_config(None)` usa sempre `DEFAULT_CONFIG` e non legge variabili d'ambiente.
  - L'elenco "da non esporre mai" della revisione precedente è coperto per intero, più
    `--trusted-paths` e `--no-consent-gate`. Mancano solo quelli di BASSA-1 e BASSA-2.
- **Limiti documenti, ordine dei controlli**: `max_bytes` sullo `stat()` prima di aprire; pagine
  prima dell'estrazione; dimensione scompattata del DOCX prima di `docx.Document`; testo semplice
  letto solo dopo `max_bytes`; `max_chars` controllato pagina per pagina nei PDF. Lo zip non si
  aggira (vedi MEDIA-2). Valori non validi in `documents:` → `ConfigError`.
- **Estrazione fuori dal lock**: `apply_changes` estrae (e calcola lo sha256 in
  `document_record`) prima di `locked_update`. Dentro il lock si scrivono soltanto
  `episodio.json` e `documento_estratto.txt`, con scrittura atomica. `run_init.py` estrae una sola
  volta e passa il `Document`.
- **Sessione**: `document_text` è troncato a `script.max_document_chars`. Nessun codice lo rilegge
  per costruire prompt o hash (verificato con grep), quindi il troncamento non cambia
  comportamenti.
- **BASSA-1 precedente**: senza `--version` né `--current` → `VersionRequired` prima di toccare
  la sessione. Il confronto di versione e quello dello sha256 su disco restano sotto
  `session_lock`.
- **BASSA-2 precedente**: `run` aggiunto alle risposte; `pid` filtrato in `current_work` (anche
  in `RunBusy.details`); `LockTimeout.details.file` contiene solo il nome; messaggi di
  `script_session`, `episode_setup` e dei tool con soli nomi.
- **BASSA-3 precedente (`make_run_dir`)**: `output_root.mkdir(parents=True, exist_ok=True)` crea
  la cartella di output se non esiste, e se al suo posto c'è un file solleva `FileExistsError`
  (nessun ciclo infinito). Il `mkdir(parents=False)` atomico nel ciclo è corretto: con un file o
  un link rotto con lo stesso nome si passa al suffisso successivo, e `slugify` tronca a 60
  caratteri (nessun `ENAMETOOLONG`). Il risultato resta una sottocartella diretta di `output/`,
  coerente con `confine_run_dir`.
- **Sottoprocessi**: nessun cambiamento nel diff.
- **Prompt injection / gate**: nessun nuovo percorso verso il TTS. `_approval.py` non è cambiato;
  `synthesize.py`, `voices_prepare.py`, `retake.py` e `mix.py` cambiano solo per `parse_args` e il
  campo `run`.
- **Garanzie di dominio**: gate sha256 intatto; nessun retry sulla clonazione (codice di
  `voices_prepare.py` invariato); voci mai indovinate (`resolve_voice` cambia solo nel messaggio);
  scritture atomiche invariate (`write_text_atomic`, `write_json_atomic`).
- **Segreti**: nessuna nuova via. `_redact` agisce solo sui percorsi, i messaggi di pypdf e
  python-docx riportati con `{exc}` contengono solo il nome del file, e nei JSON non compaiono
  chiavi API.

## Da verificare nelle prossime fasi

- **C2**: sintesi e stima per `--run-dir` al posto di `--script`/`--voices`, con i nomi dei file
  fissati dal tool (vedi BASSA-2); aggiungere a `web_forbidden` ogni nuovo flag di bypass.
- **C2, retake**: whitelist dei tag di resa da configurazione, invariata in questo diff.
- **C4, caricamento documenti**: calcolare lo sha256 sugli **stessi byte** che si estraggono. Oggi
  `document_record` rilegge il file dopo l'estrazione, quindi un file sostituito nel frattempo
  avrebbe sha256 e testo che non corrispondono. Applicare i limiti di `documents:` anche al
  caricamento, prima di scrivere in `documenti/`.
- **C5**: il contratto deve elencare i timeout dei sottoprocessi (MEDIA-1, MEDIA-2), la forma
  `--opzione=valore` per i testi liberi (BASSA-3) e il divieto di `--current` (BASSA-1).
- `tools/lessons.py --accept --text` scrive testo libero in `prompts/lezioni.md`, che entra in
  ogni prompt futuro: se l'adattatore lo esporrà, va trattato come argomento riservato.


---

## Esito delle correzioni (aggiunto da chi ha implementato, non dal revisore)

Data: 2026-10-07. L'utente ha scelto di correggere tutto prima di C2.

- **MEDIA-1 · corretta.** `_read_pdf` controlla fra una pagina e l'altra un budget sui byte
  dei content stream decodificati (`documents.max_content_bytes`, 50 MB; uno stream condiviso
  conta a ogni pagina) e una scadenza (`documents.max_seconds`, 60). Limiti `contenuto_pagine`
  e `secondi`. Il timeout del sottoprocesso è nel contratto, a carico dell'adattatore.
- **MEDIA-2 · corretta.** `_check_docx_unpacked` controlla anche le parti XML
  (`max_xml_bytes`, 15 MB, limite `byte_xml`) e il numero di parti (`max_zip_entries`, 2000,
  limite `parti_zip`). Nota: il DOCX vuoto di python-docx ha già ~800 KB di XML (stili).
- **BASSA-1 · corretta.** `script_approve.py` vieta `--current` in modalità web.
- **BASSA-2 · corretta.** `--out`, `--voices`, `--audio-paths` aggiunti a
  `WEB_FORBIDDEN_ARGS`: in modalità web `voices_prepare.py --out` e `run_finalize.py` non si
  usano; il loro lavoro passa a `produce.py` (C2), che fissa da sé i nomi dei file.
- **BASSA-3 · corretta.** `parse_args` sostituisce `parser.error`: exit 1
  `InvalidArguments` con JSON. Nota: con Python 3.14 un valore con spazi come `"- Sì, certo"`
  è già accettato; il caso che fallisce è un valore senza spazi come `"-Davvero?"`. Nel
  contratto: valori liberi sempre come `--opzione=valore`.
- **BASSA-4 · corretta.** `_redact` riconosce la forma `repr` con barre raddoppiate, accorcia
  al loro nome le cartelle configurate (registrate da `bootstrap()`), usa un confine dopo il
  percorso (`/Users/mario2` non diventa `~2`) e si applica a tutto stderr, perché passa da
  `note()`.
- **Test**: `test_web_integration.py` da 283 a 303 controlli; le altre suite invariate e verdi.
