# Stato dell'integrazione web

Ultimo aggiornamento: 2026-10-07 (correzioni di sicurezza e modalità web). Branch `integrazione-web`.

Il metodo di lavoro e le regole sono in `CLAUDE.md`. I dettagli di ogni fase sono nei
resoconti accanto a questo file:
- `C0.md`
- `C1.md`
- `C0+C1-sicurezza.md`
- `C0+C1-correzioni.md`, `C0+C1-correzioni-sicurezza.md`
- `C2.md`, `C2-sicurezza.md`
- `C3.md`, `C3-sicurezza.md`
- `C4.md`

## 1. Commit

| Commit | Contenuto |
|---|---|
| `33068cc` | baseline: presentatore, controllo sottovoce, livelli mixer (lavoro precedente) + `docs/wireframe/` |
| `a3f4e28` | **C0** esiti strutturati, lock e scritture atomiche, client Anthropic finto |
| `0cc44fe` | **C1** copione (passi 1–4 e Confronto) |
| `5240ce2` | C1: decisioni sui dubbi aperti (DOCX, errori API, ritiro approvazione su cambio cast) |
| `649be02` | toglie due `.DS_Store` entrati per errore |
| `b2f89ce` | sicurezza C0+C1: correzioni ALTA-1 e ALTA-2, subagent `security-reviewer` |
| `d29ae0c` | `CLAUDE.md` e questo `STATO.md` |
| `a6361f1` | sicurezza C0+C1: medie e basse chiuse, modalità web, python-docx installato |
| `b3c6ed8` | correzioni della revisione del diff: limiti di lavoro e tempo, `InvalidArguments`, `_redact`, divieti web |
| `dde378c` | **C2** produzione: consenso, stima per la run, `produce.py` con eventi e interruzione |
| `4d8ede8` | sicurezza C2: ALTA-1 corretta (consenso ricontrollato sui byte clonati) |
| `9006245` | sicurezza C2: medie e basse corrette |
| `8621966` | **C3** episodio: `_montaggio.json`, `episode_info`, `waveform`, retake con indicazioni, `mix` senza musiche |
| `711c34b` | rapporto di sicurezza C3 |
| `db8e47d` | sicurezza C3: medie e basse corrette |
| (questo) | **C4** Archivio, librerie, impostazioni; lock delle lezioni; doctor senza percorsi |

Test all'ultimo commit, sempre con `.venv/bin/python`: smoke 68, tools_cli 183 (+2
saltati, servono `ANTHROPIC_API_KEY`), music 73, lessons 80, web_integration 568.

## 2. Fasi

| Fase | Contenuto | Stato |
|---|---|---|
| C0 | Trasversale: esiti strutturati, lock e scritture atomiche, client Anthropic finto | **fatta** |
| C1 | Copione: passi 1–4 e pannello Confronto | **fatta** (+ decisioni sui dubbi) |
| Sicurezza C0+C1 | Revisione, rapporto, correzione delle alte | **fatta**: alte, medie e basse corrette |
| Correzioni + modalità web | MEDIA-1, BASSA-1/2/3, `PODCAST_AGENT_WEB=1` | **fatta**; revisione del diff: 0 alte, 2 medie e 4 basse, **tutte corrette** |
| C2 | Produzione: passi 5 (stima e consenso) e 6 (generazione) | **fatta**; revisione: 1 alta, 4 medie, 5 basse, **tutte corrette** |
| C3 | Episodio e correzioni: passo 7 | **fatta**; revisione (con le correzioni di C2): 0 alte, 2 medie, 6 basse, **tutte corrette** |
| C4 | Archivio e librerie (Documenti, Voci, Musiche), Impostazioni, verifica di Lezioni e Diagnostica | **fatta**; revisione di sicurezza da fare |
| C5 | Contratto completo + README/LEGENDA + riepilogo finale | da fare (il contratto è già aggiornato per C0–C1) |
| C6 | Allineamento di `run.py` a `src/music_context.py`, fase finale dedicata | da fare |

A fine di **ogni** fase: revisione di sicurezza (`<fase>-sicurezza.md`), alte corrette
prima della fase successiva.

### Piano delle fasi da fare (dal piano approvato)

**C2 · Produzione (passi 5 e 6)**
- **Consenso**: `voice_meta.py --name X --consent --by U` / `--revoke-consent`.
  - Metadati in un JSON accanto all'audio (vedi decisione D2).
  - `voices_prepare.py` si rifiuta di CLONARE una voce senza consenso registrato: exit 3
    `ConsentRequired`, voci in `details`, bypass solo con un flag esplicito
    (`--no-consent-gate`).
  - Riusare una voce già clonata non richiede di nuovo il consenso.
  - La logica sta in `src/voice_prep.py`, condivisa da `voices_prepare.py` e `produce.py`.
    Nessun retry sulla clonazione.
- **`estimate.py`**:
  - G2: deve funzionare anche senza `voci.json`. Per le voci riusate ricava le chiavi dal
    registro; le battute delle voci da clonare contano tutte come da generare.
  - Restituisce `voices: [{speaker, ruolo, azione: reuse|clone, consenso}]`.
  - Costo di clonazione da `providers.*.options.clone_cost_usd` (null = "non noto",
    dichiarato nel JSON).
  - Valuta USD + EUR (decisione D3).
- **`src/production.py`**: prende la logica di `synthesize.py`, che diventa sottile.
- **`produce.py --run-dir [--events PATH]`**:
  - sequenza: gate → controllo musiche → voci (consenso, nessun retry) → sintesi →
    montaggio e mix → tempi e segnali → `run_finalize`;
  - tiene il lock di lavoro per tutta la durata (`src/run_lock.work_lock`), così lo stato
    "in generazione" si deriva dal lock.
- **Eventi JSONL** (`--events PATH`):
  - fase iniziata e finita;
  - battuta i/N con `cached` ed `eta_seconds`;
  - voci riusate e clonate;
  - avvisi;
  - interrotta, finita, errore.
- **Interruzione pulita**: SIGTERM e SIGINT alzano un flag controllato fra una battuta e
  l'altra; il segmento in corso si scrive in modo atomico.
  - Esito exit 1 `ProductionInterrupted` con `{done, total, missing}`.
  - Test: rilanciare paga solo le battute mancanti.
  - Test: un errore di clonazione dà exit 2 con un solo tentativo.

**C3 · Episodio (passo 7)**
- `episode_info.py --run-dir`: titolo, formato, durata, voci, data di montaggio, sezioni
  nel file finale (da `musiche_usate.json`), trascrizione con
  `inizio_finale`/`fine_finale` per battuta, file della run.
- `waveform.py --run-dir --points N`: picchi calcolati sul file finale via ffmpeg, messi in
  cache in `_forma_onda_N.json` legata allo sha256 del file.
- G1: `mix.py` rimonta dalla cache anche senza musiche, con zero chiamate TTS.
- G5: `retake --list` restituisce l'anteprima in contesto (prima, take, dopo) per ogni
  take, originale compreso, senza TTS.
- Indicazioni di resa del retake: decisione D1.
- G7: il titolo di `episodio.json` dà nome all'mp3 (solo per le run nuove).
- Percorsi completi da provare con i test:
  - correggi il testo → `script_edit` → approva → `produce` (paga solo la battuta
    cambiata);
  - cambia musiche → `music_set` + `mix`.

**C4 · Archivio e librerie**
- `runs_list.py [--status] [--search]` e `run_status.py --run-dir`.
  - Stato derivato in `src/run_state.py`, con priorità dall'alto:
    1. lock di lavoro tenuto → in corso (i/N dai segmenti in cache);
    2. file finale coerente con il copione → pronto;
    3. approvato + `voci.json` + battute mancanti → interrotta;
    4. approvato → stima e consenso;
    5. sessione → copione da approvare;
    6. `musiche.json` → bozza · copione;
    7. `episodio.json` con il cast → bozza · musiche;
    8. altrimenti → bozza · cast.
  - Regge le run vecchie senza i file nuovi.
- `documents_list.py`:
  - nome, tipo, dimensione, data, pagine/parole, estraibile sì/no;
  - run in cui è usato (sha256 di `documento_originale.*` o di `episodio.json`, con il
    nome come ripiego);
  - estrazione in cache (con lock).
- `voices_list.py` esteso: durata del campione, data di clonazione, consenso, nome
  pronunciato, usata in N (da `voci.json` delle run).
- `voice_meta.py --spoken-name`: un nome incompleto dà exit 3 `SpokenNameIncomplete`, da
  confermare in modo esplicito.
- `music_meta.py`: licenza (testo o nessuna, chi e quando), parlato, trascrizione.
  - Stesse validazioni di `music_library` (sottofondo con parlato = errore), con lock.
- `music_list.py`: "usato in N" e testo della licenza.
- `settings_show.py`: whitelist di chiavi di config, mai `api_key*` né valori
  d'ambiente.
- Verifica degli output di `lessons.py` e `doctor.py`.

**C5 · Contratto e documentazione**
- `docs/CONTRATTO_INTERFACCIA.md`: per ogni azione del wireframe comando, argomenti,
  esempio reale di JSON, exit code, se è lunga (con eventi), se costa.
- In fondo, le azioni a carico dell'adattatore (upload, download, login, coda) e quelle
  non supportate, con il motivo.
- README e LEGENDA aggiornati; riepilogo di cosa è cambiato, cosa no e perché.

**C6 · run.py**: usa `src/music_context.py` al posto della sua copia di `_music_context`,
senza cambiare il comportamento visibile.
Inoltre `tools/lessons.py --propose` passa a `classify_api_error`.

**Verifica finale**: un flusso completo da CLI su una run fittizia: documento → cast →
musiche → copione → modifica → approva → stima → consenso → produce interrotto e ripreso
→ info → forma d'onda → retake → scegli → mix → archivio.

## 3. Decisioni prese (con il motivo)

### D1 · Retake: "Come deve cambiare" (risposta ai punti aperti, 2026-10-06)
- **Decisione**: ogni indicazione ("Più calmo", "Più lento"…) aggiunge una tag **solo al
  testo inviato per quel take**. La corrispondenza indicazione → tag sta in
  `config.yaml`. Il copione approvato e il suo sha256 non cambiano. Le indicazioni restano
  nei metadati del take, il loro costo (le tag si pagano) è nella stima. "Pronuncia
  diversa" non ha una tag e rimanda a "Correggi il testo", con nuova approvazione.
- **Motivo**: nel wireframe è "stesso testo, nuova resa". Cambiare le tag nel copione
  cambierebbe il testo e richiederebbe una nuova approvazione per ogni tentativo.
- **Condizione di sicurezza** (checklist del revisore, punto 5): le indicazioni devono
  essere una **whitelist** da configurazione. Nessun testo libero arriva al TTS fuori dal
  copione approvato. Da verificare nella revisione di C3.
- **Confermato (2026-10-07)**: solo la whitelist, nessun'altra condizione.
- **Attuata in C3**: `retake.indicazioni` in `config.yaml`; il campo libero "Altre
  indicazioni" del wireframe non è supportato.

### D2 · Metadati delle voci accanto all'audio (2026-10-06)
- **Decisione**: `voci_disponibili/<nome>.json` accanto alla registrazione, con nome
  pronunciato e consenso (chi e quando), scritto in modo atomico e sotto lock.
- **Motivo**: stessa forma dei `.json` delle musiche. Il file si sposta e si cancella con
  l'audio, quindi revocare il consenso e cancellare la registrazione avvengono nello
  stesso posto. Niente contesa su un registro unico condiviso fra tutti.

### D3 · Valuta: USD + EUR (2026-10-06)
- **Decisione**: `cost_usd` c'è sempre. `cost_eur` c'è solo se in `config.yaml` c'è un
  cambio con la data del tasso, altrimenti è null e la UI mostra i dollari.
- **Motivo**: nessun tasso inventato; il costo vero del provider è in USD.

### D4 · Formati (2026-10-06)
- **Decisione**: il web supporta solo `conversazione` e `intervista`, sempre con
  presentatore + 2 voci. Monologo e tavola rotonda restano in `run.py` e in
  `script_write.py` senza `episodio.json`.
- **Motivo**: allineamento al wireframe senza togliere funzioni a chi le usa oggi.

### D11 · Consenso per voce (2026-10-07)
- **Decisione dell'utente**: il consenso vale per la voce (per la registrazione, legato
  al suo sha256), non per il singolo episodio. Un consenso revocato blocca anche il riuso;
  una registrazione sostituita va riconfermata. `clone_cost_usd` sta in
  `providers.<nome>.clone_cost_usd`.

### D5 · Consenso
- **Decisione del piano** (approvato il 2026-10-06): il blocco "niente clonazione senza
  consenso registrato" vale per i tool (`voices_prepare.py`, `produce.py`) e quindi per
  l'adattatore e il subagent.
- **Confermato (2026-10-07): il blocco vale solo nei tool**; `run.py` resta invariato.
  Il nome di chi registra il consenso si passa con `--by`, come nei tool del copione
  (`--confirmed-by` non si usa).
- Storia della domanda: **"consenso anche in run.py"**. L'utente l'ha indicato come decisione
  presa, ma in questa sessione non risulta. Risulta il contrario: il piano proponeva
  "`run.py` resta invariato (nessun blocco sul consenso)", e quella proposta non è mai
  stata confermata né respinta. Va confermato all'inizio di C2.
  - Se il consenso vale anche in `run.py`, il suo passo di clonazione deve leggere
    `voci_disponibili/<nome>.json`.
  - Senza consenso registrato non clona. Può chiederlo in modo interattivo e registrarlo:
    `run.py` usa `input()`, quindi è possibile.
- **Da confermare: `--confirmed-by`**. Indicato come decisione presa, ma non compare né
  nella conversazione né nel codice. Interpretazione probabile: il nome di chi registra
  il consenso (`voice_meta.py --consent --confirmed-by U`, al posto di `--by` previsto
  nel piano). Va confermato nome e uso all'inizio di C2.
  - Oggi i tool del copione usano `--by` (`script_save`, `script_write`, `script_revise`,
    `script_edit`, `script_versions --restore`, `script_approve`).
  - Se `--confirmed-by` deve valere anche lì, va deciso.

### D6 · DOCX (2026-10-07)
- **Decisione**: `python-docx==1.2.0` è obbligatorio in `requirements.txt` e in
  `doctor.py`.
- **Motivo**: il wireframe accetta DOCX.
- **Stato**: installato nella `.venv` il 2026-10-07 con il permesso dell'utente.

### D7 · Errori dell'API Anthropic (2026-10-07)
- **Decisione**: `src/script_writer.classify_api_error`.
  - Exit 2: chiave non valida o senza permessi (`ModelAuthError`), credito esaurito
    (`ModelCreditExhausted`, riconosciuto dal testo della risposta 400: euristica
    dichiarata).
  - Exit 1 con `details.retry_after_seconds`: rate limit (`ModelRateLimited`),
    sovraccarico e 5xx (`ModelOverloaded`), rete (`ModelUnreachable`).
- **Motivo**: chiave e credito non si risolvono riprovando (come il provider vocale); gli
  altri sì.
- Verificato prima: il subagent non usa `script_write.py`/`script_revise.py`, e `run.py`
  usa `ScriptWriter` direttamente. Le nuove eccezioni sono sottoclassi di
  `ScriptWriterError`.

### D8 · Ritiro dell'approvazione su cambio di cast (2026-10-07)
- **Decisione**: in `episode_config.py`, cambiare formato, voci, nomi pronunciati o
  descrizioni dei ruoli dopo l'approvazione la ritira (`approval_revoked` nel JSON).
  Titolo e durata no. Riassegnare lo stesso valore non conta.
- **Motivo**: il copione era stato approvato per un altro cast anche se il testo (e lo
  sha256) non cambia. Le descrizioni sono incluse perché il presentatore le pronuncia.
- Allo stesso modo il **ripristino di una versione** ritira sempre l'approvazione (C1),
  come chiede il wireframe ("da approvare di nuovo").

### D9 · Copione dall'API con errori bloccanti (piano approvato)
- **Decisione**: salvato come versione con `errors` e `approvable: false`;
  `script_approve.py` lo rifiuta (`ScriptHasErrors`). Non viene rigenerato da solo.
- **Motivo**: la chiamata è già pagata e non si spende di nuovo senza l'utente.

### D10 · Correzioni di sicurezza C0+C1 (2026-10-07)
- **ALTA-1**: i documenti si leggono solo da `documenti/`.
  - Niente ricerca nella cartella corrente, niente file nascosti o senza estensione,
    niente uscite con `../` o link simbolici.
  - Il percorso libero resta solo con `--trusted-paths`, per subagent e CLI; l'adattatore
    non lo passa mai.
  - Il subagent usa il flag su `doc_extract.py` e `run_finalize.py`.
- **ALTA-2**: `--run-dir`/`--session` sono accettati solo come sottocartelle dirette di
  `output/` in tutti i tool.
  - I lock non creano più cartelle.
  - I file JSON passati per percorso devono stare in `output/`, salvo `--trusted-paths`.
- **Motivo**: un `doc_extract.py .env` restituiva le chiavi; un `--run-dir` qualsiasi
  scriveva fuori dall'Archivio. Riprodotto e corretto.

### Altre decisioni minori (dettagli nei resoconti)
- L'interruzione (Ctrl-C/SIGTERM) dà exit 1 `Interrupted`, mai codici nuovi.
- `music_list`/`music_set`: un nome ambiguo dà `AmbiguousName` (era `needs_user_input`).
- `VersionConflict` è exit 1, non 3: la UI ricarica, non riprova.
- Una voce che coincide a meno di maiuscole e accenti è accettata; una corrispondenza
  parziale dà `AmbiguousName`.
- Durata dell'episodio da 1 a 180 minuti; il presentatore è richiesto nel cast web.
- Una nuova stesura su una run con copione aggiunge una versione `riscrittura`: la storia
  non si cancella.

## 4. Richieste ricevute e non ancora completate

1. **Revisione di sicurezza di C4** (`db8e47d..HEAD`), con le alte corrette prima di C5.
2. **OK per C5.**

## 5. Annotazioni per le fasi successive e per la fase finale

- **Da verificare nelle prossime revisioni di sicurezza** (dal rapporto C0+C1):
  - whitelist delle tag dei retake (C3);
  - consenso prima della clonazione, nessun retry, `voices_prepare.py`/`synthesize.py`
    mai con `--no-approval-gate` dall'adattatore (C2);
  - lock e rilettura per i metadati di voci e musiche (C4);
  - elenco dell'Archivio derivato da `output/` (C4).
- **Caricamenti** (a carico dell'adattatore, da scrivere nel contratto in C5):
  - nome ridotto a un nome semplice, senza `..`, separatori o punto iniziale;
  - niente symlink;
  - dimensione massima in streaming;
  - niente sovrascrittura silenziosa di un file con lo stesso nome. Una voce sostituita
    cambia la persona di un copione già approvato: valutare uno sha256 del campione nel
    consenso.
  - Valutare ffmpeg su file caricati con `-protocol_whitelist file` e formato forzato.
- **Argomenti da non esporre mai all'utente web** (sezione del contratto "a carico
  dell'adattatore", C5):
  - `--config`: `script.client_factory` importa un modulo qualsiasi;
  - `--trusted-paths`, `--save-to`, `--text-file`, `--session`, `--script-json`,
    `--script`;
  - `--no-approval-gate` e, quando ci sarà, `--no-consent-gate`.
  - Dal 2026-10-07 sono anche **rifiutati** dai tool in modalità web
    (`PODCAST_AGENT_WEB=1`, `NotAllowedInWebMode`). Il contratto lo documenta già.
- **Fase finale (C6)**: `run.py` allineato a `src/music_context.py`.
- **Fase finale (richiesta dell'utente, 2026-10-07)**: `tools/lessons.py --propose` deve
  usare la stessa classificazione degli errori API del copione
  (`src/script_writer.classify_api_error`): oggi i suoi errori escono con exit 1 e il nome
  della classe della libreria.
- **Fuori perimetro, da segnalare nel riepilogo finale**:
  - In modalità libera (`run.py`, `--trusted-paths`) un file senza estensione si legge
    ancora come testo: è uso locale.
- **README e LEGENDA** non sono ancora aggiornati per C0–C1 (previsto in C5).
  - Mancano i nuovi tool: `episode_config`, `script_show`, `script_edit`,
    `script_versions`.
  - Mancano i nuovi moduli `src/`, `--trusted-paths` e `python-docx` obbligatorio.
- **Pulizia da proporre all'utente**:
  - `.venv-vecchia-copia/` (venv copiata, da cancellare quando l'utente vuole);
  - i due `.DS_Store` ancora tracciati dal primo commit (`../.DS_Store`,
    `podcast-agent/.DS_Store`).
- **`docs/wireframe/`**: la sottocartella annidata `docs/wireframe/wireframe/` è come
  l'ha fornita l'utente: non spostata.
- **Il piano originale** era anche in `~/.claude/plans/typed-stargazing-hoare.md`, fuori
  dal repository. La versione di riferimento è ora questa.
  - Rispetto a quel file, `src/cast.py` è diventato `src/episode_config.py` +
    `src/episode_setup.py`.
  - `episode_config.py` usa flag diretti (`--formato`, `--voce RUOLO=...`) invece di
    `--set`/`--show`.
