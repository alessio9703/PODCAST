# podcast-agent · istruzioni per Claude Code

Questo file vale per ogni sessione. Lo stato del lavoro in corso è in
`docs/avanzamento/STATO.md`: **leggilo prima di fare qualunque cosa**.

## Obiettivo in corso: integrazione web

Rendere `podcast-agent` integrabile con un'interfaccia web: ogni funzione del wireframe
in `docs/wireframe/wireframe/` deve essere eseguibile con i comandi in `tools/`, senza terminale
interattivo e senza Claude Code nel mezzo.

Server web, autenticazione, upload HTTP, coda dei lavori e frontend **non** fanno parte
del lavoro: li costruirà un adattatore separato, che chiamerà `tools/*.py` come
sottoprocessi e leggerà il loro JSON. Il compito qui è esporre tutto ciò che serve
all'adattatore e documentarlo in `docs/CONTRATTO_INTERFACCIA.md`.

Da leggere prima di toccare il codice:
- `README.md`, `LEGENDA.md`, `.claude/agents/podcast-agent.md`, `prompts/copione.md`;
- `tools/_common.py` e `tools/_approval.py` (il contratto);
- `docs/wireframe/wireframe/`: un `.dc.html` per schermata. `Flusso.dc.html` è la mappa,
  `Stati.dc.html` dice come la UI tratta gli exit code, `canvas.json` dà i titoli.

## Regole non negoziabili

- **Contratto dei tool** (`tools/_common.py`):
  - stdout = UN solo oggetto JSON, stderr = messaggi per l'umano;
  - exit code 0 ok / 1 errore di dominio / 2 errore del provider / 3 serve l'utente;
  - ogni tool usa `run_tool`, `emit`, `note`;
  - nessun `input()` nei tool;
  - errori strutturati con `error_type` stabile e `details` (`src/errors.py`).
- **La logica sta in `src/`**: i file in `tools/` restano sottili (argomenti → `src/` →
  `emit`).
- **Mai indebolire le garanzie esistenti**:
  - gate di approvazione legato allo sha256 del copione;
  - nessun retry sulla clonazione;
  - nomi di voce mai indovinati;
  - controllo sha256 delle musiche;
  - cache dei segmenti scritta in modo atomico;
  - promemoria sul consenso;
  - nessun bypass che non sia un flag esplicito.
- **`run.py` e il subagent `@podcast-agent` devono continuare a funzionare.** Se un tool
  nuovo serve anche al subagent, aggiorna `.claude/agents/podcast-agent.md`.
- **Test**:
  - i test esistenti restano verdi;
  - ogni funzione nuova ha test in `tests/`, con `tests/fake_provider.py` e
    `tests/fake_anthropic.py`;
  - mai chiamate reali a Fish Audio o Anthropic.
- **Concorrenza**: le scritture su file condivisi (`voices_registry.json`, metadati delle
  librerie) sono atomiche e sotto lock, con rilettura dentro il lock (`src/fileio.py`).
  L'adattatore avrà più utenti.
- **Stato derivato**: lo stato di un episodio si DERIVA dai file della run (e dai lock),
  non si duplica in un campo che può andare fuori sincrono.
- **Percorsi da input confinati** (`src/paths.py`):
  - `--run-dir`/`--session` solo sottocartelle dirette di `output/`;
  - documenti solo da `documenti/`;
  - file JSON passati per percorso solo dentro `output/`;
  - `--trusted-paths` esiste solo per l'uso locale, e l'adattatore non lo passa mai.

## Metodo di lavoro

- Si lavora **solo sul branch `integrazione-web`**. Il commit di baseline è `33068cc`.
- **Un commit per fase.** Prima di ogni commit controlla che fra i file in stage non ci
  siano `.env`, `documenti/`, `voci_disponibili/`, `musiche/`, `output/`, venv o
  `.DS_Store`. Se ci sono, **fermati**: non basta segnalarli.
- **A fine fase**:
  1. tutte le suite verdi;
  2. resoconto in `docs/avanzamento/<fase>.md` (cosa è stato fatto, test, decisioni,
     dubbi aperti);
  3. contratto aggiornato;
  4. `docs/avanzamento/STATO.md` aggiornato;
  5. commit;
  6. messaggio all'utente.
- **Revisione di sicurezza a fine fase**: subagent `.claude/agents/security-reviewer.md`,
  rapporto in `docs/avanzamento/<fase>-sicurezza.md`.
  - Se il tipo `security-reviewer` non è registrato nella sessione, usa un agente
    generico che legge ed esegue quel file.
  - Le gravità **alte** si correggono prima di passare alla fase successiva.
  - **Medie e basse** si decidono con l'utente.
- **Niente installazioni senza chiedere**: nessun `pip install`, in nessuna venv. Gli
  strumenti di sviluppo si propongono in `requirements-dev.txt`.
- Le decisioni aperte si chiedono all'utente, non si prendono da soli.
- **Ogni decisione presa durante il lavoro** (dall'utente o confermata da lui) va inserita
  come **punto del piano della fase in cui va attuata**, non solo registrata in
  `STATO.md`: una decisione che vive solo nell'elenco delle decisioni rischia di non
  essere mai attuata (è successo con D11 fra C2 e C7).

## Test

Sempre con la venv del progetto (il python di sistema non ha le dipendenze):

```bash
.venv/bin/python tests/test_smoke.py
.venv/bin/python tests/test_tools_cli.py
.venv/bin/python tests/test_music.py
.venv/bin/python tests/test_lessons.py
.venv/bin/python tests/test_web_integration.py
```

Ogni suite stampa "N controlli superati" ed esce con 0. La `.venv` è stata ricreata il
2026-10-06 in questa copia del progetto; quella vecchia copiata da un'altra cartella
(`.venv-vecchia-copia/`) è stata cancellata il 2026-10-07 ed è nel `.gitignore`.
