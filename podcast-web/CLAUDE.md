# podcast-web · istruzioni per Claude Code

Controller web di LIL Podcast: interfaccia e API davanti a `podcast-agent/`. Il piano è in
`docs/avanzamento/A0.md`, lo stato delle fasi nei resoconti `docs/avanzamento/<fase>.md`.
Rispondi sempre in italiano.

## Specifica

`../podcast-agent/docs/CONTRATTO_INTERFACCIA.md` (agente al tag `v1.0.1-web`, file
`podcast-agent/VERSION` = `1.0-web`). In particolare §0 (`PODCAST_AGENT_WEB=1`,
`doctor.py --web`), §2–§3 (esiti ed `error_type`) e "A carico dell'adattatore".

Produzione: browser → CMS di LIFE (login) → VPS, che riceve richieste SOLO dal CMS. Il CMS
inoltra tutto al controller, pagine comprese.

## Regole non negoziabili

1. **Separazione**: `podcast-web` non importa niente da `podcast-agent` e non lo modifica.
   Se serve un cambiamento nell'agente, ci si ferma e si chiede all'utente.
2. **Un solo punto lancia l'agente**: `app/agente/`. La tabella dichiarativa
   (`app/agente/tabella.py`) elenca per ogni azione argomenti ammessi, timeout del
   contratto, se è un lavoro lungo, permesso, conferme. Ciò che non è in tabella non parte.
   Sempre: python di `podcast-agent/.venv`, cwd = podcast-agent, `PODCAST_AGENT_WEB=1`,
   ambiente minimo, argomenti in lista (mai shell), `--opzione=valore`, posizionali dopo
   `--`. Percorso dell'agente da configurazione (`AGENT_DIR`).
3. **Esiti**: un solo formato JSON verso il browser (`app/servizi/esiti.py`). 0 → 200;
   1 → 422/409 con `error_type` e `details`; errori dell'adattatore (`InvalidArguments`,
   `NotAllowedInWebMode`, `ByRequired`, `InternalError`) → 500, sempre nel log con il
   dettaglio; 2 → 502 "servizio esterno"; 3 → 409 con un gettone di conferma (pochi minuti,
   monouso, legato a utente, run e domanda). I flag di conferma si aggiungono SOLO con un
   gettone valido. Timeout → 504. Mai percorsi assoluti, chiavi o `voice_id` verso il
   browser. Nomi di §3 del contratto, non inventarne (gli errori del controller hanno un
   elenco chiuso a parte, in italiano: `app/servizi/errori.py`).
4. **Identità solo da `app/ingresso/`**: `AUTH_MODE=demo` (utente fisso, tutti i ruoli,
   solo 127.0.0.1) o `AUTH_MODE=cms` (header firmati HMAC-SHA256; specifica in
   `app/ingresso/cms.py` e in `DEPLOY.md`). `--by` è sempre l'utente dell'ingresso.
5. **Permessi**: tabella ruolo → permessi (`app/servizi/permessi_default.json`, o
   `PERMESSI_FILE`). Ruoli: autore, approvatore, gestore_voci, amministratore.
6. **Registro attività** (`app/registro.py`): JSONL append-only, chi, quando, azione, run,
   esito, costo stimato. Obbligatorio per approvazioni, consensi, revoche, produzioni.
7. **Lavori lunghi** (stesura, revisione, produzione, retake): coda con UN worker,
   processo uvicorn unico. Avanzamento da `_eventi.jsonl` (SSE, polling come ripiego).
   Interruzione: UN solo SIGTERM, mai un secondo. Lo stato degli episodi non si conserva
   nel controller: si chiede sempre all'agente.
8. **File**: caricamenti con tutte le regole del contratto (nome, estensione, firma,
   dimensione in streaming, nessuna sovrascrittura senza conferma); download solo dei file
   che l'agente elenca, risolti dentro la cartella prevista, con Range.
9. **Avvio**: `VERSION` dell'agente = `AGENT_VERSIONE_ATTESA` e `doctor.py --web` superato,
   altrimenti il controller non parte. `/salute` per il CMS.
10. **Installazioni**: solo `.venv` di podcast-web, solo fastapi, uvicorn, pytest, httpx
    (python-multipart solo se serve), versioni fissate in `requirements.lock`, pubblicate da
    almeno due settimane. Nient'altro senza chiedere. Nessuna chiamata reale nei test.

## Metodo

- Fasi A0–A5 (piano in `docs/avanzamento/A0.md`). A fine fase: test verdi, resoconto in
  `docs/avanzamento/<fase>.md`, commit. Prima del commit: niente `.env`, `.venv`, `dati/`,
  `output/`, librerie dell'agente, `.DS_Store` fra i file in stage.
- Revisione di sicurezza a fine A1, A2, A4: `../podcast-agent/.claude/agents/security-reviewer.md`
  sul diff della fase, con in più: firma e ingresso, gettoni di conferma, permessi, upload e
  download, SSE. Rapporto in `docs/avanzamento/<fase>-sicurezza.md`. Si correggono alte e
  medie; il resto va in `DEPLOY.md`, "Da fare prima della produzione". Una revisione per
  fase, niente cicli.

## Comandi

```bash
.venv/bin/python -m pytest                      # test (agente finto, nessuna chiamata reale)
AUTH_MODE=demo AGENT_DIR=/percorso/podcast-agent AGENT_VERSIONE_ATTESA=1.0-web .venv/bin/python -m app
```

## Mappa

- `app/config.py` configurazione da variabili d'ambiente
- `app/ingresso/` chi è l'utente (demo, cms)
- `app/agente/` tabella dei comandi ed esecutore
- `app/servizi/` esiti, errori, gettoni, permessi, avvio, `azioni.py` (permesso → agente → esito → registro)
- `app/api/` rotte · `app/lavori/` coda e eventi · `app/file/` caricamenti e download
- `app/registro.py` registro attività · `static/` interfaccia · `deploy/` esempi di installazione
- `tests/agente_finto.py` lo script che fa la parte dei tool nei test
