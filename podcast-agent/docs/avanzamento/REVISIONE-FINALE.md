# Revisione finale del branch `integrazione-web` (33068cc..HEAD)

Data: 2026-10-07. Revisore indipendente, non coinvolto nelle fasi C0–C6.
HEAD rivista: `b06c70c` + `39c1178` (il controllo automatico su ffmpeg, aggiunto in
questa revisione, punto 6).

## Come è stata fatta

| Punto | Metodo |
|---|---|
| 1 Completezza | ogni `.dc.html` di `docs/wireframe/wireframe/` confrontato con `docs/CONTRATTO_INTERFACCIA.md`; esistenza di comandi e flag verificata sull'argparse |
| 2 Contratto ↔ codice | 68 sottoprocessi con `PODCAST_AGENT_WEB=1`, senza `--config` né chiavi, su una copia isolata costruita con `web_project_copy()` di `test_web_integration.py` (provider e client finti); ~25 casi d'errore; test separato di SIGTERM durante `produce` |
| 3 Garanzie | 21 mutazioni minime, ognuna su una copia pulita, con le 5 suite lanciate per ciascuna |
| 4 Qualità dei test | lettura dei test più le mutazioni del punto 3 |
| 5 Installazione | `git clone` in una cartella temporanea, venv nuova (Python 3.14.3) dal solo `requirements.txt`, `doctor.py`, 5 suite, cartella poi cancellata |
| 6 Sicurezza | subagent `security-reviewer` sull'intero intervallo, mirato alle combinazioni fra fasi; nuovo test statico su ffmpeg |
| 7 Coerenza | `error_type`, codice morto, duplicazioni, documenti a confronto |
| 8 Decisioni | lettura di `STATO.md` §3 alla luce dei punti precedenti |

Nessun file del repository è stato modificato, salvo questo rapporto e il test del punto 6.

## Esito in breve

- **Suite** da clone pulito: smoke 68, tools_cli 183 (+2 saltati), music 73, lessons 80,
  web_integration 666 (668 con il nuovo controllo, verificato su una copia che lo
  include). `doctor.py` dà exit 0 con 20 controlli ok. Nulla viene preso dalla copia di
  lavoro.
- **Contratto**: coincide con il codice in quasi tutto. 7 discrepanze, nessuna che
  indebolisca una garanzia.
- **Garanzie**: tutte e sei implementate e non indebolite. 18 mutazioni su 21 fanno
  fallire almeno un test; 3 punti non sono coperti (vedi N8–N10).
- **Sicurezza sull'intervallo**: 0 alte, 2 medie, 5 basse, tutte nate dalla combinazione
  di fasi diverse.
- **Problemi bloccanti per il merge: 3** (B1–B3). Ognuno si corregge in poco.

---

## Problemi bloccanti per il merge

### B1 · `run.py` scrive nel registro condiviso senza le protezioni di `voice_prep` (sicurezza MEDIA-2)

- **Dove**: `run.py:345-371` (`step_prepare_voices`), in particolare `run.py:363`
  (`provider.clone_voice`) e `run.py:369` (`registry.record(...)` senza `audio_hash`).
- **Problema**: da C2 `src/voice_prep.py` protegge la clonazione con il lock per voce
  (`:363`), il marcatore di esito incerto (`pending_clone` / `CloneOutcomeUnknown`,
  `:393-395`) e lo sha256 dei byte effettivamente clonati (`:386`, `:419`, ALTA-1 di
  C2). `run.py` non ha nessuna delle tre, e usa lo stesso `voices_registry.json`
  dell'adattatore. Conseguenze:
  - dopo un esito incerto lasciato dal web, `run.py` riclona. È esattamente il retry che
    il marcatore doveva impedire: la garanzia "nessun retry sulla clonazione" non regge
    fra i due percorsi;
  - clonazioni in parallelo con il web creano voci duplicate a pagamento;
  - il `voice_id` si lega allo sha256 del file riletto dopo, non ai byte inviati: è il
    difetto corretto in C2, che si ripresenta da qui;
  - una voce con consenso **revocato** si clona e si riusa (vedi anche B-decisione in §8).
- **Perché bloccante**: CLAUDE.md, "mai indebolire le garanzie esistenti" e
  "Concorrenza". Il difetto nasce dalla combinazione di C2 (marcatore e lock) con D5
  (`run.py` invariato) e non è coperto da nessun test.
- **Proposta**: far chiamare a `step_prepare_voices`
  `src.voice_prep.prepare_voices(..., consent_gate=False)` e tradurre `VoicePrepError` /
  `CloneOutcomeUnknown` in `fail(...)`. D5 resta rispettata (nessun blocco sul consenso
  mancante), ma valgono lock, marcatore, snapshot e hash. Da decidere con l'utente se
  `revocato` debba bloccare anche in `run.py` (raccomandato: sì). Test: una run
  `run.py` non interattiva, oppure una chiamata diretta a `step_prepare_voices`, con un
  marcatore pendente, deve fermarsi senza chiamare `clone_voice`.

### B2 · Nessun comando dice quali brani sono scelti per una run

- **Dove**: wireframe `03-Musiche.dc.html:60,81,103` (select già valorizzate),
  `05-Conferma.dc.html:86` (Riepilogo "Musiche"), `07-Episodio.dc.html:29` ("Cambia
  musiche"). `tools/music_set.py` non ha una modalità di sola lettura, `music_list.py`
  non ha `--run-dir`, `episode_config.py` restituisce solo le durate.
- **Problema**: l'adattatore non può mostrare la scelta corrente, né distinguere "nessuna
  musica" da "non ancora scelta", senza leggere `musiche.json` a mano. È una funzione
  del wireframe che non è eseguibile con i tool: va contro l'obiettivo stesso del branch
  (CLAUDE.md, "ogni funzione del wireframe deve essere eseguibile con i comandi in
  `tools/`"). Il contratto non la elenca né fra le azioni a carico dell'adattatore né
  fra quelle non supportate.
- **Proposta**: aggiungere a `episode_config.py` (sola lettura) o a `run_status.py` un
  campo `musiche: {intro, sottofondo, outro}` con `{nome, sha256} | null | "non_scelta"`,
  derivato da `musiche.json`. Poi documentarlo nel contratto, voci 03, 05 e 07, e
  aggiungere un test.

### B3 · Un test può chiamare davvero l'API Anthropic

- **Dove**: `tests/test_tools_cli.py:323-347` (sezione [10]).
- **Problema**: se `ANTHROPIC_API_KEY` è nell'ambiente (basta averla esportata nella
  shell), la sezione lancia `script_write`/`script_revise` contro l'API reale e spende.
  CLAUDE.md: "mai chiamate reali a Fish Audio o Anthropic". Il difetto è precedente al
  branch (c'è già in `33068cc`), ma il branch ha reso la regola esplicita senza
  applicarla qui. In più, in `test_web_integration.py` `clean_env()` toglie le chiavi,
  mentre `test_tools_cli.py` no.
- **Proposta**: sostituire la sezione con `tests/fake_anthropic.py`, come già fa
  `test_fake_anthropic`. In alternativa, renderla attivabile solo con un flag esplicito
  (`PODCAST_E2E_REALE=1`) e saltarla di default, anche con la chiave presente.

---

## Problemi non bloccanti

### Sicurezza (dal `security-reviewer` sull'intero intervallo)

**N1 · Riuso di una voce clonata senza consenso registrato; revoca persa con il file (MEDIA-1)**
- **Dove**: `src/voice_prep.py:84-89` (`blocked_by_consent` blocca il riuso solo con lo
  stato `revocato`), `src/voice_prep.py:244-253` (`check_not_revoked`),
  `tools/retake.py:117,197`, `tools/mix.py:63`, `src/production.py:796-800`.
- **Scenari**:
  - le voci già nel registro e senza `.json` di consenso si usano subito dal web, e la UI
    non mostra mai la casella del consenso;
  - un clone fatto da `run.py` o con `--no-consent-gate` su un file sostituito (stato
    `superato`) viene poi riusato dal web;
  - se si cancella l'audio insieme al suo `.json` e si ricarica una voce con lo stesso
    nome, `retake` e `mix` delle run vecchie usano di nuovo il `voice_id` revocato
    (`voci.json` della run).
- **Nota**: il riuso senza nuovo consenso è una scelta del piano (`STATO.md`, C2) ed è
  documentato in `README.md:173-174`. La procedura di revoca del README
  (`README.md:23-25`) chiede di cancellare anche la voce sul provider e la riga del
  registro, e questo chiude il terzo scenario. Resta però affidata alla persona.
- **Proposta**: salvare nel registro, alla clonazione, lo sha del consenso valido per
  quell'`audio_hash`; bloccare il riuso se manca un consenso per l'`audio_hash` del
  registro; tenere le revoche nel registro (o in un elenco di `voice_id` revocati);
  controllare in `check_not_revoked` anche i `voice_id` di `voci.json`. Per le voci già
  nel registro: una migrazione esplicita, oppure il rischio accettato e scritto nel
  contratto. **Da decidere con l'utente.**

**N2 · Una revoca dell'approvazione a produzione in corso non la ferma (BASSA-1)**
- **Dove**: `src/production.py:733,796-800`; le revoche avvengono in
  `src/episode_setup.py:99-112`, `src/script_editing.py:146` e `script_approve --revoke`.
- **Proposta**: rileggere `_script_session.json` fra una battuta e l'altra, come già si
  fa per la revoca del consenso, e fermarsi con `ApprovalRequired` se lo sha approvato
  è cambiato.

**N3 · Avvisi senza `redact()` negli eventi e nei `warnings` (BASSA-2)**
- **Dove**: `src/production.py:745,838,853`, `tools/mix.py:114`. L'origine è
  `src/audio_assembler.py:735-738`, dove lo stderr di ffmpeg contiene percorsi assoluti.
- **Proposta**: applicare `redact()` in `EventLog.emit` e ai `warnings` di `produce` e
  `mix`, come fa già `retake`.

**N4 · Le librerie Voci e Musiche seguono i link simbolici (BASSA-3)**
- **Dove**: `src/voice_library.py:86-94`, `src/music_library.py:165-182`.
- **Problema**: un link nella libreria Voci verrebbe inviato a Fish Audio come campione.
  Oggi lo impedisce solo il contratto dell'adattatore.
- **Proposta**: scartare i link (`is_symlink()`) o confinare il percorso risolto, come
  fa `document_library`.

**N5 · `music_set.py` aggiorna `musiche.json` senza lock (BASSA-4)**
- **Dove**: `tools/music_set.py:65-107`.
- **Problema**: due scelte contemporanee (intro e outro da due schede) si sovrascrivono.
  È contro la regola di concorrenza di CLAUDE.md, anche se il file è di una sola run.
- **Proposta**: lettura e scrittura sotto `file_lock`, con rilettura dentro il lock, e
  la logica spostata in `src/`.

**N6 · `script_write.py` in modalità web accetta il cast da riga di comando (BASSA-5)**
- **Dove**: `tools/script_write.py:98-127,145` (`--speakers`, `--participant`,
  `--presenter`, `--interviewer`, `--format`, `--target-minutes`).
- **Problema**: si aggirano i controlli del passo 2 e la D4 (formati ammessi dal web).
  Il gate sullo sha256 regge.
- **Proposta**: aggiungere questi flag a `web_forbidden`.

**N7 · Da tenere d'occhio** (dal rapporto di sicurezza):
- il documento archiviato nella run senza confronto dello sha256;
- l'istruzione di `script_revise`, salvata in `_lavoro.json`, è visibile in
  `run_status`/`runs_list` a chiunque veda l'Archivio;
- i file scritti in modo atomico hanno permessi 0600: se l'adattatore gira con un altro
  utente di sistema non potrà servirli. Va scritto nel contratto.

**Controllo su ffmpeg (punto 6)**: oggi nessuna chiamata a ffmpeg o ffprobe con `-i`
fuori da `src/ffmpeg_tool.py`. Aggiunto `test_ffmpeg_input_confined` in
`tests/test_web_integration.py`, commit `39c1178`. Il test cerca costanti `"-i"`
(anche dentro le f-string) e righe di comando `ffmpeg … -i` in `src/`, `tools/` e
`run.py`. Controlla anche di non essere vuoto: deve trovare il `-i` di
`src/ffmpeg_tool.py`. Provato contro un mutante con `["ffprobe", "-i", p]` e
`f"ffmpeg -y -i {p}"`: rilevati entrambi.

### Garanzie e qualità dei test (punti 3 e 4)

| Garanzia | Implementazione | Test che fallisce se la si rimuove |
|---|---|---|
| Gate sullo sha256 del copione | `src/script_session.py:339`; `produce`: `src/production.py:734` | `test_tools_cli.py:501`; `test_web_integration.py:1618` |
| Consenso prima di ogni clonazione | `src/voice_prep.py:338` (iniziale), `:385-392` (ricontrollo sui byte), `src/voice_meta.py:126` (legato allo sha256) | web:1539, web:1786, web:1547 |
| Revoca durante la produzione, in retake e in mix | `src/production.py:796-800`, `src/voice_prep.py:244-253` | web:1865, web:2099 |
| Nessun retry sulla clonazione | `src/voice_prep.py:418` (logica), `:394` (marcatore); HTTP: `src/providers/fish_audio_provider.py:163` (`retry=False`) | web:1684, web:1691; **HTTP non coperto (N8)** |
| Nomi mai indovinati | `src/voice_library.py:184`, `src/voice_prep.py:134-136`, `src/episode_setup.py:42` | web:454, web:1472, web:1474 (solo la suite web: N13) |
| sha256 delle musiche | `src/music_mixer.py:154`; avviso intro: `:202` | `test_music.py:435`; **avviso non coperto (N10)** |
| Cache dei segmenti | riuso `src/audio_assembler.py:418`, scrittura `:807` → `src/fileio.py:141`, mancanti `src/segment_cache.py:118-126` | `test_tools_cli.py:674`, `test_music.py:395`, web:2130; **atomicità non coperta (N9)** |

**N8 · `retry=False` della clonazione Fish non è coperto**
- **Dove**: `src/providers/fish_audio_provider.py:163`.
- **Problema**: con il retry rimesso tutte le suite passano: sarebbero voci duplicate a
  pagamento.
- **Proposta**: in `test_tools_cli` [14], sostituire `provider._session` con uno stub che
  risponde 503 e conta le richieste, poi verificare una sola richiesta.

**N9 · Atomicità dei segmenti non coperta**
- **Dove**: `tests/test_web_integration.py:1661`.
- **Problema**: il test cerca solo file `.tmp` rimasti, che con una scrittura non atomica
  non esistono.
- **Proposta**: un `monkeypatch` di `os.replace`, o un errore iniettato a metà
  scrittura, poi verificare che il segmento non esista.

**N10 · L'avviso "copione scritto per un'intro diversa" non è coperto**
- **Dove**: `src/music_mixer.py:202`, già nella baseline.
- **Proposta**: un test che sostituisce l'intro dopo `script_save` e cerca l'avviso in
  `warnings`.

**N11 · Asserzione sempre vera**
- **Dove**: `tests/test_music.py:443-447`. Il controllo termina con `or True`.
- **Proposta**: confrontare con `file_hash(piano_path)`.

**N12 · I conteggi TTS dei test C2 non contano le chiamate reali**
- **Dove**: web:1585, 1668, 1873, 2672. Usano `out["tts_calls"]`/`generated_turns`,
  calcolati dal codice stesso (`src/production.py:869`).
- **Problema**: con la mutazione "battuta in cache ripagata" questi controlli passano
  (la prende solo web:2130).
- **Proposta**: attivare `tts_log_file` in `write_c2_config` e contare i testi davvero
  inviati al fake.

**N13 · Altri controlli deboli**
- web:1741 (dry-run): manca la verifica del consenso nel JSON;
- web:1133 e 1137: manca la verifica di `error_type`;
- web:2082: verifica solo l'exit code;
- `VoiceLibrary.resolve("Mario")` → nessuna corrispondenza: controllo da aggiungere
  anche in smoke (oggi lo prende solo la suite web, da 400 s);
- `tests/fake_anthropic.py` accetta qualunque parametro: una lista dei parametri ammessi
  coglierebbe un parametro sbagliato verso l'API vera.

Nota positiva: `fake_provider` registra ogni clonazione e ogni testo su file anche fra
processi diversi; nessun `try/except` nasconde un FAIL (`check()` solleva
`SystemExit`).

### Contratto ↔ codice (punto 2)

Controlli superati in tutti i 68 casi: un solo oggetto JSON su stdout, nessun JSON né
percorso assoluto su stderr, `exit_code` del JSON uguale al codice del processo. Gli
eventi JSONL di `produce` corrispondono al contratto; la ripresa paga solo le battute
mancanti.

**N14 · `fasi` annuncia `mix` anche senza musiche**
- **Dove**: `src/production.py:740`. Contratto: riga 797.
- **Problema**: la UI resterebbe ferma su "Mix · In attesa".
- **Proposta**: emettere `iniziata` dopo `preflight()` togliendo `mix` quando non ci sono
  musiche, con un test.

**N15 · L'errore di consenso o delle musiche arriva anche negli eventi**
- **Dove**: `src/production.py:713-717,740`. Il contratto (righe 809-810) dice "solo su
  stdout".
- **Proposta**: correggere il contratto.

**N16 · `WaveformError.details` ha chiavi diverse dal contratto**
- **Dove**: `src/waveform.py:82` dà `campo, ammessi`; il contratto (riga 297) dice
  `campo, minimo, massimo`.
- **Proposta**: allineare il contratto.

**N17 · Il JSON di `mix.py` non è documentato ed è incoerente con `produce`**
- **Dove**: `tools/mix.py:106`. Contratto: riga 939.
- **Problema**: `output` e `musiche_usate_file` sono percorsi assoluti (in `produce` sono
  relativi), e c'è un `file_ui` non documentato.
- **Proposta**: renderli relativi e aggiungere un esempio al contratto.

**N18 · Il "JSON del copione comune" non è identico fra i comandi**
- **Dove**: contratto riga 506; `tools/script_write.py:58`.
- **Problema**: `script_write`/`script_edit`/`--restore` non hanno
  `run, run_dir, has_script, in_corso`, write ed edit nemmeno `versions`, e
  `script_write` aggiunge `problems`.
- **Proposta**: documentare le differenze, o uniformare.

**N19 · Il rifiuto web dipende dall'ordine dei controlli**
- **Dove**: `tools/_common.py:182`. Contratto: righe 70-72.
- **Problema**: argparse gira prima del controllo web, quindi `voices_prepare` senza
  argomenti dà `InvalidArguments`, `synthesize` non dà mai `details.comando`, e
  `produce --no-consent-gate` dà `InvalidArguments`. Il rifiuto avviene comunque, senza
  effetti.
- **Proposta**: fare il controllo web prima di argparse, o precisare il contratto.

**N20 · Due errori non nominati nel contratto**
- `voice_meta --consent` senza `--expect-sha256` dà `InvalidArguments`
  (`tools/voice_meta.py:68`; contratto riga 718).
- Un documento fuori libreria dà `DocumentError` senza `details` (contratto righe
  112-114).

### Completezza (punto 1), oltre a B2

Azioni del wireframe che non trovo da nessuna parte nel contratto (file in
`docs/wireframe/wireframe/`):

**N21 · "Ascolta il campione" e "Ascolta l'anteprima"**
- **Dove**: voce in `02-Cast.dc.html:69` e `Voci.dc.html:52`; brano in
  `03-Musiche.dc.html:71` e `Musiche.dc.html:62`.
- **Problema**: la sezione Download del contratto esclude `voci_disponibili/` e
  `musiche/`.
- **Proposta**: dire se l'adattatore può servirli (con quali confinamenti), o metterli
  fra le azioni non supportate.

**N22 · Gli "Annulla" non sono definiti**
- **Dove**: "Annulla" al passo 1 (`01-Documento.dc.html:76`) e nel messaggio "Copione
  salvato" (`Stati.dc.html:22`).
- **Proposta**: mappare il secondo su `script_versions --restore`, il primo sulle azioni
  a carico dell'adattatore.

**N23 · Link dello Stepper assenti dall'indice**
- **Dove**: `Stepper.dc.html:23`.
- **Proposta**: mapparli su `run_status.passo`.

**N24 · Manca la data "registrata il" fra i candidati di `AmbiguousName`**
- **Dove**: `Stati.dc.html:55-56`; `voices_list.py` non dà una data di caricamento.

**N25 · `lessons.py --accept` con exit 3 non dà candidati in `details`**
- **Dove**: `tools/lessons.py:166-169`.
- **Problema**: i candidati sono solo nel testo, contro quanto dice il §2 del contratto e
  `Stati.dc.html:59`.

**N26 · Motivi di "non supportato" deboli**
- La cancellazione (contratto:1272) dice "non c'è un comando", che descrive lo stato e
  non dà un motivo.
- Il contenuto dei menù "⋯" non è specificato.

**N27 · `--by` non obbligatorio in modalità web**
- **Dove**: per approva, ritira, ripristina, modifica e retake; per esempio
  `tools/script_approve.py:59`, `default=None`.
- **Problema**: l'approvazione si registra con `by: null`, mentre il wireframe
  (`04-Copione.dc.html:88`) mostra "Approvato da".
- **Proposta**: in modalità web rendere `--by` obbligatorio per le azioni che lasciano
  una firma.

### Coerenza (punto 7)

**N28 · Gli `error_type` sono incoerenti**
- `--by` mancante dà tre errori diversi: `ConsentByRequired` (`src/voice_meta.py:183`),
  `InvalidArguments` (`tools/lessons.py:229`, `tools/voice_meta.py:87`) e
  `MusicMetaError` (`src/music_meta.py:75`).
- Copione non valido: `ValueError` in `tools/script_save.py:164`, `ScriptInvalid` in
  `script_edit`.
- Stili misti: `needs_user_input` (snake_case) e `MusicChangedError` (suffisso `Error`).
- Senza riga nel §3 del contratto: `TimelineError` (il suo `code` non sta in
  `details`), `FfmpegNotFoundError`, `LessonsError`, `MusicLibraryError`,
  `MusicMixerError` e gli errori `Provider*` di rate limit, timeout e qualità.
- Testi superati: contratto:170-171 parla al futuro di un comando che esiste già;
  contratto:178-179 è incompleto.
- La definizione dell'exit 2 parla solo del provider vocale (§2 del contratto,
  `.claude/agents/podcast-agent.md:127`, `README.md:480`), ma anche `ModelAuthError` e
  `ModelCreditExhausted` escono con 2.
- Nessun `error_type` esce con exit code diversi in punti diversi.

**N29 · Codice morto**
- `build_writer` in `tools/script_write.py:43` non è chiamato da nessuno.
- Import inutilizzati: `src/music_catalog.py:15`, `src/music_library.py:18,20`,
  `src/music_meta.py:17`, `tools/_common.py:33` (`re`).
- In `tools/_common.py:259-276` c'è un ramo ridondante in `_classify`.
- `VERSION_KINDS` ed `EXIT_OK` non sono usati in produzione.
- Proposta: `pyflakes` (già in `requirements-dev.txt`) in un controllo.

**N30 · Logica in `tools/` e duplicata**
- `tools/estimate.py:104-152` (`_incremental`) rifà `missing_turns` di
  `src/production.py`.
- La formula del costo TTS è in 4 posti: `estimate.py`, `production.py`, `retake.py`,
  `cost_estimate.py`.
- `tools/script_save.py:107-168` duplica `_validate` di `src/script_authoring.py` e di
  `src/script_editing.py`.
- `tools/script_approve.py:73-133` tiene la logica di approvazione; il messaggio a riga
  108 cita `script_save.py`, che in web non si usa.
- Logica che starebbe in `src/` anche in `tools/retake.py` (`_do_generate`, `cost_eur`),
  `tools/turn_at.py:39` (duplica `src/episode_info.py`), `tools/lessons.py:38-67`.
- È contro la regola "i tool restano sottili".

**N31 · Documenti in disaccordo**
- Percorso dei wireframe: CLAUDE.md:9,20, `README.md:395`, contratto:4,350 dicono
  `docs/wireframe/`, i file sono in `docs/wireframe/wireframe/`.
- Conteggi dei test: `LEGENDA.md:1050` dice 51 e `LEGENDA.md:1065` dice 171, contro 68
  e 183 altrove; `README.md:813` dice "666 (C0-C5)", ma include C6 (e ora sono 668).
- `tests/test_lessons.py:672` stampa "N check passati." invece di "N controlli
  superati".
- Flag in due lingue per lo stesso concetto: `run_init --title/--document` contro
  `episode_config --titolo/--documento`; `--accept-incomplete-name` contro
  `--accetta-nome-incompleto`.

### Installazione (punto 5)

**N32 · Piccole lacune dell'installazione**
- `doctor.py` non dice se le chiavi (o il `.env`) ci sono: un'installazione senza chiavi
  risulta `ok: true`. Proposta: controlli `required: false` con sì/no, mai il valore.
- La versione minima di Python non è documentata (provata solo la 3.14.3).
- `httpx2`, usato in `tests/fake_anthropic.py:100`, arriva solo come dipendenza
  indiretta di `anthropic==1.5.0`.

---

## 8 · Decisioni di `STATO.md` da riconsiderare

1. **D5, `run.py` senza controllo del consenso.** Presa da sola è difendibile (uso
   locale, interattivo, documentata in `README.md:179`). Vista con il resto è il punto
   più debole del branch:
   - `run.py` condivide il registro con l'adattatore, quindi i suoi cloni senza consenso
     diventano riusabili dal web (N1) senza che la UI chieda mai il consenso;
   - ignora anche la **revoca**, che D11 vuole bloccante "anche per il riuso", e il
     README stesso dice "una voce revocata non si clona, non si riusa, non si rimonta"
     senza eccezioni per `run.py`;
   - salta lock e marcatore (B1).

   **Raccomandazione**: correggere B1 subito. Poi far bloccare in `run.py` almeno lo
   stato `revocato`, e chiedere il consenso in modo interattivo quando manca:
   `run.py` usa già `input()`, e la storia della decisione in `STATO.md` lo prevedeva
   come opzione. Così il consenso è una proprietà della voce, come vuole D11, e non del
   percorso usato.
2. **Riuso senza nuovo consenso (piano C2) insieme a D11 e alla procedura di revoca del
   README.** La revoca si fa cancellando file e righe a mano, e così si perde proprio
   l'informazione "revocato" che il codice controlla (N1). Serve una revoca che lasci
   traccia nel registro.
3. **D8, il titolo non ritira l'approvazione.** Va bene, ma con G7 (il titolo dà nome
   all'mp3) un cambio di titolo dopo il montaggio rende incoerenti il nome del file e
   `episodio.json`. Da verificare che `run_state` non lo veda come "file finale mancante".
4. **D9, copione con errori salvato come non approvabile.** Corretta. Insieme a N6
   (cast da riga di comando in `script_write` dal web) però permette di generare copioni
   per formati che D4 esclude dal web. Si chiude con N6.
5. **`VersionConflict` exit 1 e "nome ambiguo" = `AmbiguousName` exit 3.** Coerenti, ma
   `Stati.dc.html` va riletto con l'adattatore. `lessons --accept` (N25) è l'unico exit 3
   senza `details` utilizzabili.
6. **I divieti della modalità web dipendono da `PODCAST_AGENT_WEB=1`**, che deve impostare
   l'adattatore. Se se ne dimentica, tutti i flag pericolosi tornano disponibili.
   Raccomando di scriverlo nel contratto come primo requisito, con un controllo
   all'avvio (es. `doctor.py --web` che fallisce senza la variabile).

---

## Giudizio

Il lavoro è solido. Il contratto corrisponde davvero al codice (verificato su 68
invocazioni reali). Le garanzie sono intatte e quasi tutte difese da test che falliscono
se le si toglie. L'installazione da zero funziona senza nulla dalla copia di lavoro. Le
revisioni di sicurezza per fase hanno chiuso i problemi gravi: sull'intero intervallo non
ne restano di alti.

**Il branch non è ancora pronto per il merge in `main`.** Prima farei, in questo ordine:

1. **B1**: `run.py` passa da `src/voice_prep.prepare_voices(..., consent_gate=False)`
   (lock, marcatore, hash), con un test; con l'utente, decidere se `revocato` blocca
   anche lì (D5, §8.1).
2. **B2**: esporre le musiche scelte per la run, con contratto e test.
3. **B3**: togliere la chiamata reale ad Anthropic da `test_tools_cli.py` [10].
4. Decidere con l'utente **N1** (consenso e revoca nel registro) e §8.6
   (`PODCAST_AGENT_WEB` obbligatorio).
5. Tre test economici che chiudono buchi veri: N8 (`retry=False` di Fish), N11
   (`or True`), N12 (contare le chiamate TTS reali nei test C2).
6. Il resto (N2–N7, N13–N32) può andare in una fase di rifinitura, anche dopo il merge:
   sono correzioni di documentazione, coerenza e robustezza, nessuna delle quali
   indebolisce una garanzia.

I rapporti di lavoro dei singoli controlli sono rimasti nello scratchpad della sessione,
che è temporaneo. Il contenuto rilevante è tutto in questo file.
