# Revisione di sicurezza · A4

Intervallo esaminato: modifiche non committate su `3115981` (`DEPLOY.md`, `README.md`,
`strumenti/cms_finto.py`, `tests/test_cms_finto.py`, `deploy/podcast-web.service`,
`deploy/nginx-vps.conf`) + interfaccia di A3 mai revisionata (`23687f2..3115981 --
static/ app/main.py strumenti/demo.py`).
File esaminati: 27 (6 di A4, 17 di `static/`, `app/main.py`, `strumenti/demo.py`, più per
contesto `app/ingresso/cms.py`, `app/ingresso/demo.py`, `app/api/lavori.py`,
`app/api/librerie.py`, `app/lavori/coda.py`, `app/file/download.py`, `deploy/.env.example`).
Data: 2026-10-09.

Verifiche eseguite: test (`pytest`, tutti verdi); firma d'esempio di DEPLOY §1 ricalcolata in
modo indipendente (coincide: `v1=80be18eb…bfd520`); controller in `AUTH_MODE=cms` + CMS finto
avviati su 127.0.0.1 con `dati/demo-agente` (poi fermati): richiesta diretta → 401; header
`X-LIL-*` falsi dal browser tolti; `/static/../app/config.py` e `//evil.example/x` → 401;
POST senza `X-LIL-Richiesta` → rifiutato; SSE attraverso il CMS finto in streaming con
`X-Accel-Buffering: no`; header `Host` estraneo accettato dal CMS finto (vedi BASSA-6).

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 3 |
| Bassa | 6 |
| Nota | 9 |

L'interfaccia è solida contro l'XSS: tutto il DOM passa da `static/js/dom.js` (`textContent`,
`setAttribute`, niente markup in stringa), la CSP è `'self'` senza `unsafe-inline`, nessun URL
costruito con dati dell'agente esce dall'origine (`seg()` = `encodeURIComponent`, link solo
`#/…` o `/api/…`), e ogni gettone parte solo dopo una domanda all'utente. Il CMS finto segue
DEPLOY.md. I problemi medi sono: la produzione non è legata alla stima che l'utente ha visto,
DEPLOY non dice sotto quale origine servire l'interfaccia (e l'esempio con il prefisso non
funziona con i percorsi assoluti dell'UI), e un ripristino da backup può far rivivere un
consenso revocato.

## Problemi

### [MEDIA-1] La produzione non è legata alla stima mostrata all'utente
- **Dove**: `static/js/schermate/stima.js:132-143` (`genera`: dialogo con `costo(stima)`, poi
  `POST /api/episodi/<run>/produzione` con corpo `{}`); `app/api/lavori.py:62-66`;
  `app/lavori/coda.py:161-181` (la stima rifatta dal server finisce solo nel registro).
- **Voce della checklist**: 16. interfaccia (costi visibili prima di spendere) / 8. garanzie di dominio.
- **Scenario**: l'approvatore A apre il passo 5: copione v3 approvato, 6 battute da generare,
  0,40 €. Lascia la pagina aperta. Nel frattempo l'autore riscrive il copione (v4, 80 battute) e
  l'approvatore B lo approva. A torna, clicca "Genera l'episodio · 0,40 €" e conferma il
  dialogo con lo stesso importo: il server rifà la stima, la scrive nel registro (es. 5,20 € +
  clonazione di una voce nuova) e lancia la produzione della v4. A ha confermato una spesa che
  non è quella sostenuta, e una voce nuova viene clonata senza che il suo nome comparisse nella
  stima vista. Stesso effetto con una pagina rimasta aperta dopo un "Riprendi" o dopo un cambio
  di cast/musiche.
- **Proposta di correzione**: la UI manda in `POST …/produzione` ciò che ha mostrato, es.
  `{"versione_approvata": stima.approved_version, "battute": stima.turns_to_generate,
  "costo_usd": stima.cost_usd, "voci_da_clonare": [...]}` (o un'impronta sha256 della stima
  normalizzata calcolata dal server e restituita con `GET …/stima`). In `coda.py`, dopo la
  stima del server, se versione/battute/voci da clonare differiscono o il costo supera quello
  mostrato, rispondere 409 con un errore del controller (es. `StimaCambiata`) e i dati nuovi; la
  UI ridisegna la stima e richiede la conferma. Test: stima cambiata fra GET e POST → 409, nessun
  lavoro accodato.

### [MEDIA-2] DEPLOY non fissa l'origine dell'interfaccia; l'esempio con il prefisso non è compatibile con l'UI
- **Dove**: `DEPLOY.md:41-42` (esempio `/podcast/api/io` → `/api/io`), `DEPLOY.md:120-126`;
  `static/index.html:7-9` (`/static/...` assoluti), `static/js/api.js:82,110,139`,
  `static/js/schermate/*.js` (`/api/...` assoluti); `app/main.py:55` (`frame-ancestors 'self'`).
- **Voce della checklist**: 9. firma e ingresso (istruzioni che, seguite alla lettera, aprono un buco) / 16. CSP.
- **Scenario**: il dev segue l'esempio di §1 e monta il controller su
  `https://cms.life.it/podcast/`. L'UI chiama `fetch("/api/io")` e carica `/static/js/app.js`
  alla radice del CMS: non funziona finché il dev non inoltra al controller anche `/api/*` e
  `/static/*` della radice del CMS (rotte che un CMS spesso usa già: collisioni) oppure riscrive
  l'UI. In ogni caso l'interfaccia vive sulla **stessa origine del CMS**: un XSS in qualunque
  pagina del CMS (o un file `.js`/`.html` caricato dagli utenti del CMS e servito dalla sua
  origine, che per la CSP del controller è `'self'`) può chiamare `POST /api/episodi/<run>/produzione`,
  `/api/voci/<nome>/consenso` ecc. con `X-LIL-Richiesta: 1` e la sessione dell'utente: la
  difesa CSRF non vale fra pagine della stessa origine. `frame-ancestors 'self'` consente a ogni
  pagina del CMS di incorniciare l'interfaccia (clickjacking del pulsante "Genera").
- **Proposta di correzione**: in DEPLOY §3 una regola esplicita: l'interfaccia va servita da
  un'**origine dedicata** (es. `https://podcast.cms.life.it/`, inoltrata dal CMS alla radice del
  controller, cookie di sessione host-only o comunque non leggibile da altre app), mai sotto un
  percorso dell'origine principale del CMS; togliere o correggere l'esempio con il prefisso
  (oppure rendere relativi tutti i percorsi dell'UI e documentare il prefisso). Portare la CSP a
  `frame-ancestors 'none'` se l'UI non va incorniciata.

### [MEDIA-3] Un ripristino da backup può riabilitare una voce il cui consenso è stato revocato
- **Dove**: `DEPLOY.md:209-228` (§7 Backup: c'è cosa salvare, manca come ripristinare).
- **Voce della checklist**: 15. backup / 8. garanzie di dominio (consenso).
- **Scenario**: backup di lunedì notte. Martedì il gestore voci revoca il consenso di "Mario
  Rossi" (scritto in `voci_disponibili/*.json`, `voices_registry.json` e nel registro attività)
  e cancella la registrazione. Mercoledì il disco si guasta: si ripristina il backup di lunedì,
  dove il consenso è ancora valido e la registrazione c'è. La produzione successiva usa (o
  clona) la voce di una persona che ha revocato il consenso. Lo stesso per un documento o una
  registrazione cancellati su richiesta. Il registro attività, che saprebbe della revoca, sta
  sullo stesso disco e viene ripristinato alla stessa data.
- **Proposta di correzione**: aggiungere in §7 una procedura di **ripristino**: (1) controller
  fermo; (2) recuperare la copia più recente del registro attività (spedirlo fuori dalla VPS in
  modo continuo, es. le righe di revoca/consenso anche nel journal inoltrato a un syslog remoto,
  o un backup orario solo del registro); (3) riapplicare con `voice_meta.py --revoke-consent`
  ogni revoca e ripetere ogni cancellazione successive alla data del backup; (4) solo dopo
  riavviare. Inoltre: backup **interamente cifrati** e ad accesso ristretto (le registrazioni
  sono dati biometrici di persone reali, non solo `.env`), conservazione limitata, e nel backup
  anche `PERMESSI_FILE` se personalizzato, l'unità systemd e la configurazione di nginx.

### [BASSA-1] DEPLOY non chiede al CMS di verificare il certificato della VPS
- **Dove**: `DEPLOY.md:137-138` (§4), `DEPLOY.md:42`; `deploy/nginx-vps.conf:8` (nome interno).
- **Voce della checklist**: 9. firma e ingresso / 14. nginx (TLS).
- **Scenario**: il corpo non è firmato e "lo protegge l'HTTPS". Con un nome interno
  (`podcast-vps.life.example`) è frequente un certificato autofirmato e un client del CMS con
  `verify=False`. Chi sta in mezzo fra CMS e VPS modifica il corpo di una richiesta firmata (es.
  istruzioni di revisione, `audio_sha256` del consenso) o la ripete entro 60 s.
- **Proposta di correzione**: in §4 scrivere che il CMS **deve** verificare il certificato
  della VPS (CA interna o pinning della chiave), con il nome host atteso; mai disattivare la
  verifica. Consigliare mTLS (già accennato) come default, non come opzione.

### [BASSA-2] Il servizio possiede e può scrivere il proprio codice
- **Dove**: `DEPLOY.md:154-157,160-162` (`/srv/lil` e il clone di proprietà di `lilpodcast`);
  `deploy/podcast-web.service:34` (`ReadWritePaths=/srv/lil/podcast-agent`).
- **Voce della checklist**: 14. systemd (isolamento, permessi di file e cartelle).
- **Scenario**: un difetto futuro che permetta di scrivere un file sotto `AGENT_DIR` (es. un
  nome di caricamento non controllato in A5) o un tool compromesso può riscrivere
  `podcast-agent/tools/*.py`, `src/`, `.venv/` o `podcast-web/app/`: al lancio successivo è
  esecuzione di codice persistente, con accesso a `.env` (chiavi) e `LIL_SEGRETO`.
- **Proposta di correzione**: codice e venv di proprietà di `root` (0755/0644), solo i dati
  dell'utente di servizio; nell'unità aggiungere `ReadOnlyPaths=/srv/lil/podcast-agent/tools
  /srv/lil/podcast-agent/src /srv/lil/podcast-agent/.venv /srv/lil/podcast-agent/config.yaml
  /srv/lil/podcast-agent/.env /srv/lil/podcast-web` (più specifici di `ReadWritePaths`, quindi
  prevalgono) e lasciare scrivibili solo `output/`, `documenti/`, `voci_disponibili/`,
  `musiche/`, `prompts/` e la radice dell'agente per i temporanei di `voices_registry.json`.

### [BASSA-3] systemd senza limiti di risorse; un crash del controller uccide la produzione con SIGKILL
- **Dove**: `deploy/podcast-web.service:22-43`.
- **Voce della checklist**: 14. systemd.
- **Scenario**: (a) un documento enorme o un ffmpeg che sfugge consuma tutta la memoria della
  VPS (anche nginx e SSH). (b) Se il controller muore (eccezione fatale, OOM) mentre una
  produzione è in "preparazione voci", con `KillMode=mixed` systemd manda poi SIGKILL al resto
  del cgroup: la clonazione si interrompe a metà (esito incerto, gestito dall'agente con
  `CloneOutcomeUnknown`, ma è proprio il caso che il contratto vuole evitare).
- **Proposta di correzione**: aggiungere `MemoryMax=` (es. 2G), `TasksMax=256`,
  `PrivateDevices=true`, `ProtectProc=invisible`, `ProtectClock=true`, `ProtectHostname=true`,
  `RestrictNamespaces=true`, `SystemCallFilter=@system-service`. In DEPLOY §5/§6 documentare che
  dopo un riavvio non pulito si controllano le clonazioni incerte (passo 5) prima di produrre.

### [BASSA-4] nginx: limite del corpo a 60 MB su tutte le rotte e bufferizzazione dei caricamenti futuri
- **Dove**: `deploy/nginx-vps.conf:23` (`client_max_body_size 60m` a livello di server),
  `:25-31`, `:34-40`.
- **Voce della checklist**: 12. upload e download attraverso il proxy.
- **Scenario**: le rotte JSON accettano al massimo 256 KB, ma nginx bufferizza su disco fino a
  60 MB per richiesta prima di passarla al controller (`proxy_request_buffering` è attivo in
  `location /`). Raggiungibile solo dal CMS, ma un utente autenticato può inviare molte
  richieste da 60 MB a `PATCH /api/episodi/x/configurazione` e riempire `client_body_temp`. In A5
  i caricamenti di voci e brani passeranno da `location /` con buffering attivo (il controller
  non conta più i byte "mentre arrivano").
- **Proposta di correzione**: `client_max_body_size 1m;` in `location /`, `60m` (o 20m) solo
  nelle location di caricamento; una location regex per tutte le rotte binarie
  (`^/api/(documenti|voci|musiche)$` in A5) con `proxy_request_buffering off`. Aggiungere
  `server_tokens off;`.

### [BASSA-5] `AUTH_MODE=demo` dietro nginx accetterebbe ogni utente del CMS
- **Dove**: `app/ingresso/demo.py:34-46`, `app/config.py:82`; `deploy/nginx-vps.conf:25-30`
  (nessun `proxy_set_header Host`, quindi nginx manda `Host: 127.0.0.1:8765`).
- **Voce della checklist**: 9. firma e ingresso / 11. permessi.
- **Scenario**: un errore di configurazione (`AUTH_MODE=demo` lasciato in
  `/etc/podcast-web/podcast-web.env`, per esempio copiato dal README): le richieste arrivano da
  127.0.0.1 con `Host: 127.0.0.1:8765`, quindi la difesa anti-rebinding di demo le accetta. Ogni
  utente del CMS diventa l'utente demo con **tutti** i ruoli: legge tutto; le scritture passano
  se il CMS non inoltra `Origin` (oggi DEPLOY non dice niente su `Origin`).
- **Proposta di correzione**: in `nginx-vps.conf` `proxy_set_header Host $host;` (con il nome
  della VPS demo rifiuta); nel controller rifiutare `AUTH_MODE=demo` se c'è `INVOCATION_ID`
  (avvio da systemd) oppure richiedere una variabile esplicita; nel `.env.example` un commento
  "demo: mai sulla VPS".

### [BASSA-6] CMS finto: nessun controllo di `Host` (DNS rebinding) e login con un GET
- **Dove**: `strumenti/cms_finto.py:101-107` (`/__cms/entra?profilo=` in GET), `:114-125`
  (nessun controllo dell'header `Host`).
- **Voce della checklist**: 9. firma e ingresso.
- **Scenario** (verificato in locale): `curl -H "Host: evil.example:8780"
  "http://127.0.0.1:8780/__cms/entra?profilo=tutti"` imposta il cookie e `GET /api/io` con lo
  stesso Host risponde con l'utente "Elena Redazione" e i permessi `produrre`,
  `voce_consenso`, `voce_clonazione`. Una pagina web visitata dal dev mentre il CMS finto è
  acceso, con un dominio che si risolve a 127.0.0.1 (DNS rebinding), è same-origin con il CMS
  finto: si "logga" come `tutti`, registra consensi e lancia produzioni. Se il controller dietro
  il CMS finto usa l'agente vero (README "servizi veri"), è spesa reale. Il controller in
  `demo` ha questa difesa, il CMS finto no.
- **Proposta di correzione**: nel CMS finto accettare solo `Host` in
  `{127.0.0.1:<porta>, localhost:<porta>}` (altrimenti 400), anche per `/__cms/`; login con un
  form `POST` e controllo di `Origin`; nel README dire di usarlo solo con `dati/demo-agente`.

## Note

- **NOTA-1** (9, DEPLOY §1): "percorso esattamente come arriva al controller" — aggiungere che
  il CMS deve firmare il percorso **della richiesta in uscita, dopo** che il suo client HTTP l'ha
  costruita: le librerie normalizzano (httpx toglie i `..`, codifica spazi e non-ASCII, e
  `//host/x` diventa `/x`). Il CMS finto firma il percorso del browser
  (`strumenti/cms_finto.py:119-125`) e manda quello normalizzato da httpx: oggi fallisce in modo
  sicuro (401), ma conviene firmare `richiesta.url.raw_path`. Nella conf di nginx un commento:
  `proxy_pass` **senza** URI (con `/` finale nginx inoltra il percorso normalizzato).
- **NOTA-2** (9, DEPLOY §1/§8): il segreto è la stringa ASCII così com'è (non va decodificata
  da esadecimale); nella rotazione, dopo aver tolto `LIL_SEGRETO_PRECEDENTE` serve un riavvio.
- **NOTA-3** (13, SSE): DEPLOY §3 non dice che il CMS non deve comprimere `text/event-stream`
  (la compressione trattiene i dati; l'UI ripiega sul polling dopo 8 s, quindi non si rompe).
  `proxy_read_timeout 300s` in `location /` non basterà al rimontaggio di A5 (fino a 900 s).
- **NOTA-4** (14, nginx/log): `X-LIL-Gettone` (header) e `/api/conferme/<gettone>` (percorso,
  anche nell'access log di uvicorn nel journal): oltre a `X-LIL-Firma`, anche i gettoni vanno
  tenuti fuori dai log del CMS (già NOTA-3 di A1).
- **NOTA-5** (DEPLOY §4/§5, coerenza): §4 propone "il controller in ascolto sull'indirizzo del
  tunnel" ma subito dopo "solo su 127.0.0.1, non esporre mai 8765"; §5 clona in `/srv/lil/repo`
  ma poi usa `/srv/lil/podcast-agent` (con il clone `ReadWritePaths` punterebbe a una cartella
  inesistente e l'unità non parte); `REGISTRO_FILE` diverso fra DEPLOY (`…/registro/…`) e
  `.env.example`; manca l'abilitazione del sito nginx (`ln -s`, `nginx -t`, reload); la nota
  sul clone del repository dovrebbe dire di non lasciare credenziali in `.git/config`.
- **NOTA-6** (16, UI): `static/js/app.js:37` chiama `decodeURIComponent(location.hash)` fuori
  dal `try`: un link `#/episodi/%E0` lascia la pagina su "Caricamento…" (solo disservizio).
  Un run `..` nel hash (`#/episodi/../5`) produce `fetch("/api/episodi/../stima")`, che il
  browser normalizza in `/api/stima` (404, innocuo); `seg()` potrebbe rifiutare `.` e `..`.
- **NOTA-7** (16, UI): il consenso si registra con un solo clic sulla casella
  (`static/js/schermate/stima.js:77-92`), senza dialogo; la dichiarazione è nell'etichetta ed è
  legata a sha256 e revisione mostrati, quindi va bene, ma un dialogo di conferma ridurrebbe i
  clic accidentali su un atto con valore legale.
- **NOTA-8** (16, costi): la prima stesura del copione parte con un clic
  (`static/js/schermate/copione.js:72-75`) con l'avviso di costo accanto al pulsante ma senza
  importo (l'agente non lo stima) e senza dialogo; la revisione invece chiede conferma.
  Uniformare con un dialogo.
- **NOTA-9** (11, permessi): `amministratore: ["*"]` riceve in automatico ogni permesso
  aggiunto in futuro (es. quelli di A5); il profilo "tutti" del CMS finto mostra che una sola
  persona può scrivere, approvare, consentire e produrre: è una scelta organizzativa, ma DEPLOY §2
  potrebbe suggerire di non assegnare insieme `approvatore` e `gestore_voci`. Il CMS finto
  duplica l'header `Date` nelle risposte (innocuo).

## Verificato e a posto

- **9. Firma e ingresso**: il CMS finto toglie ogni `X-LIL-*` del browser tranne
  `Richiesta`/`Nome-File`/`Gettone` (case-insensitive), non inoltra `Cookie` né `Set-Cookie`,
  firma con il codice di DEPLOY (indipendente dal controller), `Host` riscritto verso il
  controller; verificato dal vivo. Il controller usa `raw_path` + query, confronto a tempo
  costante, `proxy_headers=False`; firma d'esempio di DEPLOY ricalcolata e corretta. DEPLOY
  descrive header, stringa canonica, orologi, 401 senza dettagli, rotazione, header da
  togliere/inoltrare, cookie e CORS (`SameSite`, niente ACAO generico).
- **10. Gettoni nell'interfaccia**: ogni gettone (`FileEsistente`, `SpokenNameIncomplete`,
  `MusicChanged`) parte solo dopo `domanda()` con "Annulla" come alternativa
  (`stati.js:190-202`, `documento.js:81-90`, `cast.js:46`, `generazione.js:142-147`); il gettone
  di `ConsentRequired` è ignorato; `clonazione-verificata` ha un dialogo e il server accoda solo
  se l'agente segnala davvero una clonazione incerta.
- **11. Permessi**: DEPLOY §2 coincide con `permessi_default.json`; un utente senza ruoli noti
  non ha `leggere`; i pulsanti nascosti dalla UI sono solo estetica, il controllo è sul server.
- **12. Upload/download**: corpo grezzo in streaming nel CMS finto e in nginx per
  `/api/documenti`; `Range` e `Last-Event-ID` inoltrati; download con `nosniff`, tipi da
  elenco chiuso, `Content-Disposition` con nome ripulito.
- **13. SSE**: `X-Accel-Buffering: no`, `proxy_buffering off`, `proxy_read_timeout 3600s`,
  battito ogni 15 s, durata massima 2 h; ripiego automatico sul polling; verificato attraverso
  il CMS finto.
- **14. systemd/nginx**: `KillMode=mixed`, `TimeoutStopSec=180` > 120+30 s del controller,
  `EnvironmentFile` 0640 root:lilpodcast, `HOME` in `/var/lib/podcast-web`, `UMask=0077`,
  `NoNewPrivileges`, `ProtectSystem=strict`, `CapabilityBoundingSet=` vuoto; il segreto non
  arriva ai sottoprocessi (ambiente minimo in `app/agente/esecutore.py`). nginx: TLS 1.2+,
  `allow`/`deny`, formato di log di default (nessun header).
- **15. Backup**: l'elenco copre output, documenti, voci con consenso, musiche con licenze,
  registro delle voci con le revoche, lezioni, registro attività, chiavi e `LIL_SEGRETO`
  (cifrati), con servizio fermo o snapshot. Manca solo il ripristino (MEDIA-3).
- **16. Interfaccia**: nessun `innerHTML`/`eval`/`javascript:` (anche controllato da
  `tests/test_interfaccia.py`), `h()` vieta `style`, gestori solo come funzioni; CSP `'self'`
  senza inline, `object-src 'none'`, `base-uri 'none'`; nessun `window.open` né URL esterni;
  link costruiti con `seg()`; costo e voci da clonare mostrati nella pagina e ripetuti nel
  dialogo prima di "Genera" (salvo MEDIA-1); approvazione legata alla versione letta.
- **1-8 (agente)**: `strumenti/demo.py` copia solo `src`, `tools`, `prompts` e i finti, senza
  `.env`, con provider e client Anthropic finti; nessun nuovo sottoprocesso o percorso da input
  in A4.

## Da verificare nelle prossime fasi

- A5: rotte binarie di voci e brani in nginx (`proxy_request_buffering off`, limiti per rotta)
  e in `CARICAMENTI`; timeout del proxy per il rimontaggio (900 s).
- A5: whitelist dei tag dei retake; costo del retake mostrato e legato come in MEDIA-1.
- Sulla VPS: prova di `systemctl stop` durante la sintesi (già in DEPLOY §9) e di un crash del
  controller durante la preparazione delle voci (BASSA-3).
- Con il CMS vero: firma del percorso dopo la normalizzazione del client (NOTA-1), verifica del
  certificato (BASSA-1), origine dedicata (MEDIA-2).
