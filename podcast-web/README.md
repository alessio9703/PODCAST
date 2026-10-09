# LIL Podcast · controller web (podcast-web)

Interfaccia web e API davanti a `podcast-agent/`. Il controller lancia i comandi dell'agente
(`tools/*.py`) come sottoprocessi, ne traduce gli esiti per il browser e serve l'interfaccia
(HTML, CSS e JavaScript senza build). In produzione sta su una VPS dietro il CMS di LIFE, che
fa il login e firma ogni richiesta (`DEPLOY.md`).

```
browser ──► CMS di LIFE (login, firma) ──► podcast-web (VPS) ──► podcast-agent/tools/*.py
```

## Requisiti

- Python 3.11 o più recente (sviluppato con 3.14).
- `podcast-agent/` accanto a questa cartella, con la sua `.venv` e il file `VERSION`
  (`1.0-web`); vedi `../podcast-agent/README.md`.

## Installazione

```bash
cd podcast-web
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest            # ~1 minuto, nessuna chiamata reale
```

`requirements.lock` fissa tutte le versioni (dirette e transitive). Le sole dipendenze
dirette sono fastapi, uvicorn, httpx e pytest.

## Avvio in locale con i servizi finti (consigliato per provare)

Nessuna chiamata a Fish Audio o Anthropic, nessuna chiave.

```bash
.venv/bin/python -m strumenti.demo        # crea dati/demo-agente (copia dell'agente con i servizi finti)

AUTH_MODE=demo AGENT_DIR=$PWD/dati/demo-agente AGENT_VERSIONE_ATTESA=1.0-web \
  REGISTRO_FILE=$PWD/dati/registro-demo/registro.jsonl .venv/bin/python -m app
```

Poi apri http://127.0.0.1:8765/ (ferma con Ctrl-C).

- In modalità `demo` l'utente è fisso (`DEMO_UTENTE`, `DEMO_NOME`) con tutti i ruoli, e il
  server ascolta solo su 127.0.0.1.
- Il client finto non legge il documento: risponde sempre con lo stesso copione. Per la
  stesura scegli il formato **Intervista** con Anna Neri (presentatore), Mario Rossi
  (intervistatore) e Giulia Bianchi (ospite).
- `.venv/bin/python -m strumenti.demo --ricarica` rimette dieci copioni finti in coda;
  cancella `dati/demo-agente` per ripartire da zero.

## Avvio in locale come in produzione (CMS finto, AUTH_MODE=cms)

Il CMS finto (`strumenti/cms_finto.py`) fa quello che farà il CMS di LIFE: login (una pagina
per scegliere l'utente e il ruolo), toglie gli header `X-LIL-*` del browser e firma ogni
richiesta.

```bash
export LIL_SEGRETO=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# terminale 1: il controller, che accetta solo richieste firmate
AUTH_MODE=cms AGENT_DIR=$PWD/dati/demo-agente AGENT_VERSIONE_ATTESA=1.0-web \
  REGISTRO_FILE=$PWD/dati/registro-demo/registro.jsonl .venv/bin/python -m app

# terminale 2 (con lo stesso LIL_SEGRETO): il CMS finto
.venv/bin/python -m strumenti.cms_finto
```

Apri http://127.0.0.1:8780/__cms/ e scegli chi sei (autore, approvatore, gestore voci,
amministratore, o redazione con più ruoli): i pulsanti e i permessi cambiano di
conseguenza. Una richiesta diretta a http://127.0.0.1:8765/ risponde 401.

## Avvio con i servizi veri (costa)

Con `AGENT_DIR` puntato all'agente vero, i comandi usano il suo `config.yaml` e le chiavi
del suo `.env` (`FISH_AUDIO_API_KEY`, `ANTHROPIC_API_KEY`): stesura del copione, clonazione e
sintesi **sono a pagamento**.

```bash
AUTH_MODE=demo AGENT_DIR=$(cd ../podcast-agent && pwd) AGENT_VERSIONE_ATTESA=1.0-web \
  REGISTRO_FILE=$PWD/dati/registro/registro.jsonl .venv/bin/python -m app
```

## All'avvio

Il controller non parte (codice d'uscita diverso da zero, motivo nel log) se:
la configurazione non è valida; `AGENT_DIR/VERSION` non è `AGENT_VERSIONE_ATTESA`;
`doctor.py --web` non passa; le cartelle delle librerie non sono dentro `AGENT_DIR`; il
registro attività o la sua cartella hanno permessi aperti ad altri (servono 0600 e 0700).
`GET /salute` risponde 200 se tutto è a posto, 503 altrimenti.

## Configurazione

Tutto da variabili d'ambiente: l'elenco commentato è in `deploy/.env.example`.

## Struttura

| Percorso | Contenuto |
|---|---|
| `app/config.py` | configurazione |
| `app/ingresso/` | chi è l'utente: `demo.py`, `cms.py` (header firmati) |
| `app/agente/` | tabella dei comandi ed esecutore: l'unico punto che lancia l'agente |
| `app/servizi/` | esiti, errori, gettoni di conferma, permessi, avvio, azioni |
| `app/api/` | rotte HTTP e SSE |
| `app/lavori/` | coda dei lavori lunghi (un worker) ed eventi |
| `app/file/` | caricamenti e download |
| `app/registro.py` | registro attività (JSONL append-only) |
| `static/` | interfaccia; colori e caratteri in `static/css/tema.css` |
| `strumenti/` | demo con i servizi finti, CMS finto, revoche da riapplicare dopo un ripristino (`DEPLOY.md` §7) |
| `deploy/` | `.env.example`, unità systemd, esempio di proxy |
| `docs/avanzamento/` | piano (`A0.md`), resoconti e revisioni di sicurezza per fase |

Regole del progetto: `CLAUDE.md`. Installazione sulla VPS: `DEPLOY.md`.
