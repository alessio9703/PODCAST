# DEPLOY · podcast-web per il dev

> Per il dev che collega il CMS di LIFE e installa il controller sulla VPS. Indice:
> §1 header firmati · §2 ruoli · §3 proxy del CMS · §4 rete e HTTPS · §5 installazione
> sulla VPS · §6 avvio, salute, log · §7 backup · §8 aggiornamenti · §9 da fare prima della
> produzione. Per provare tutto sul Mac prima: `README.md` (CMS finto, `AUTH_MODE=cms`).

```
browser ──HTTPS──► CMS di LIFE (login, firma) ──HTTPS──► nginx sulla VPS ──► 127.0.0.1:8765 podcast-web
                                                         (solo il CMS)         └─► podcast-agent/tools/*.py
```

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

Il percorso da firmare è quello della richiesta **in uscita** dal CMS, così come la costruisce
il suo client HTTP (dopo l'eventuale normalizzazione: codifica di spazi e caratteri non ASCII).
Il corpo non è firmato: lo protegge l'HTTPS fra CMS e VPS.

**Origine dedicata (obbligatorio).** L'interfaccia usa percorsi assoluti (`/`, `/api/…`,
`/static/…`): va inoltrata **alla radice** di un'origine (schema + host + porta) usata **solo**
per LIL Podcast, ad esempio `https://podcast.life.com`, dove il CMS serve soltanto il login e
l'inoltro al controller. Niente prefissi riscritti (`/podcast/api/…` non funziona) e niente
altre pagine del CMS sulla stessa origine: la difesa CSRF del controller non vale fra pagine
della stessa origine, quindi un difetto (XSS) in un'altra pagina lì sopra potrebbe avviare
produzioni o registrare consensi a nome dell'utente. Il controller vieta di incorniciare la
pagina (`frame-ancestors 'none'`, `X-Frame-Options: DENY`): non va mostrata in un iframe.

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
Il segreto è la stringa così com'è, in UTF-8 (se è generato con `openssl rand -hex 32` si
usano i 64 caratteri esadecimali come testo, non i byte che rappresentano).
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
- **Rotte con corpo binario**: `POST /api/documenti` (20 MB), `POST /api/voci` (20 MB),
  `POST /api/musiche/intro|outro|sottofondo` (50 MB).
  Tutte le altre accettano solo `application/json`, al massimo 256 KB.
- **Timeout del proxy** (lettura della risposta): almeno **300 s** per le API (l'estrazione
  di un documento lungo arriva a 240 s; il rimontaggio e la forma d'onda fino a 900 s), **senza limite
  pratico (≥ 1 h) per lo SSE**, che riceve un battito ogni 15 s. I lavori lunghi (stesura,
  produzione) non tengono aperta la richiesta: rispondono subito 202 e si seguono con lo
  SSE o con il polling.
- **Pagine e statici**: anche `GET /` e `/static/…` vanno inoltrati con la firma (il
  controller li protegge come le API). `GET /salute` è l'unica rotta senza firma.
- **Cookie**: i cookie della sessione del CMS non vanno inoltrati al controller (non li usa).
- **CORS e cookie del CMS**: la difesa CSRF del controller (`X-LIL-Richiesta`, solo JSON o
  binario) presuppone che il CMS non permetta richieste da altri siti con la sessione
  dell'utente: cookie di sessione `SameSite=Lax` o `Strict`, nessun
  `Access-Control-Allow-Origin` generico sulle rotte inoltrate.

## 4. Rete e HTTPS

- **La VPS riceve solo dal CMS.** Due modi equivalenti, a scelta:
  - **firewall**: solo l'IP (o gli IP) del CMS può raggiungere la porta 443 della VPS
    (`ufw allow from <IP del CMS> to any port 443 proto tcp`, tutto il resto chiuso, SSH solo
    dalla rete di amministrazione); nginx ripete il controllo (`deploy/nginx-vps.conf`,
    `allow`/`deny`) e, meglio ancora, chiede un certificato client al CMS (mTLS);
  - **tunnel**: WireGuard o SSH fra CMS e VPS, e nginx in ascolto solo sull'indirizzo del
    tunnel (il controller resta comunque su 127.0.0.1, dietro nginx).
- **HTTPS fino alla VPS**: TLS fra CMS e nginx (certificato della VPS, TLS 1.2+). Il corpo
  delle richieste non è firmato: lo protegge questo canale.
- **Il controller ascolta solo su 127.0.0.1** (`HOST=127.0.0.1`, il default): davanti c'è
  nginx. Non esporre mai la porta 8765.
- **Orologi sincronizzati** (NTP) su CMS e VPS: la firma vale 60 s.
- **Log**: nessun log, né sul CMS né su nginx, deve registrare `X-LIL-Firma` (il formato di
  default di nginx non registra gli header). Un gettone di conferma può comparire nel
  percorso (`/api/conferme/<gettone>`) negli access log: tenerli riservati (valgono 5
  minuti e sono legati all'utente).

## 5. Installazione sulla VPS

Esempio con Debian/Ubuntu, utente di servizio `lilpodcast`, codice in `/srv/lil`.

```bash
sudo useradd --system --home /var/lib/podcast-web --shell /usr/sbin/nologin lilpodcast
sudo install -d -o lilpodcast -g lilpodcast -m 0700 /var/lib/podcast-web
sudo install -d -o lilpodcast -g lilpodcast -m 0750 /srv/lil
# codice: podcast-agent e podcast-web dallo stesso repository, al tag concordato
# il repository contiene podcast-agent/ e podcast-web/: si clona IN /srv/lil, al tag concordato
sudo -u lilpodcast git clone --branch <tag> <repository> /srv/lil
#   -> /srv/lil/podcast-agent e /srv/lil/podcast-web (i percorsi dell'unità systemd)
# niente credenziali in /srv/lil/.git/config (usare una deploy key in sola lettura)

# agente: venv, dipendenze, .env con le chiavi (0600), config.yaml di produzione
cd /srv/lil/podcast-agent && sudo -u lilpodcast python3 -m venv .venv \
  && sudo -u lilpodcast .venv/bin/pip install -r requirements.txt
sudo -u lilpodcast install -m 0600 /dev/null .env      # poi FISH_AUDIO_API_KEY=… ANTHROPIC_API_KEY=…
sudo -u lilpodcast env PODCAST_AGENT_WEB=1 .venv/bin/python tools/doctor.py --web

# controller: venv con le versioni fissate
cd /srv/lil/podcast-web && sudo -u lilpodcast python3 -m venv .venv \
  && sudo -u lilpodcast .venv/bin/pip install -r requirements.lock

# configurazione (deploy/.env.example commentato)
sudo install -d -m 0750 -o root -g lilpodcast /etc/podcast-web
sudo install -m 0640 -o root -g lilpodcast deploy/.env.example /etc/podcast-web/podcast-web.env
#   AUTH_MODE=cms, AGENT_DIR=/srv/lil/podcast-agent, AGENT_VERSIONE_ATTESA=1.0-web,
#   LIL_SEGRETO=<openssl rand -hex 32, lo stesso nel CMS>,
#   REGISTRO_FILE=/var/lib/podcast-web/registro/registro_attivita.jsonl

sudo cp deploy/podcast-web.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now podcast-web
sudo cp deploy/nginx-vps.conf /etc/nginx/sites-available/podcast-web   # adattare nome, IP, certificati
sudo ln -s /etc/nginx/sites-available/podcast-web /etc/nginx/sites-enabled/podcast-web
sudo nginx -t && sudo systemctl reload nginx
```

Note:
- Le librerie (`documenti/`, `voci_disponibili/`, `musiche/`) e `output/` devono stare
  **dentro** `podcast-agent/` (come nel `config.yaml` del repository): il controller le cerca
  lì e non parte se mancano o sono link.
- Il registro attività deve stare in una cartella dell'utente di servizio con permessi 0700
  (il controller la crea così se non esiste) e il file 0600; altrimenti non parte.
- L'unità systemd usa `KillMode=mixed` e `TimeoutStopSec=180`: **non cambiarli**. Con il
  default systemd manderebbe un secondo SIGTERM alla produzione in corso (vietato dal
  contratto durante la preparazione delle voci) e la ucciderebbe dopo 90 s.
- `ProtectSystem=strict`: il servizio scrive solo in `/srv/lil/podcast-agent` e
  `/var/lib/podcast-web`.

## 6. Avvio, salute, log

- All'avvio il controller controlla configurazione, `VERSION` dell'agente,
  `doctor.py --web`, cartelle delle librerie e permessi del registro. Se qualcosa non va
  esce con un codice diverso da zero e il motivo nel log: `journalctl -u podcast-web`.
- **`GET /salute`** (senza firma): `200 {"ok": true}` o `503 {"ok": false}`, senza
  dettagli. Ricontrolla `VERSION` ogni 5 minuti. Il CMS può usarla per mostrare "servizio
  non disponibile".
- **Allarmi sul log**:
  - `REGISTRO ATTIVITA' NON SCRITTO` (livello CRITICAL): un'azione è avvenuta ma la riga
    non è nel registro (disco pieno?). La riga completa è nel messaggio: va riportata a mano.
  - `errore dell'adattatore` (ERROR): un comando ha rifiutato gli argomenti del controller
    (`InvalidArguments`, `NotAllowedInWebMode`, `ByRequired`): è un difetto del controller.
  - `cms: richiesta rifiutata` (WARNING): firme non valide; molte di seguito indicano un
    problema di configurazione (segreto, orologi, percorso firmato) o un tentativo d'accesso.

## 7. Backup

Ogni notte (e prima di ogni aggiornamento), con il servizio fermo o con uno snapshot del
disco, così i file scritti insieme restano coerenti:

| Cosa | Perché |
|---|---|
| `podcast-agent/output/` | gli episodi: copioni, versioni, approvazioni, segmenti, mp3 |
| `podcast-agent/documenti/` | i documenti caricati |
| `podcast-agent/voci_disponibili/` **con i `.json`** | le registrazioni e il **consenso** di ogni voce |
| `podcast-agent/musiche/` **con i `.json`** | i brani e le **licenze** dichiarate |
| `podcast-agent/voices_registry.json` | voci clonate e **revoche** del consenso (una revoca persa riabiliterebbe una voce) |
| `podcast-agent/prompts/lezioni.md`, `prompts/_lezioni.json` | le lezioni attive e il loro registro |
| `podcast-agent/.env`, `config.yaml` | chiavi (in un archivio cifrato) e configurazione |
| `REGISTRO_FILE` (es. `/var/lib/podcast-web/registro/`) | il registro attività: chi ha approvato, consentito, revocato, prodotto |
| `/etc/podcast-web/podcast-web.env` | configurazione del controller (contiene `LIL_SEGRETO`: cifrare) |

I file `*.lock` e `.*.tmp` non servono. Il registro attività è append-only: non va mai
riscritto né "pulito"; per ruotarlo si ferma il servizio, si archivia il file e se ne crea
uno nuovo (0600).

**Il registro attività va copiato anche fuori dal server, spesso** (es. ogni ora, su un
archivio in sola aggiunta o con syslog remoto): è la prova di chi ha revocato un consenso, e
serve proprio quando il disco del server va ripristinato.

### Ripristino

Un backup è una fotografia: se dopo il backup una persona ha **revocato** il consenso alla
propria voce, il ripristino lo riporterebbe a "registrato". Vince sempre la revoca, quindi:

1. servizio fermo (`systemctl stop podcast-web`);
2. ripristinare i file della tabella sopra;
3. ricavare le revoche avvenute dopo il backup dalla copia **più recente** del registro
   attività (quella tenuta fuori dal server):
   ```bash
   cd /srv/lil/podcast-web
   .venv/bin/python -m strumenti.revoche_dal_registro /percorso/copia/registro_attivita.jsonl \
     --dopo 2026-10-09T02:00:00+00:00          # istante del backup ripristinato
   ```
   lo script stampa un comando `voice_meta.py --revoke-consent` per ogni revoca: eseguirli
   dalla cartella di `podcast-agent` come utente di servizio (`sudo -u lilpodcast …`);
4. i consensi registrati dopo il backup sono persi: vanno chiesti e confermati di nuovo
   (passo 5 o libreria Voci), così come approvazioni e produzioni successive;
5. riavviare (`systemctl start podcast-web`) e controllare `/salute`.

## 8. Aggiornamenti

- **Agente**: dopo un aggiornamento di `podcast-agent`, `VERSION` cambia; si aggiorna
  `AGENT_VERSIONE_ATTESA` solo dopo aver verificato che il controller la supporti (contratto
  d'interfaccia). Con una versione diversa il controller non parte, e `/salute` diventa 503
  entro 5 minuti se l'agente viene cambiato a controller acceso.
- **Controller**: `pip install -r requirements.lock` (versioni fissate), poi
  `systemctl restart podcast-web`. Un riavvio interrompe il lavoro in corso con un SIGTERM
  (si riprende dall'Archivio) e svuota la coda dei lavori in attesa.
- **Segreto**: per cambiarlo senza interruzioni si mette il nuovo in `LIL_SEGRETO` e il
  vecchio in `LIL_SEGRETO_PRECEDENTE`, si riavvia, si aggiorna il CMS, poi si toglie il
  vecchio e **si riavvia di nuovo** (finché non si riavvia, il vecchio resta valido).

## 9. Da fare prima della produzione

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

- [ ] **systemd: provare lo spegnimento sulla VPS** (da A2): l'unità `deploy/podcast-web.service`
  usa già `KillMode=mixed` e `TimeoutStopSec=180` (§5). Prova da fare: lanciare una produzione
  con i servizi finti, `systemctl stop podcast-web` durante la sintesi, controllare che arrivi
  un solo SIGTERM (esito `ProductionInterrupted`) e che si riprenda dall'Archivio.
- [ ] **Limite SSE** (A2 BASSA-3): con richieste simultanee si arriva a 5 stream per utente
  invece di 4 (il contatore sale dentro il generatore). Innocuo; si può contare prima.
- [ ] Note di A2: con uvicorn reale le letture brevi **non** si fermano quando il browser si
  chiude (NOTA-1; K23 invece vale: nessuna scrittura si ferma), aggiungere un controllo di
  `request.is_disconnected()` alle letture lunghe come `documents_list --name`; `%zz` nel nome
  del file passa la decodifica (finisce rifiutato o salvato con `%` letterale, NOTA-2); il
  gettone di `ConsentRequired` è inutile (NOTA-4, resta: formato uniforme); I/O
  bloccante nel loop per `fsync` e letture (NOTA-6); azzeramento di `_eventi.jsonl` riconosciuto
  solo se il file nuovo è più corto (NOTA-7); log rumoroso al superamento del limite del
  corpo (NOTA-8).

### Dalla revisione di A4

Medie corrette nella fase (vedi `docs/avanzamento/A4.md`): produzione legata all'impronta della
stima vista (MEDIA-1), origine dedicata e interfaccia non incorniciabile (MEDIA-2), procedura
di ripristino con le revoche ricavate dal registro (MEDIA-3). Corrette anche le incoerenze del
documento segnalate in NOTA-2 e NOTA-5 (segreto come testo, rotazione con doppio riavvio,
tunnel, percorso del clone, `REGISTRO_FILE`, abilitazione del sito nginx).

- [ ] **Il CMS verifica il certificato della VPS** (A4 BASSA-1): TLS con verifica del nome e
  della catena (o certificato fissato), mai `verify=false`: il corpo delle richieste non è
  firmato.
- [ ] **Codice in sola lettura per il servizio** (A4 BASSA-2): codice di proprietà di root (o
  di un utente di deploy); l'utente di servizio scrive solo `output/`, `documenti/`,
  `voci_disponibili/`, `musiche/`, `voices_registry.json`, `prompts/lezioni.md`,
  `prompts/_lezioni.json` e i loro lock; restringere `ReadWritePaths` di conseguenza.
- [ ] **Limiti di risorse nell'unità systemd** (A4 BASSA-3): `MemoryMax`, `TasksMax`. Un crash
  del controller fa arrivare il SIGKILL di systemd anche alla produzione in corso (si riprende
  dall'Archivio; una clonazione in corso diventa `CloneOutcomeUnknown`).
- [ ] **nginx: 60 MB solo dove serve** (A4 BASSA-4): `client_max_body_size 1m` di base, 60m
  solo sulle rotte di caricamento.
- [ ] **Mai `AUTH_MODE=demo` sulla VPS** (A4 BASSA-5): dietro nginx ogni utente del CMS
  diventerebbe l'utente demo con tutti i ruoli. Controllare `/etc/podcast-web/podcast-web.env`
  a ogni installazione; valutare un rifiuto all'avvio di `AUTH_MODE=demo` fuori dallo sviluppo.
- [ ] **CMS finto e DNS rebinding** (A4 BASSA-6): strumento solo locale, ma non controlla
  `Host` e fa il login con un GET; aggiungere lo stesso controllo dell'ingresso demo se lo si
  usa a lungo.
- [ ] Note di A4: il CMS non deve comprimere `text/event-stream` (NOTA-3); il percorso
  `/api/conferme/<gettone>` finisce negli access log (log riservati, NOTA-4); nell'interfaccia
  `decodeURIComponent` dell'hash fuori dal `try` e `seg()` che accetta `.`/`..` (solo
  disservizio, NOTA-6); il consenso si registra con un clic sulla casella, senza un secondo
  passaggio (NOTA-7: valutare una conferma); la prima stesura del copione parte con un clic
  accanto all'avviso di costo, senza dialogo (NOTA-8); `amministratore: ["*"]` riceve anche i
  permessi aggiunti in futuro (NOTA-9); il CMS finto firma il percorso del browser e non quello
  normalizzato dal suo client (fallisce in modo sicuro, NOTA-1).

### Dalla revisione di A5

Rapporto: `docs/avanzamento/A5-sicurezza.md` (0 alte, 1 media corretta: consenso su una
registrazione revocata solo con una conferma esplicita, `ConsensoDopoRevoca`).

- [ ] **Proposte di lezioni senza controllo dei doppioni in coda** (BASSA-1): due conferme
  ravvicinate fanno due chiamate al modello. Un solo lavoro `lezioni_proponi` alla volta.
- [ ] **Registro più ricco** (BASSA-2): per accettazione e rifiuto delle lezioni scrivere
  id, testo accettato, motivo e `remove_id`; per la scelta del take battuta e take; per i
  metadati dei brani categoria e campi cambiati.
- [ ] **Brano sostituito** (BASSA-3): licenza, parlato e trascrizione del brano vecchio
  restano; chiedere conferma di licenza e parlato dopo una sostituzione.
- [ ] **Un processo dell'agente per ogni anteprima** (BASSA-4): ogni richiesta (anche
  `Range`) lancia `retake.py --list`; limitare i processi brevi contemporanei o tenere
  l'elenco delle anteprime per qualche secondo.
- [ ] **nginx a 900 s** (BASSA-5) per rimontaggio, forma d'onda e anteprime con musica
  (`deploy/nginx-vps.conf` è ancora a 300 s per le API).
- [ ] **Prezzi del modello delle lezioni** in `config.yaml`
  (`lessons.input/output_cost_per_million_tokens_usd`, NOTA-2): senza, la proposta parte con
  «costo non noto» e la riga del registro resta senza costo.
- [ ] Note da valutare: impronta del retake con `run` e versione approvata (NOTA-1);
  doppioni anche fra estensioni senza firma nota (NOTA-3); rifiutare i nomi di voce di sole
  cifre (NOTA-4); permesso dedicato e registro per scaricare i campioni delle voci (NOTA-5);
  percorsi con spazi nella diagnostica (NOTA-6); la richiesta di revisione si vede nel
  confronto da tutti gli utenti (NOTA-7, decisione da scrivere).
- [ ] **Cancellare voci, brani e documenti** non si fa dall'interfaccia (non c'è un comando
  dell'agente): per una voce prima si revoca il consenso dalla libreria, poi si tolgono a
  mano registrazione e `.json` (contratto, "Caricamenti").
