# Revisione di sicurezza · C6

Intervallo esaminato: `9212351..be9b674` (nessuna modifica non committata)
File esaminati: 23. Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 0 |
| Bassa | 3 |

Le correzioni di C6 fanno quello che dichiarano. Ogni invocazione di ffmpeg e ffprobe su un
file in ingresso passa da `input_args()`/`probe_args()`, e la mappa estensione → demuxer apre
correttamente tutti i formati legittimi di `audio.accepted_extensions` (verificato con
l'ffmpeg 7.1 del progetto). `--propose` classifica gli errori dell'API senza esporre chiavi o
header. Lo sha256 del documento e il testo vengono dagli stessi byte, letti una sola volta e
con il limite di dimensione. La verifica in modalità web gira davvero con
`PODCAST_AGENT_WEB=1` su una copia isolata. Restano tre punti di robustezza.

## Problemi

### [BASSA-1] Formato forzato: un file con l'estensione sbagliata entra in libreria e fallisce solo al mix
- **Dove**: `src/ffmpeg_tool.py:46-80` (`_DEMUXERS`, `input_args`, `probe_args`). L'errore
  emerge in `src/music_mixer.py:608` (`_decide_channels`) e `:647` (`_convert`). La durata è
  `None` in `media_duration_seconds` (`src/ffmpeg_tool.py:147-178`).
- **Voce della checklist**: 3. File non fidati (robustezza)
- **Scenario**: un brano caricato come `sigla.mp3` che in realtà è un MP4/AAC (succede con
  alcuni convertitori online), oppure un `.wav` che contiene MP3. Prima di C6 ffmpeg
  riconosceva il contenuto e lo leggeva. Adesso `-f mp3` / `-f wav` lo rifiuta. Verificato:
  `m4a_as.mp3` dà rc=183 "Failed to find two consecutive MPEG audio frames"; `mp3_as.wav` dà
  rc=183 "invalid start code"; `wav_as.mp3` e `m4a_as.aac` danno rc=69. Il brano compare in
  `music_list` con durata sconosciuta e si può scegliere con `music_set`. La run si blocca
  solo al mix, con un `MusicMixerError` che riporta lo stderr di ffmpeg, poco comprensibile
  per l'utente. I file legittimi non hanno problemi: wav (anche RF64 e float), mp3 con ID3,
  m4a, mp4, aac ADTS, flac, ogg (vorbis, opus, flac), opus, webm (anche con vorbis) si aprono
  tutti con il formato forzato. La sicurezza non è toccata.
- **Proposta di correzione**: controllare il contenuto al caricamento e alla scansione, non
  al mix. In `MusicLibrary.scan()` (e nell'adattatore dopo il caricamento), quando
  `media_duration_seconds()` restituisce `None` per un file con estensione nota, impostare
  `track.metadata_error = "il contenuto non corrisponde all'estensione .mp3 (rinomina il
  file con l'estensione giusta o convertilo)"`. Così `get_usable()` lo rifiuta subito e
  `music_list` lo mostra come non utilizzabile. Fare lo stesso per le voci in
  `VoiceLibrary`, dove serve la durata del campione. Aggiungere un test con un file
  `.mp3` che contiene un WAV.

### [BASSA-2] `--propose` con `lessons.api_key_env`: niente `client_factory` e un messaggio che nomina la variabile sbagliata
- **Dove**: `tools/lessons.py:121-132`, `src/script_writer.py:91`
- **Voce della checklist**: 6. Segreti / 8. Garanzie di dominio (spesa, difesa in profondità)
- **Scenario**: (a) Un `config.yaml` di prova con `script.client_factory:
  tests.fake_anthropic:...` e `lessons.api_key_env: ANTHROPIC_API_KEY`, lanciato in una
  shell dove la chiave vera è impostata: `--propose` costruisce `anthropic.Anthropic` con la
  chiave vera e scavalca il client finto, quindi parte una chiamata a pagamento in un
  ambiente che sembra di test. Oggi non succede, perché il `config.yaml` del progetto ha
  `lessons.api_key_env: null` e i test tolgono le chiavi dall'ambiente (`clean_env`). (b) Con
  `lessons.api_key_env: LESSONS_KEY` e quella chiave rifiutata, l'errore `ModelAuthError`
  dice "Controlla ANTHROPIC_API_KEY": porta a cercare il problema nella chiave sbagliata. In
  nessuno dei due casi chiavi o header finiscono nei messaggi: `classify_api_error` non usa
  `str(exc)` per auth, credito, rate limit e rete, e `raise ... from None` taglia la catena.
- **Proposta di correzione**: in `_do_propose`, se `config.script.get("client_factory")` è
  impostato usare sempre `config.anthropic_client()`, anche quando c'è
  `lessons.api_key_env`; in alternativa dare un errore di configurazione se ci sono tutti e
  due. In `classify_api_error` aggiungere un parametro facoltativo `key_env` (default
  `"ANTHROPIC_API_KEY"`) da mettere nel messaggio, e passarlo da `call_model` con il nome
  della variabile usata davvero.

### [BASSA-3] `doc_extract.py`: `size_bytes` letto da un secondo `stat()`, non dagli stessi byte; codice morto in `extract_text`
- **Dove**: `tools/doc_extract.py:69`, `src/pdf_reader.py:256-264`
- **Voce della checklist**: 7. TOCTOU (coerenza dei dati)
- **Scenario**: un documento sostituito (con conferma, come il contratto permette) fra la
  lettura e la risposta. `sha256`, `chars` e `pages` descrivono il contenuto vecchio,
  `size_bytes` quello nuovo. L'interfaccia mostra una dimensione che non corrisponde allo
  sha256. L'effetto è solo informativo. In `extract_text`, `document.sha256 = digest` sta
  prima di `if document is not None`, e i rami ora restituiscono sempre un `Document`: il
  controllo e il `raise UnsupportedDocument` che segue non si raggiungono più, e il caso
  "estensione non supportata" è già gestito a `:236`.
- **Proposta di correzione**: aggiungere a `Document` un campo `size_bytes` (= `len(data)`),
  impostarlo insieme a `sha256` in `extract_text` e usarlo in `doc_extract.py` al posto di
  `stat().st_size`. Togliere da `extract_text` il ramo `if document is not None` e il
  `raise` finale ormai irraggiungibili.

## Verificato e a posto

- **1. Path traversal**: C6 non aggiunge input che diventano percorsi. `run.py._music_context`
  ora passa da `MusicLibrary.get_usable()`, che confronta solo i nomi già trovati dalla
  scansione. `web_project_copy()` scrive solo in una cartella `mkdtemp`. Dopo l'intera suite,
  `git status` è pulito e in `output/`, `prompts/`, `voci_disponibili/`, `musiche/`,
  `documenti/` e `voices_registry.json` del progetto vero non c'è niente di modificato.
- **2. Sottoprocessi**: ho cercato tutte le chiamate (`subprocess.`, `_run_ffmpeg(`, `-i`,
  `concat`, `amovie`, `movie=`). Quelle che aprono un file sono 13: `media_duration_seconds`
  (ffprobe e ffmpeg), `music_mixer` (9: `_decide_channels`, `_convert`, `_loudnorm` ×2,
  `_match_level`, `_measure_integrated_loudness`, `_apply_ducking` con due ingressi,
  `_encode_mp3`), `audio_assembler` (codifica mp3) e `waveform._decode`. Passano tutte da
  `input_args`/`probe_args`. Non esistono invocazioni senza `-i` su file in ingresso, a parte
  ffprobe, che usa `probe_args`. I filtri (`loudnorm`, `volume`, `alimiter`,
  `sidechaincompress`) sono costruiti solo da numeri di configurazione e non aprono file:
  nessun `amovie`/`movie`. La concatenazione dei segmenti e dei pezzi del mix è in Python
  (`_concat_wav`, `_concat_wavs`), non con il demuxer concat. `tools/doctor.py` lancia
  solo `-version`. Liste di argomenti, nessuno `shell=True`, timeout presenti (900 s, 30 s
  per la durata). Mappa demuxer: tutte e 7 le estensioni di `audio.accepted_extensions`
  (`.wav .mp3 .m4a .flac .ogg .opus .webm`) sono nella mappa, e con l'ffmpeg 7.1 del
  progetto i file legittimi si aprono tutti (vedi BASSA-1). Con un'estensione aggiunta in
  configurazione e non nella mappa resta solo la whitelist. Ho provato: una playlist HLS con
  estensione non standard viene rifiutata da ffmpeg stesso ("Not detecting m3u8/hls with
  non standard extension"); un `ffconcat` viene rifiutato con percorsi assoluti o `../`
  ("Unsafe file name") e può leggere solo file nella stessa cartella o sotto.
  `-protocol_whitelist file` vale anche per gli open annidati.
- **3. File non fidati**: in `extract_text` il limite di dimensione vale sulla lettura
  unica (`read(max_bytes + 1)`, poi il confronto con `len(data)`), quindi anche se il file
  cresce dopo lo `stat()`. In memoria resta al massimo `max_bytes` (20 MB di default). Prima
  non andava meglio: pypdf con un percorso carica comunque tutto il file, e `io.BytesIO(data)`
  non copia il buffer finché non lo si modifica. Su DOCX il controllo delle dimensioni
  scompattate (`_check_docx_unpacked`) si fa sugli stessi byte, prima di `docx.Document`. Su
  PDF il limite di pagine e di stream non è cambiato. Testo: CRLF e CR diventano LF come
  faceva `read_text()` con i newline universali; l'ordine delle codifiche
  (utf-8, utf-8-sig, latin-1) è lo stesso. Il BOM resta nel testo come prima: è un
  comportamento che c'era già e non incide sulla sicurezza. Il test C6 conferma che sha256
  e testo vengono dallo stesso contenuto anche se il file viene sostituito durante
  l'estrazione, e `document_record` usa `Document.sha256`.
- **4. Prompt injection**: niente di nuovo. Il contesto delle musiche in `run.py` è lo stesso
  di prima (trascrizioni di intro/outro con `parlato: true`). Il prompt di `--propose` non
  cambia, e le proposte restano `proposta`, cioè non attive, finché non c'è `--accept --by`.
- **5. Tag dei retake**: non toccati da C6.
- **6. Segreti**: `call_model` traduce `anthropic.APIError`, compreso `APIConnectionError`,
  con `classify_api_error(...) from None`. Per auth, credito, rate limit, sovraccarico e rete
  i messaggi sono fissi. Il caso generico usa `exc.message` (il testo del server, senza
  header), come per il copione. Per la chiave mancante il `ConfigError` riporta solo il
  nome della variabile. Il test C6 verifica che "sk-" non compaia nel messaggio.
  `client_factory` resta in `config.yaml`, che in modalità web non si può cambiare
  (`--config` rifiutato).
- **7. Lock e temporanei**: C6 non aggiunge lock. `.gitignore` copre i file di stato locali:
  il marcatore `.clonazione-<hex>.json` (`src/voice_prep.py:258,268`) è coperto da
  `.clonazione-*.json`, il suo lock da `*.lock`, i temporanei atomici da `.*.tmp`, le cache
  `.cache_file.json` e `.cache_testo/` stanno in cartelle già ignorate (`documenti/*`,
  `voci_disponibili/*`, `output/`).
- **8. Garanzie di dominio**: gate di approvazione, consenso, nessun retry sulla clonazione,
  sha256 delle musiche e cache dei segmenti non sono toccati. Il flusso web completo
  (consenso, approvazione legata allo sha256, produzione interrotta e ripresa, retake) passa
  con `PODCAST_AGENT_WEB=1`, senza `--config` e con il controllo iniziale che `--config` sia
  rifiutato (`NotAllowedInWebMode`).
- **`run.py`**: l'unica modifica è `_music_context`. Stesso contesto e stesso avviso.
  L'unica differenza: un brano con `metadata_error` viene saltato (`get_usable`), ma prima
  aveva metadati vuoti e quindi `parlato` falso, per cui il risultato non cambia. Gli import
  e gli altri passi sono invariati.
- **Pulizia**: `git diff --name-status` mostra cancellati solo i tre `.DS_Store`, che restano
  sul disco. `.venv-vecchia-copia/` non era tracciata. Nessun altro file del progetto è stato
  rimosso.
- **Test eseguiti**: `test_web_integration.py` 661 controlli superati (363 s),
  `test_lessons.py` 80, `test_smoke.py` 68.

## Da verificare nelle prossime fasi

- **Adattatore web**: prima di accettare un brano o un campione di voce, verificare che il
  contenuto corrisponda all'estensione (vedi BASSA-1). Non aggiungere a
  `audio.accepted_extensions` estensioni assenti da `_DEMUXERS` senza aggiornare la mappa.
- **Revisione finale del branch**: controllare che nessuna nuova invocazione di ffmpeg usi
  `-i` direttamente; un test con `grep` su `"-i"` fuori da `ffmpeg_tool.py` lo
  garantirebbe.


---

## Esito delle correzioni (aggiunto da chi ha implementato, non dal revisore)

Data: 2026-10-07. Nessuna alta né media. L'utente ha deciso di correggere le tre basse.

- **BASSA-1**: `MusicTrack.readability_error()`: con ffmpeg disponibile, un brano non-wav
  la cui durata non si legge con il formato della sua estensione è "non utilizzabile" con
  il motivo, in `music_list.py` (`usable: false`, `error`), in `music_set.py` e in
  `get_usable` (quindi in `preflight`, prima di qualunque spesa). Per le voci,
  `voices_list.py` le segna `errore: "Illeggibile"` con `motivo`.
- **BASSA-2**: con `script.client_factory` impostato `lessons.py --propose` usa sempre
  quel client, anche se `lessons.api_key_env` è impostato. Il messaggio di
  `ModelAuthError` non nomina più una variabile fissa: rimanda alla chiave nel `.env`
  indicata in `config.yaml`.
- **BASSA-3**: `Document.size_bytes` dagli stessi byte di testo e sha256, usato da
  `doc_extract.py`; tolto il codice non più raggiungibile in `extract_text`.
- Test: web_integration da 661 a 666; `[sicurezza C6]` (5 controlli: brano AAC chiamato .mp3 in elenco e in
  `music_set`, voce illeggibile, messaggio di `ModelAuthError`, `size_bytes`).

