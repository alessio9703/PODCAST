# Revisione di sicurezza · C5

Intervallo esaminato: `adbbab6..62abef2` (nessuna modifica non committata, a parte i
`.DS_Store`, ignorati)
File esaminati: 6 (`docs/CONTRATTO_INTERFACCIA.md`, `README.md`, `LEGENDA.md`,
`docs/avanzamento/C5.md`, `docs/avanzamento/STATO.md`, `tests/test_web_integration.py`),
più il codice citato dai documenti per le verifiche a campione. Data: 2026-10-07.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 2 |
| Bassa | 5 |

C5 non cambia codice: i rischi stanno in ciò che il contratto dice (o non dice)
all'adattatore. La sezione "A carico dell'adattatore" riporta quasi tutte le note dei
rapporti precedenti, e le affermazioni controllate sul codice (argomenti riservati, tool
esclusi dal web, estensioni, limiti dei documenti, timeout di ffmpeg e del lock di
clonazione, campi nascosti di `voices_list`) sono corrette. Mancano due regole che
l'adattatore non può ricavare da solo: l'identità di un file di libreria è il nome
**senza estensione** (MEDIA-1), e `documents_list.py` può durare molto più dei 30 s
consigliati (MEDIA-2). Il test end-to-end usa solo il provider e il client finti.
`test_web_integration.py`: 617 controlli superati.

## Problemi

### [MEDIA-1] Caricamenti: un file con lo stesso nome e un'altra estensione eredita i metadati e blocca le run
- **Dove**: `docs/CONTRATTO_INTERFACCIA.md:1175-1196` (regole dei caricamenti, "Nessuna
  sovrascrittura silenziosa: un file con lo stesso nome…"); codice:
  `src/music_library.py:90` (`track_path.with_suffix(".json")`), `src/voice_meta.py:59-61`
  (stessa regola per le voci), `src/voice_library.py:158-162` e `MusicLibrary.resolve`
  (due file con lo stesso nome senza estensione sono ambigui).
- **Voce della checklist**: 1 (file caricati) e 8 (garanzie di dominio: musiche, nomi mai
  indovinati)
- **Scenario**: in `musiche/intro/` c'è `Sigla.wav`, con `Sigla.json` che dichiara la
  licenza ed è usata in episodi già montati. Un utente carica `Sigla.mp3`: per il
  contratto è un nome diverso, quindi nessuna conferma. Verificato con uno script:
  - `Sigla.mp3` compare con la licenza di `Sigla.wav` (e con la sua trascrizione e il suo
    `parlato`), senza che nessuno l'abbia dichiarata;
  - `resolve("intro", "Sigla")` dà due candidati con lo stesso nome: è il caso che il
    contratto dice "da rinominare nella libreria", quindi `music_set`, `produce` e `mix`
    delle run che usano "Sigla" non hanno più un'uscita dall'interfaccia.

  Per le voci è lo stesso: `Mario Rossi.mp3` accanto a `Mario Rossi.wav` rende "Mario
  Rossi" ambiguo e blocca ogni produzione e ogni retake con quella voce. Il consenso non è
  aggirabile, perché è legato allo sha256: il danno è il disservizio e il nome pronunciato
  condiviso.
- **Proposta di correzione**: nel contratto, sezione "Caricamenti", aggiungere che il nome
  di un file di libreria è il nome **senza estensione**, confrontato come fanno i comandi
  (minuscole, senza accenti, `_` e `-` come spazi: `_normalize` in `voice_library.py` e
  `music_library.py`). Un caricamento che collide con un nome esistente è una
  sostituzione: richiede la conferma esplicita, e il file vecchio si toglie insieme
  (stessi effetti già descritti: consenso `superato`, `MusicChangedError`). In aggiunta,
  e facoltativo, i comandi possono segnalare la collisione negli elenchi (riga con
  `errore`).

### [MEDIA-2] `documents_list.py` può durare ben più del timeout consigliato: la libreria Documenti resta in timeout
- **Dove**: `docs/CONTRATTO_INTERFACCIA.md:1151` ("`documents_list.py` fino a ~25 s",
  timeout 30 s) e `:1003`; codice: `src/document_library.py:228-230` (il budget di 20 s si
  controlla **prima** di iniziare un'estrazione), `:114` (`file_lock(target,
  timeout=120)`), `config.yaml:140` (`max_seconds: 60`).
- **Voce della checklist**: 3 (file non fidati: tempo)
- **Scenario**: un utente carica un PDF entro i limiti, ma lento da estrarre (50 s). Alla
  chiamata successiva `documents_list.py` inizia subito la sua estrazione (il budget è
  ancora a 0) e dura ~50 s; l'adattatore, con il timeout di 30 s del contratto, uccide il
  processo prima che la cache sia scritta. La chiamata dopo ricomincia da capo: la
  libreria Documenti (e il passo 1, che usa lo stesso elenco) non si carica più finché il
  documento c'è. Se intanto un'altra richiesta sta estraendo lo stesso file,
  `documents_list.py` aspetta il lock fino a 120 s.
- **Proposta di correzione**: nel contratto, per `documents_list.py` senza `--name`: durata
  massima = budget (20 s) + un'estrazione intera (`documents.max_seconds`, 60 s, più una
  pagina) + l'attesa del lock (120 s), con un timeout consigliato coerente (es. 240 s) o,
  meglio, nel codice: avviare un'estrazione nuova solo se il tempo che resta nel budget
  basta (o non estrarre affatto nell'elenco e lasciare `in_attesa: true`, delegando a
  `documents_list.py --name`, che ha già il timeout di 120 s), e usare per il lock
  dell'elenco un timeout breve con riga `in_attesa`.

### [BASSA-1] Download: i file con `interno: false` comprendono JSON con percorsi assoluti del server
- **Dove**: `docs/CONTRATTO_INTERFACCIA.md:1208-1212`; `src/episode_info.py:138`
  (`interno` = nome che inizia con `_`); `src/music_mixer.py:375` (`"path":
  str(track.path)` in `musiche_usate.json`); `episodio.json` (`documento.percorso`).
- **Voce della checklist**: 6 (messaggi che rivelano più del necessario)
- **Scenario**: nella run finale del test (`output/<run>/`) i file con `interno: false`
  sono `conversazione.json`, `documento_estratto.txt`, `documento_originale.txt`,
  `episodio.json`, `il-trimestre-finale.mp3`, `musiche.json`, `musiche_usate.json`.
  Seguendo il contratto l'adattatore li serve, e `episodio.json` e `musiche_usate.json`
  contengono il percorso assoluto del documento e del brano sul server (verificato). Il
  contratto in §1 dice invece che i percorsi assoluti non si mostrano mai. Inoltre "i
  documenti della libreria (per l'anteprima)" sono fra i file da servire, ma la regola di
  risoluzione riguarda solo `output/<run>`.
- **Proposta di correzione**: nel contratto, servire solo `file_finale` e i file audio
  (`.mp3`, `.wav`) e testo (`documento_estratto.txt`) di `file[]`, oppure marcare come
  `interno: true` anche i JSON di lavoro (`episodio.json`, `musiche*.json`,
  `conversazione.json`) in `src/episode_info.py`. Per i documenti: risolverli dentro
  `documenti/` solo per un `nome` dell'elenco `documents_list.py`, rifiutando nomi con il
  punto iniziale e link simbolici.

### [BASSA-2] README e LEGENDA promettono risposte senza percorsi del server
- **Dove**: `README.md:412-415` ("non danno percorsi del server né `voice_id` nelle
  risposte"); in forma più sfumata `LEGENDA.md:1411-1413`.
- **Voce della checklist**: 6
- **Scenario**: in modalità web `run_init.py` dà ancora `run_dir`, `session_file`,
  `script_file` assoluti (`tools/run_init.py:50-53`), e così `script_show.py`
  (`run_dir`, `:44`, `:67`) e gli altri campi elencati in §1 del contratto. Chi scrive
  l'adattatore leggendo il README può inoltrare le risposte alla UI così come sono.
- **Proposta di correzione**: allineare README e LEGENDA al contratto: "i messaggi
  d'errore e stderr non contengono percorsi del server; le risposte di successo ne
  contengono ancora in campi elencati nel contratto (§1, 'Campi da non mostrare'), che
  l'adattatore non mostra".

### [BASSA-3] Nomi che iniziano con `-`: ammessi al caricamento, letti come opzioni
- **Dove**: `docs/CONTRATTO_INTERFACCIA.md:1175-1177` (regola dei nomi), `:377`
  (`doc_extract.py NOME`, argomento posizionale), `:1139`.
- **Voce della checklist**: 2 (un nome di file che inizia con `-` non deve essere
  interpretato come opzione)
- **Scenario**: la regola dei nomi vieta `/`, `\`, `..` e il punto iniziale, non il
  trattino. Un documento caricato come `--save-to=x.pdf` e passato come da contratto
  (`doc_extract.py NOME`) diventa l'opzione `--save-to`: verificato, in modalità web dà
  `InvalidArguments` (documento mancante) e in ogni caso `--save-to` sarebbe rifiutato
  (`NotAllowedInWebMode`). La forma `--opzione=valore` del contratto non si applica a un
  posizionale; con `--` davanti al nome funziona (verificato). Nessun effetto oltre
  l'errore, perché gli argomenti pericolosi sono già rifiutati: difesa in profondità.
- **Proposta di correzione**: nel contratto, aggiungere alla regola dei nomi "mai con il
  trattino iniziale", e scrivere `doc_extract.py -- NOME` (o dire che un posizionale si
  passa sempre dopo `--`).

### [BASSA-4] La nota su ffmpeg e i file caricati (playlist, `ffconcat`) non è nel contratto
- **Dove**: nota di `C0+C1-sicurezza.md`, "Da verificare", Caricamenti; `src/ffmpeg_tool.py:124-126`,
  `src/music_mixer.py:609`, `src/waveform.py:45` (ffmpeg/ffprobe con `-i <file>` e
  formato riconosciuto dal contenuto); `docs/CONTRATTO_INTERFACCIA.md:1178-1185`.
- **Voce della checklist**: 1 e 3
- **Scenario**: un "brano" `x.mp3` che in realtà è un file `ffconcat version 1.0` passa il
  controllo sull'estensione; a ogni `music_list.py` (durata) e al mix ffmpeg lo apre con
  il demuxer `concat`, che legge altri file indicati dentro (in modalità `safe`, solo nomi
  semplici relativi alla cartella). Effetto: il brano "suona" come un altro file della
  libreria; con altri demuxer (playlist) il rischio dipende dalla versione di ffmpeg. Lo
  scenario è limitato, ma la nota chiedeva di valutarlo e il contratto non ne parla.
- **Proposta di correzione**: nel contratto, "Caricamenti": controllare al caricamento la
  firma del file (RIFF/WAVE, ID3 o frame MPEG, `fLaC`, `OggS`, `ftyp`, EBML) oltre
  all'estensione. Nel codice (fase C6): passare a ffmpeg/ffprobe `-protocol_whitelist
  file` e, dove il formato è noto dall'estensione, `-f` esplicito.

### [BASSA-5] Autorizzazioni: mancano le conferme che superano un controllo
- **Dove**: `docs/CONTRATTO_INTERFACCIA.md:1214-1226` (tabella); `:321`, `:836`, `:1196`
  (`--accept-changed-music`); `:307`, `:1052` (`--accept-incomplete-name`).
- **Voce della checklist**: 8 (nessun bypass che non sia un flag esplicito e documentato)
- **Scenario**: `--accept-changed-music` fa usare in un episodio già approvato un brano
  diverso da quello controllato con lo sha256; `--accept-incomplete-name` fa pronunciare
  al presentatore un nome senza cognome negli episodi di tutti. Sono flag espliciti e
  documentati, ma la tabella delle autorizzazioni non li elenca e il contratto non dice
  che vanno passati **solo** dopo l'esito 3 corrispondente (`MusicChangedError`,
  `SpokenNameIncomplete`) e su conferma dell'utente. Un adattatore che li passa sempre,
  "per semplicità", toglie il controllo sha256 delle musiche senza che nessuno lo veda.
- **Proposta di correzione**: aggiungere le due righe alla tabella "Autorizzazioni" e, in
  "Come lanciare i comandi", la regola: "i flag di conferma (`--accept-changed-music`,
  `--accept-incomplete-name`, `--confirm-clone-retry`, `--remove-id`) si passano solo
  nella richiamata dopo l'esito 3 che li chiede, con la scelta dell'utente".

## Confronto con le note "Da verificare" dei rapporti precedenti

| Nota | Rapporto | Nel contratto C5 |
|---|---|---|
| Argomenti da non esporre (`--config`, `--save-to`, `--text-file`, `--document`, `--session`, `--script-json`, `--script`, `--no-approval-gate`) | C0+C1 | sì (§1 "Modalità web", e rifiutati dal codice) |
| Nomi caricati semplici, niente symlink, dimensione in streaming, niente sovrascrittura silenziosa | C0+C1 | sì, ma il "nome" non tiene conto dell'estensione (MEDIA-1) e non vieta il `-` iniziale (BASSA-3) |
| ffmpeg su file caricati: `-protocol_whitelist`, playlist, `ffconcat` | C0+C1 | **no** (BASSA-4) |
| Archivio da `output/`, "in corso" dal lock | C0+C1 | sì |
| `lessons.py --propose` fuori da `classify_api_error` | C0+C1, C4 | sì, come "Non supportato", rinviato a C6 |
| Timeout dei sottoprocessi, `--opzione=valore`, divieto di `--current` | C0+C1-correzioni | sì (§1 e "Processi, tempi e coda"); il timeout di `documents_list` è sbagliato (MEDIA-2) |
| `lessons.py --accept --text` come argomento riservato | C0+C1-correzioni | in parte: è ammesso, ma riservato a chi amministra (Autorizzazioni) con i limiti di C4: coerente |
| sha256 del documento sugli stessi byte estratti | C0+C1-correzioni | non corretto nel codice; `C5.md` dice che lo evita la regola "nessuna sovrascrittura silenziosa", ma il contratto ammette la sostituzione confermata e non dice "non mentre un'estrazione è in corso". Vedi "Da verificare" |
| Niente `.json` in `voci_disponibili/`, voce e `.json` cancellati insieme | C2 | sì |
| `--by` = utente autenticato; chi registra/revoca il consenso | C2 | sì |
| Niente secondo SIGTERM durante la preparazione delle voci | C2 | sì |
| `--events` con nome fisso | C2 | superata (ora è un flag) |
| Consenso per episodio | C2 | sì ("Non supportato") |
| `--confirm-clone-retry` solo dopo `CloneOutcomeUnknown`, autorizzato, timeout | C3 | sì (§3 C2, Autorizzazioni, 11 min) |
| Download risolti dentro `output/<run>`, solo file previsti | C3 | sì, ma l'elenco dei "previsti" comprende JSON con percorsi assoluti (BASSA-1) |
| "Altre indicazioni" mai a nessun tool | C3 | sì ("Non supportato") |
| Limite di dimensione delle registrazioni | C3 | sì |
| `istruzione` in `in_corso` visibile ad altri | C3 | sì |
| `.estrazioni`, `.json`/`.lock`/`.tmp` nelle musiche, limite per i brani | C4 | sì |
| Autorizzazioni: nome pronunciato, licenza, trascrizione, lezioni, run di tutti | C4 | sì (mancano le conferme di BASSA-5) |

## Verificato e a posto
- **1. Path traversal**: il contratto non suggerisce percorsi assoluti né `--trusted-paths`
  per l'adattatore; `--run-dir` "sempre con il `run` dell'elenco"; i nomi dei documenti
  sempre dalla libreria. Nessun esempio usa un argomento riservato (gli unici `--current`
  e `--trusted-paths` nei documenti sono per il subagent e la CLI, dichiarati tali).
- **2. Sottoprocessi**: "mai una shell, argomenti in lista" nel contratto; timeout di
  ffmpeg (900 s, `src/ffmpeg_tool.py:40`), di ffprobe (30 s) e del lock di clonazione
  (600 s, `src/voice_prep.py:47`) corrispondono a quanto scritto.
- **3. File non fidati**: estensioni dei documenti (`SUPPORTED_EXTENSIONS`,
  `src/pdf_reader.py:11,146`) e delle registrazioni (`config.yaml`
  `audio.accepted_extensions`) coincidono con il contratto; i limiti dei documenti
  (20 MB, 500 pagine, 2 M caratteri, 60 s) coincidono con `config.yaml`.
- **4-5. Testo nei prompt e retake**: "Altre indicazioni" non supportato; `--direction`
  solo dalla whitelist; nessun documento invita a passare testo libero a `retake.py`.
- **6. Segreti**: chiavi solo in `.env`, "mai sulla riga di comando"; `settings_show`
  descritto come whitelist; il test end-to-end toglie `ANTHROPIC_API_KEY` e
  `FISH_AUDIO_API_KEY` dall'ambiente (`clean_env`).
- **Modalità web**: l'elenco degli argomenti riservati del contratto coincide con
  `WEB_FORBIDDEN_ARGS` (`tools/_common.py:118-131`) più `--current`
  (`script_approve.py:63`) e `--document` (`script_write.py:145`); `voices_prepare.py` e
  `synthesize.py` rifiutati per intero; `run_finalize.py` e `script_save.py` di fatto
  inutilizzabili (`--voices`, `--script-json` obbligatori e riservati); `--by`
  obbligatorio per le lezioni e `--expect-sha256` per il consenso.
- **Test end-to-end** (`test_full_flow`): provider `tests.fake_provider:FakeProvider`,
  client `tests.fake_anthropic:build`, chiavi tolte dall'ambiente, nessuna rete. Suite
  verde (617 controlli).
- **7. Lock** e **8. Garanzie di dominio**: il contratto non propone bypass;
  `estimate.py` toglie `voice_id` (`src/production.py:623`).

## Da verificare nelle prossime fasi
- **`test_full_flow` non gira in modalità web**: usa `--config` (riservato), quindi la
  "verifica finale" non prova il flusso con `PODCAST_AGENT_WEB=1`. Valutare un modo di
  puntare i test a una config senza `--config` (per esempio la cartella del progetto
  copiata in una cartella temporanea) e ripetere il flusso in modalità web.
- **sha256 del documento** (nota C0+C1-correzioni, ancora aperta nel codice): o si
  corregge `document_record` (hash sugli stessi byte estratti), o il contratto dice che un
  documento non si sostituisce mentre una run che lo usa è `in_corso` o mentre è aperto
  un `run_init`/`episode_config --documento`.
- **C6**: `lessons.py --propose` con `classify_api_error` (oggi un errore di
  autenticazione esce con exit 1 e la UI potrebbe proporre "Riprova"); ffmpeg con
  `-protocol_whitelist file` (BASSA-4).
- **Imprecisioni senza effetto sulla sicurezza**:
  - `CONTRATTO_INTERFACCIA.md:76-79` dice ancora che `estimate.py` non si usa in modalità
    web e che la stima per `--run-dir` "arriva in C2"; `:174-175` parla di "produci"
    al futuro;
  - `README.md:811` e `LEGENDA.md:78` contano 593 controlli in `test_web_integration.py`,
    ora 617;
  - `:1064` scrive `--licenza "testo"` e `--by U` senza la forma `=` che il contratto
    chiede per i valori liberi.
