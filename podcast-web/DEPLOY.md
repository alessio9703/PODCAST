# DEPLOY · podcast-web per il dev

> Bozza iniziata in A1. In A4 si aggiungono: VPS raggiungibile solo dal CMS (firewall o
> tunnel), HTTPS fra CMS e VPS, unità systemd, backup, timeout del proxy.

## 1. Header firmati dal CMS

Il controller riconosce l'utente **solo** da questi header, che il CMS aggiunge a **ogni**
richiesta inoltrata (pagine, file statici e API; fa eccezione solo `GET /salute`). Il
codice di riferimento è `app/ingresso/cms.py`, e per cambiare i nomi degli header basta
toccare le costanti in cima a quel file.

| Header | Contenuto |
|---|---|
| `X-LIL-Utente` | id stabile dell'utente nel CMS, `^[A-Za-z0-9._@-]{1,100}$` |
| `X-LIL-Nome` | nome da mostrare: finisce nella firma delle azioni ("Approvato da …"). UTF-8 **percent-encoded** (es. `Zo%C3%AB%20D'Amico`; in Python `urllib.parse.quote(nome, safe="")`), al massimo 100 caratteri decodificato |
| `X-LIL-Ruoli` | ruoli separati da virgola, senza spazi: `autore,approvatore` (vedi §2) |
| `X-LIL-Timestamp` | secondi Unix, intero |
| `X-LIL-Firma` | `v1=` + esadecimale minuscolo di HMAC-SHA256(segreto, stringa canonica) |

**Stringa canonica**: queste sette righe unite da `\n` (nessun `\n` finale), codificate in
UTF-8:

```
v1
<X-LIL-Timestamp>
<metodo HTTP in maiuscolo>
<percorso esattamente come arriva al controller, con ?query se c'è, senza decodificarlo>
<X-LIL-Utente>
<X-LIL-Ruoli, esattamente come nell'header>
<X-LIL-Nome, esattamente come nell'header (percent-encoded)>
```

Se il CMS riscrive il percorso (es. riceve `/podcast/api/io` e inoltra `/api/io`), firma
quello **inoltrato**. Il corpo non è firmato: lo protegge l'HTTPS fra CMS e VPS.

Esempio (segreto `0123456789abcdef0123456789abcdef`, ASCII):

```
timestamp 1760000000 · GET /api/episodi?gruppo=bozze · utente mrossi · ruoli autore,approvatore · nome Mario%20Rossi
→ X-LIL-Firma: v1=80be18ebc1f3d9b1590c45c12c3899a9c8ac31ffc8c7cffeeb18479babbfd520
```

```python
import hashlib, hmac, time
from urllib.parse import quote

def header_lil(segreto: bytes, metodo: str, percorso: str, utente: str, ruoli: list[str], nome: str) -> dict:
    ts = str(int(time.time()))
    nome_h = quote(nome, safe="")
    ruoli_h = ",".join(ruoli)
    canonica = "\n".join(["v1", ts, metodo.upper(), percorso, utente, ruoli_h, nome_h]).encode("utf-8")
    firma = "v1=" + hmac.new(segreto, canonica, hashlib.sha256).hexdigest()
    return {"X-LIL-Utente": utente, "X-LIL-Nome": nome_h, "X-LIL-Ruoli": ruoli_h,
            "X-LIL-Timestamp": ts, "X-LIL-Firma": firma}
```

Regole di verifica: tutti gli header presenti, timestamp entro **60 s** dall'ora del
server (orologi sincronizzati con NTP su CMS e VPS), firma confrontata a tempo costante.
Se qualcosa non torna la risposta è `401` con un corpo fisso, senza dettagli: il motivo è
solo nel log del controller. Segreto in `LIL_SEGRETO` (almeno 32 byte); per la rotazione
`LIL_SEGRETO_PRECEDENTE` resta accettato finché il CMS non usa quello nuovo.

Il CMS deve **togliere** dalle richieste del browser ogni header `X-LIL-*` prima di aggiungere
i propri. Fanno eccezione, e vanno inoltrati così come sono, i tre header che vengono dalla
pagina e non danno identità: `X-LIL-Richiesta`, `X-LIL-Nome-File` e `X-LIL-Gettone` (la
conferma della sostituzione di un file). L'elenco completo è in §3.

## 2. Ruoli

Il CMS deve inviare in `X-LIL-Ruoli` **questi nomi**, esattamente così; il dev li associa ai
gruppi aziendali del CMS (es. gruppo "Redazione podcast" → `autore`). Nomi diversi vengono
ignorati.

| Ruolo | Cosa può fare |
|---|---|
| `autore` | creare episodi, caricare documenti, passi 1–3, scrivere, rivedere e modificare il copione |
| `approvatore` | approvare il copione o ritirare l'approvazione, produrre l'episodio e i retake (azioni che **costano**) |
| `gestore_voci` | registrare e revocare il consenso delle voci, confermare una nuova clonazione dopo un esito incerto, caricare voci, cambiare il nome pronunciato |
| `amministratore` | tutto, comprese lezioni, diagnostica, brani e loro licenze |

Tutti i ruoli possono vedere l'Archivio, ascoltare e scaricare. Un utente può avere più
ruoli. La tabella è in `app/servizi/permessi_default.json` e si può sostituire con
`PERMESSI_FILE`.

## 3. Proxy del CMS

- **Caricamenti come corpo grezzo**: i file (documenti, voci, brani) arrivano come corpo
  della richiesta (`Content-Type: application/octet-stream`), non come form multipart. Il
  proxy deve inoltrare il corpo **intatto** e accettarne almeno **20 MB** (documenti e
  registrazioni; per i brani fino a 50 MB). Il nome del file viaggia nell'header
  `X-LIL-Nome-File`, codificato come `X-LIL-Nome` (UTF-8 percent-encoded): il proxy lo
  inoltra così com'è.
- **SSE** (`GET /api/episodi/<run>/eventi`, `Content-Type: text/event-stream`): il proxy
  **non deve trattenere i dati** (buffering disattivato su quella rotta, es. nginx
  `proxy_buffering off;`, oppure l'header `X-Accel-Buffering: no` che il controller manda
  già), e non deve chiudere la connessione per inattività prima di 60 s (il controller
  manda un segnale ogni 15 s). Altrimenti l'avanzamento arriva tutto alla fine. Se lo SSE
  non passa, l'interfaccia ripiega sul polling (`?dopo=N`).
- Le richieste che cambiano qualcosa portano l'header `X-LIL-Richiesta: 1` (difesa CSRF):
  il proxy lo inoltra.
- **Header della pagina da inoltrare intatti**: `X-LIL-Richiesta`, `X-LIL-Nome-File`,
  `X-LIL-Gettone` (confermare la sostituzione di un file già presente), `Range` (ascolto
  dell'audio), `Last-Event-ID` (ripresa dello SSE). Tutti gli altri `X-LIL-*` del browser si
  tolgono (§1).
- **Rotte con corpo binario**: oggi solo `POST /api/documenti` (in A5 anche voci e brani).
  Tutte le altre accettano solo `application/json`, al massimo 256 KB.

## 4. Da fare prima della produzione

Elenco alimentato dalle revisioni di sicurezza (rapporti in `docs/avanzamento/<fase>-sicurezza.md`).
Le gravità alte e medie sono corrette nella fase; qui restano le basse e le note.

### Dalla revisione di A1

Corrette dopo A1 (con test in `tests/test_correzioni_a1.py`): BASSA-1 (header di identità
duplicati → 401), BASSA-4 (header di sicurezza anche sui 500), BASSA-5 (interi solo con
cifre ASCII), BASSA-6 (timeout dei comandi brevi: segnali a tutto il gruppo di processi),
BASSA-8 (flag di conferma solo da gettone, controllato da `verifica_tabella`), BASSA-9
(corpo JSON massimo 256 KB, contato anche senza `Content-Length`), BASSA-10 (registro: vedi
sotto), NOTA-1 (impronta del gettone tolta, la conferma registra a quale domanda risponde).

- [ ] **Il CMS toglie gli `X-LIL-*` del browser** (A1 BASSA-1): il controller ora rifiuta
  gli header di identità duplicati, quindi un CMS che li lascia passare produce 401.
- [ ] **Replay entro 60 s** (A1 BASSA-2): il CMS non registra `X-LIL-Firma` nei log, TLS fino
  al VPS. Se serve chiudere il caso: `X-LIL-Nonce` nella stringa canonica e cache delle
  firme già viste per i metodi che cambiano qualcosa.
- [ ] **Nome con caratteri bidirezionali o invisibili** (A1 BASSA-3): rifiutare (o togliere)
  le categorie Unicode `Cf` in `X-LIL-Nome`.
- [ ] **Pulizia dei percorsi**: la rete di sicurezza non vede un percorso dentro una frase né
  nelle chiavi dei dizionari (A1 BASSA-7). L'agente garantisce già messaggi senza percorsi
  assoluti; valutare un controllo per sottostringa della cartella dell'agente nelle chiavi.
- [ ] **Registro attività** (A1 BASSA-10): se una riga non si scrive (disco pieno) l'azione
  resta fatta, la risposta porta un `avviso` e la riga completa finisce nel log del servizio
  come `CRITICAL` con "REGISTRO ATTIVITA' NON SCRITTO": **allarme sul log** e riportarla a
  mano nel registro. Monitorare lo spazio su disco. All'avvio file e cartella del registro
  devono essere dell'utente del servizio, 0600 e 0700, altrimenti il controller non parte.
- [ ] Note di A1: `--by` è il nome
  visualizzato, due persone con lo stesso nome si distinguono solo nel registro, che ha
  l'id (NOTA-2); il gettone sta nel percorso e finisce negli access log: tenere gli access
  log riservati o spostarlo nel corpo (NOTA-3); in cms la difesa CSRF dipende anche dalla
  CORS e dai cookie del CMS (`SameSite`) (NOTA-4); validare `DEMO_NOME` e l'esistenza di
  `PERMESSI_FILE` all'avvio (NOTA-5); tetto alla lettura di stdout dei tool (NOTA-6); elenco
  delle opzioni vietate per singolo tool in `verifica_tabella` (NOTA-7); confrontare gli id
  dei candidati di `LessonRemovalRequired` come stringhe (NOTA-8).

### Dalla revisione di A2

Medie corrette nella fase (vedi `docs/avanzamento/A2.md`): coda con il posto prenotato
prima della stima (MEDIA-1), interruzione che arriva anche durante l'avvio del processo e
spegnimento che aspetta il lavoro (MEDIA-2), corpo binario solo sulle rotte di caricamento
(MEDIA-3).

- [ ] **systemd** (da A2, "Da verificare nelle prossime fasi"): con `KillMode=control-group`
  (default) systemd manda il suo SIGTERM a tutto il gruppo, quindi un secondo SIGTERM alla
  produzione, e dopo `TimeoutStopSec` un SIGKILL. Usare `KillMode=mixed` (SIGTERM solo al
  controller, che lo inoltra una volta alla produzione) e `TimeoutStopSec` ≥ 180 s (30 s di
  uvicorn + 120 s di attesa del lavoro + margine). Da provare sul VPS.
- [ ] **Sostituzione e file accanto** (A2 BASSA-1): la sostituzione toglie i file con la stessa
  chiave, compresi `.json` e `.lock`. Per i documenti non conta; **prima delle voci e dei brani
  (A5) va limitata ai file con un'estensione della libreria**, altrimenti cancellerebbe il
  `.json` del consenso.
- [ ] **K7 al momento del rename** (A2 BASSA-2): il blocco "niente sostituzione durante un
  lavoro" si controlla all'inizio del caricamento; ricontrollarlo subito prima del `rename`
  (indispensabile per le voci in A5).
- [ ] **Limite SSE** (A2 BASSA-3): con richieste simultanee si arriva a 5 stream per utente
  invece di 4 (il contatore sale dentro il generatore). Innocuo; si può contare prima.
- [ ] **Nomi lunghi in byte** (A2 BASSA-4): 150 caratteri accentati possono superare i 255 byte
  del filesystem (ext4) → 500 invece di 415. Aggiungere un limite di 200 byte UTF-8.
- [ ] **Timeout di stesura e revisione** (A2 BASSA-5): dopo il SIGTERM l'attesa non ha limite;
  se il comando non si chiudesse la coda resterebbe ferma. Aggiungere un'attesa massima (es.
  2 minuti) e poi SIGKILL al gruppo, **solo** per azioni senza clonazione (mai per `produci`).
- [ ] **Registro all'esito anche per errori del controller e allo spegnimento** (A2 BASSA-6).
- [ ] **Stima fallita prima della produzione** (A2 BASSA-7): oggi si accoda lo stesso e il
  registro non ha il costo. Valutare se rifiutare la produzione (la stima non costa).
- [ ] **Spegnimento e azioni protette** (A2 BASSA-8): allo spegnimento il loop cancella anche
  le azioni brevi protette; per `ritenta_clonazione` (fino a 11 minuti) un SIGTERM può portare
  a `CloneOutcomeUnknown`. Aspettare le azioni protette in corso nel lifespan prima di uscire.
- [ ] Note di A2: con uvicorn reale le letture brevi **non** si fermano quando il browser si
  chiude (NOTA-1; K23 invece vale: nessuna scrittura si ferma), aggiungere un controllo di
  `request.is_disconnected()` alle letture lunghe come `documents_list --name`; `%zz` nel nome
  del file passa la decodifica (finisce rifiutato o salvato con `%` letterale, NOTA-2); il
  gettone di `ConsentRequired` è inutile e quello di `CloneOutcomeUnknown` è di chi ha prodotto
  (approvatore), che di default non ha `voce_clonazione`: in A3 la UI dice di chiedere a chi
  gestisce le voci, oppure si lega il gettone al permesso (NOTA-4); le librerie devono stare
  **dentro** la cartella dell'agente (`config.yaml`), da controllare all'avvio (NOTA-5); I/O
  bloccante nel loop per `fsync` e letture (NOTA-6); azzeramento di `_eventi.jsonl` riconosciuto
  solo se il file nuovo è più corto (NOTA-7); log rumoroso al superamento del limite del
  corpo (NOTA-8).
