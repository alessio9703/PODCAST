# Contratto d'interfaccia · podcast-agent per l'adattatore web

> Documento in costruzione: si completa fase per fase (vedi `docs/avanzamento/`).
> Questa versione copre il contratto generale (fase C0). Le azioni schermata
> per schermata arrivano con le fasi C1–C4; la versione completa con la
> sezione "a carico dell'adattatore" chiude la fase C5.

## 1. Come si chiama un comando

Ogni azione è un comando `tools/<nome>.py` lanciato come sottoprocesso:

```
<progetto>/.venv/bin/python tools/<nome>.py [argomenti] [--config PERCORSO]
```

- **stdout**: sempre **un solo oggetto JSON**, sia in caso di successo sia di
  errore. Va letto per intero solo a processo terminato.
- **stderr**: messaggi per l'umano (avanzamento, avvisi). Non va interpretato:
  si può mostrare in un pannello "dettagli" o registrare nei log.
- **exit code**: uno di quattro valori, sempre coerente con il JSON.
- Nessun comando chiede input da tastiera: tutto passa dagli argomenti.
- `--config` è accettato da tutti i comandi (default: `config.yaml` del
  progetto).

## 2. I quattro esiti

| exit | `ok` | Significato | Cosa fa la UI (vedi `Stati.dc.html`) |
|---|---|---|---|
| 0 | `true` | Tutto a posto | Conferma breve, si prosegue |
| 1 | `false` | Errore correggibile dall'utente | Dice cosa correggere, accanto al campo che l'ha causato |
| 2 | `false` | Errore del provider esterno (voce/sintesi) | **Blocco, nessun "Riprova"** automatico: una clonazione ripetuta creerebbe voci duplicate a pagamento |
| 3 | `false` | Serve una scelta dell'utente | Mostra la domanda e le opzioni (da `details`), poi richiama il comando con la risposta |

### Forma del JSON di errore

```json
{
  "ok": false,
  "error_type": "AmbiguousName",
  "error": "messaggio leggibile, in italiano",
  "exit_code": 3,
  "details": { "...": "solo se l'errore porta dati strutturati" }
}
```

- `error_type` è un **nome stabile**: la UI decide cosa mostrare da questo,
  mai dal testo di `error`, che può cambiare.
- `details` è **opzionale**: c'è solo per gli errori che hanno dati da
  mostrare. Un errore senza `details` si mostra con il solo testo di `error`.

## 3. `error_type` con significato fisso

L'elenco cresce fase per fase; quelli qui sotto sono già in vigore.

### `Interrupted` · exit 1

Il comando è stato fermato da SIGTERM (il modo con cui l'adattatore ferma un
sottoprocesso) o da Ctrl-C prima di finire. Il JSON viene scritto comunque.

```json
{
  "ok": false,
  "error_type": "Interrupted",
  "error": "Interrotto prima della fine. Nessun file e' rimasto scritto a meta': il comando si puo' rilanciare.",
  "exit_code": 1
}
```

- Nessun `details`.
- Tutte le scritture su disco sono atomiche, quindi un'interruzione non lascia
  file corrotti: il comando si può rilanciare così com'è.
- In precedenza un'interruzione usciva con codice 130 senza JSON: questo non
  succede più.
- Il comando "produci" (fase C2) avrà una propria interruzione ordinata, con un
  `error_type` dedicato e il punto raggiunto in `details`.

### `AmbiguousName` · exit 3

Un nome scritto dall'utente corrisponde a più elementi: il comando **non
sceglie mai al posto dell'utente**. Oggi lo usano `music_list.py --resolve` e
`music_set.py`.

```json
{
  "ok": false,
  "error_type": "AmbiguousName",
  "error": "'piano' non e' un nome certo in intro/: candidati Piano calmo, Piano forte.",
  "exit_code": 3,
  "details": {
    "category": "intro",
    "query": "piano",
    "candidates": ["Piano calmo", "Piano forte"]
  }
}
```

| Campo di `details` | Tipo | Significato |
|---|---|---|
| `category` | stringa | Dove si è cercato (per le musiche: `intro`, `outro`, `sottofondo`) |
| `query` | stringa | Il testo cercato, così come è arrivato |
| `candidates` | lista di stringhe | I nomi **esatti** fra cui scegliere |

La UI mostra i candidati come opzioni e richiama lo stesso comando passando il
candidato scelto così com'è: viene riconosciuto come nome esatto e si ottiene
exit 0. Unica eccezione: due file i cui nomi differiscono solo per maiuscole,
accenti o trattini restano ambigui anche così, e vanno rinominati nella
libreria.

Un nome che non corrisponde a niente **non** è un `AmbiguousName`: è exit 1
senza `details`, con l'elenco dei disponibili nel testo.

### `LockTimeout` · exit 1

Un file condiviso (per esempio il registro delle voci) è rimasto in uso da
un'altra operazione per più di 30 secondi. In `details`: `file` e
`timeout_seconds`. Di norma non succede: le operazioni protette durano
millisecondi. Se compare, la UI può proporre di riprovare.

### Errori già esistenti, invariati

| error_type | exit | Quando |
|---|---|---|
| `needs_user_input` | 3 | Esiti 3 storici non ancora strutturati (es. `lessons.py --accept` quando serve togliere una lezione) |
| `MusicChangedError` | 3 | Un brano già usato è stato sostituito: serve `--accept-changed-music` |
| nome della classe del provider (`ProviderQuotaError`, `ProviderAuthError`, ...) | 2 | Errore del servizio vocale |
| nome della classe di dominio (`ValueError`, `ScriptWriterError`, ...) | 1 | Errore correggibile; il testo dice cosa fare |

## 4. Concorrenza

Più comandi possono girare in parallelo, anche per utenti diversi:

- i file condivisi (`voices_registry.json`, e dalle prossime fasi i metadati
  di voci e musiche) si modificano sotto lock fra processi, rileggendo il
  contenuto dentro il lock: due clonazioni simultanee non si cancellano a
  vicenda;
- tutte le scritture sono atomiche: chi legge vede la versione vecchia o
  quella nuova, mai una a metà.

Accanto ai file condivisi compaiono file `*.lock`: sono normali, non vanno
cancellati mentre un comando è in esecuzione (sono esclusi da git).
