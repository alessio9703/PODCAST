# C0+C1 · correzioni (medie e basse della revisione C0+C1, modalità web)

Data: 2026-10-07. Branch `integrazione-web`. Richiesta dell'utente dopo la revisione
`C0+C1-sicurezza.md`: chiudere medie e basse, aggiungere la modalità web, poi rivedere il
diff e passare a C2.

## 1. Cosa è stato fatto

### python-docx
- `python-docx==1.2.0` installato nella `.venv` con il permesso dell'utente (D6 chiusa).
- Il test DOCX di `test_web_integration.py` non è più saltato; `doctor.py` dà ok.

### MEDIA-1 · documenti
- **Limiti** in `config.yaml`, sezione `documents:`, letti da
  `Config.document_limits` → `src/pdf_reader.DocumentLimits`:
  - `max_bytes` (20 MB): controllato con `stat()` prima di aprire il file;
  - `max_pages` (500): `len(reader.pages)` prima del ciclo di estrazione;
  - `max_chars` (2.000.000): contati durante l'estrazione; un PDF si ferma alla prima
    pagina oltre il limite;
  - `max_unpacked_bytes` (100 MB): somma delle dimensioni scompattate dichiarate nello zip
    di un DOCX, prima di aprirlo con python-docx. **Aggiunto rispetto ai tre limiti
    richiesti** (byte, pagine, caratteri): un DOCX è uno zip, e senza questo un file piccolo
    può contenere un XML enorme.
  - Mancano nei config vecchi: valgono i default. Un valore non positivo è un `ConfigError`.
- Errore `DocumentTooLarge` (exit 1), `details: {limite, massimo, valore, file}`.
- Valgono per `doc_extract.py`, `run_init.py`, `episode_config.py --documento`,
  `script_write.py --document` e `--text-file` (per quest'ultimo solo `max_bytes`).
  `run.py` non è stato toccato: usa i default di `extract_text`.
- **Estrazione fuori dal lock**: `src/episode_setup.load_document()` estrae prima di
  `locked_update`; dentro il lock `set_document()` registra soltanto (record e testo,
  sha256 compreso, calcolati fuori).
- **Una sola estrazione in `run_init.py`**: il `Document` estratto prima di creare la
  cartella si passa ad `apply_changes`, che accetta un nome o un `Document`.
- **Sessione**: `document_text` è il testo troncato a `script.max_document_chars`, cioè quello
  che il modello ha visto. Il documento intero resta in `documento_estratto.txt`. Le sessioni
  vecchie, con il testo intero, si leggono come prima (nessun cambio di formato).

### BASSA-1 · approvazione con versione
- `script_approve.py` approva solo con `--version V` o `--current` (mutuamente esclusivi).
  Senza: exit 1 `VersionRequired`, `details.current_version`. `--revoke` non lo richiede.
- `.claude/agents/podcast-agent.md` usa `--current`.

### BASSA-2 · informazioni interne verso la UI
- Campo `run` (nome della cartella) accanto a `run_dir` in `run_init`, vista dell'episodio
  (`episode_config`), `script_show`, `script_versions --restore`, `script_approve`,
  `estimate --run-dir`, `music_set`, `run_finalize`. `documento.nome` c'era già.
- `current_work()` non restituisce più il `pid` (resta in `_lavoro.json`): vale per
  `script_show.in_corso` e per `RunBusy.details.in_corso`.
- Messaggi solo con il nome: `locate_document` (anche in modalità libera),
  `LockTimeout.details.file`, sessione non trovata o illeggibile, voce non trovata,
  "Cartella della run inesistente" nei tool.
- Riserva centrale: `_redact()` in `tools/_common.py` rende relativo al progetto ogni
  percorso nel JSON d'errore (`error`, `details`) e nel messaggio d'errore su stderr; fuori
  dal progetto toglie la home (`~`).
- Le chiavi assolute delle risposte di successo restano (le usa il subagent); il contratto
  elenca quelle che l'adattatore non deve mostrare.
- Lasciato com'era: `episode_signals` non riceve il campo `run` (è lo schema di
  `_segnali.json`, persistente).

### BASSA-3 · cartella della run
- `make_run_dir`: `output/` creata una volta (`exist_ok`), poi `mkdir(parents=False)` in
  ciclo con suffisso `-2`, `-3`… sul `FileExistsError`. Il `mkdir` è il controllo.

### Modalità web (`PODCAST_AGENT_WEB=1`)
- `tools/_common.parse_args(parser, web_forbidden=...)` sostituisce `parser.parse_args()` in
  tutti i 22 tool. In modalità web rifiuta con exit 1 `NotAllowedInWebMode`
  (`details.argomenti`) gli argomenti di `WEB_FORBIDDEN_ARGS`: `--config`,
  `--trusted-paths`, `--no-approval-gate`, `--no-consent-gate` (per C2), `--save-to`,
  `--text-file`, `--session`, `--script-json`, `--script`; più `--document` di
  `script_write.py`.
- Il controllo guarda i valori interpretati da argparse (valore ≠ default), quindi copre
  abbreviazioni (`--trusted`) e `--opzione=valore`.
- Modalità accesa per qualunque valore che non sia vuoto, `0`, `false`, `no`, `off`: un
  valore scritto male toglie poteri invece di darli.
- Riserva: `bootstrap()` in modalità web rifiuta un tool che non è passato da
  `parse_args()`.
- Conseguenza voluta: `synthesize.py`, `estimate.py` (`--script` obbligatorio) e
  `script_save.py` (`--script-json`) non si usano in modalità web; produzione e stima per
  `--run-dir` arrivano in C2.

## 2. Test

Tutte le suite verdi con `.venv/bin/python`:

| Suite | Prima | Dopo |
|---|---|---|
| smoke | 68 | 68 |
| tools_cli | 183 (+2 saltati) | 183 (+2 saltati, servono `ANTHROPIC_API_KEY`) |
| music | 73 | 73 |
| lessons | 80 | 80 |
| web_integration | 175 (+1 saltato) | **283**, 0 saltati |

Nuove sezioni in `test_web_integration.py`:
- `[sicurezza] MEDIA-1`: byte, pagine (prima dell'estrazione), caratteri, DOCX normale e
  bomba zip, config non valido, `run_init` senza bozza vuota; con `episodio.json` bloccato
  un documento troppo grande è rifiutato subito (estratto prima del lock); `run_init` estrae
  una volta sola (conteggio delle chiamate); sessione con il solo testo troncato; sessione
  vecchia leggibile; `--text-file` troppo grande.
- `[sicurezza] BASSA-1`: `VersionRequired`, `--current`, `--revoke`, `--version`, i due
  flag insieme rifiutati.
- `[sicurezza] BASSA-2`: campi `run`, niente `pid` in `in_corso` e in `RunBusy`, `pid` nel
  file, `LockTimeout` con il solo nome, messaggi senza percorsi assoluti, `_redact`.
- `[sicurezza] BASSA-3`: 8 processi con lo stesso titolo nello stesso istante → 8 cartelle;
  suffissi `-2`, `-3`.
- `[modalita' web]`: ogni tool rifiuta `--config`; ogni altro argomento vietato rifiutato
  nei tool che lo hanno (18 casi) e accettato fuori dal web; abbreviazioni e `=`; esiti veri
  fuori dal web (`--trusted-paths`, `--save-to`, `--session`); un comando senza argomenti
  vietati passa; valori della variabile; riserva in `bootstrap()`; nessun tool chiama più
  `parser.parse_args()`.

Test esistenti modificati (comportamento cambiato apposta): 4 chiamate a
`script_approve.py` senza versione, ora con `--current` (tools_cli) o `--version 1`
(web_integration).

## 3. Revisione di sicurezza del diff

Rapporto: `C0+C1-correzioni-sicurezza.md`. **0 alte**, 2 medie, 4 basse, tutte da decidere
con l'utente:
- **MEDIA-1**: i limiti non limitano il *costo* dell'estrazione di un PDF (stream di
  contenuto condivisi fra le pagine, ore di CPU per un file piccolo). Proposta: budget sui
  byte decodificati, scadenza in secondi, timeout lato adattatore.
- **MEDIA-2**: DOCX con molti `<w:p/>` (minuti e GB entro 100 MB scompattati), zip con
  moltissime entrate. Proposta: limite sulle parti XML, sul numero di entrate, timeout.
- **BASSA-1**: `--current` accettato in modalità web. Proposta: vietarlo lì.
- **BASSA-2**: `--out`, `--voices`, `--audio-paths` confinati a `output/` e non alla run
  (si può sovrascrivere un file di un'altra run). Proposta: vietarli in web o confinarli alla
  run.
- **BASSA-3**: un errore di argparse esce con exit 2 e stdout vuoto (letto come errore del
  provider); es. `--text "- Sì"`.
- **BASSA-4**: `_redact` non copre percorsi Windows nei `repr` né percorsi fuori dalla home;
  messaggi di successo su stderr ancora con percorsi assoluti.

## 4. Decisioni prese

- Limite in più `max_unpacked_bytes` per i DOCX (vedi sopra).
- Sessione: testo troncato invece del riferimento a `documento_estratto.txt` (la richiesta
  ammetteva entrambi): vale anche per `script_write --document`/`--text-file`, che non
  scrivono `documento_estratto.txt`.
- `--current` come flag a sé, mutuamente esclusivo con `--version`.
- Redazione centrale dei percorsi solo sugli errori, non sulle risposte di successo.

## 5. Annotazioni

- `tools/lessons.py --propose` deve usare `classify_api_error` (fase finale): annotato in
  `STATO.md`.
