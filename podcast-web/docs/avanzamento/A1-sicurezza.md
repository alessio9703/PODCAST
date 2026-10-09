# Revisione di sicurezza · A1

Intervallo esaminato: commit `2104260` (solo `podcast-agent/VERSION`) + tutto `podcast-web/`
non ancora committato (`app/**`, `tests/**`, `DEPLOY.md`, `CLAUDE.md`, `deploy/.env.example`,
`docs/avanzamento/A0.md`; esclusi `.venv/` e `.pytest_cache/`).
File esaminati: 41 (+ `podcast-agent/docs/CONTRATTO_INTERFACCIA.md` §0–§4 e "A carico
dell'adattatore", `podcast-agent/tools/_common.py` per il controllo della modalità web).
Data: 2026-10-09. Test: `pytest` verde (124 test). Le ipotesi segnate "verificato" sono state
provate con uno script fuori dal progetto (`/private/tmp/claude-501/revisione-a1/verifica.py`)
contro l'app ASGI con l'agente finto dei test.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 1 |
| Bassa | 10 |
| Nota | 8 |

Le fondamenta sono solide: la firma HMAC è verificata a tempo costante sul percorso grezzo, con
metodo, query, utente, ruoli e nome; nessuna rotta sfugge al middleware (a parte `/salute`,
voluta); `--by` e i flag di conferma non possono arrivare dal browser; i valori liberi vanno
sempre in `--opzione=valore` e i posizionali dopo `--`; il segreto non arriva ai
sottoprocessi. L'unico problema medio riguarda la modalità demo: senza un controllo
dell'header `Host`, una pagina web qualsiasi può pilotare il controller locale con un
attacco di DNS rebinding. Le basse sono difesa in profondità e robustezza.

## Problemi

### [MEDIA-1] Modalità demo esposta al DNS rebinding: nessun controllo di `Host`/`Origin`
- **Dove**: `app/ingresso/demo.py:27-32`; `app/main.py:87-104` (nessun `TrustedHostMiddleware`
  né controllo di `Origin`).
- **Voce della checklist**: 9. Firma e ingresso (modalità demo fuori da loopback).
- **Scenario**: l'utente avvia il controller in demo (`127.0.0.1:8765`, utente fisso con
  **tutti** i ruoli) e, con il controller acceso, visita una pagina malevola
  `http://evil.example:8765`. Il dominio dell'attaccante, con un TTL breve, viene poi risolto a
  `127.0.0.1`. Per il browser le richieste restano *same-origin* con `evil.example`: la pagina
  può aggiungere `X-LIL-Richiesta: 1` e `Content-Type: application/json` e leggere le
  risposte. Il controller vede `client.host = 127.0.0.1` e accetta. Verificato:
  `GET /api/io` con `Host: evil.example:8765` e `Origin: http://evil.example:8765` → 200 con
  ruoli `amministratore, approvatore, autore, gestore_voci`; un `POST` con l'header CSRF →
  200. In A1 la superficie è piccola (`/api/io`, `/api/conferme`); da A2 la stessa pagina può
  approvare copioni, lanciare stesure e produzioni (che **costano**), revocare consensi,
  caricare file.
  È media e non alta perché vale solo in modalità demo, sulla macchina di chi la usa, e
  richiede che l'utente visiti la pagina dell'attaccante mentre il controller è acceso.
- **Proposta di correzione**: in `IngressoDemo.identifica` (o nel middleware, prima
  dell'ingresso) accettare solo un `Host` fra `127.0.0.1:<PORT>`, `localhost:<PORT>`,
  `[::1]:<PORT>` (`config.port`), altrimenti `NonAutenticato`. Per le richieste che cambiano
  qualcosa, se c'è `Origin`, rifiutare quando non è `http://127.0.0.1:<PORT>` o
  `http://localhost:<PORT>`. Aggiungere un test con `Host: evil.example` → 401. In modalità
  cms si può fare lo stesso, con un elenco `HOST_AMMESSI` facoltativo (difesa in profondità).

### [BASSA-1] Header di identità duplicati non rifiutati
- **Dove**: `app/ingresso/cms.py:101-106` (`request.headers.get` prende il primo valore).
- **Voce della checklist**: 9. Firma e ingresso.
- **Scenario**: se il CMS non toglie gli `X-LIL-*` del browser e aggiunge i propri *dopo*,
  il controller legge quelli del browser: la firma non torna e l'utente riceve sempre 401.
  Verificato: un `X-LIL-Ruoli: amministratore` in testa → 401, in coda → 200 con i ruoli
  firmati. Non è un aggiramento: una firma valida per valori scelti dal browser resta
  impossibile. È un guasto silenzioso, difficile da diagnosticare, e dipende dall'ordine che
  sceglie il proxy.
- **Proposta di correzione**: in `identifica`, rifiutare con `_rifiuta("header di identità
  duplicati")` se `len(request.headers.getlist(H)) != 1` per uno dei cinque header. Test con
  un header doppio.

### [BASSA-2] Replay entro la finestra di tolleranza, con un corpo diverso
- **Dove**: `app/ingresso/cms.py:111-119`; decisione A0 §16.2 (corpo non firmato).
- **Voce della checklist**: 9. Firma e ingresso (replay).
- **Scenario**: chi vede una richiesta firmata fra CMS e VPS (log del proxy del CMS che
  registrano gli header, terminazione TLS intermedia) può ripeterla per 60 s (fino a 300 con
  `FIRMA_TOLLERANZA_S`), anche con un **corpo diverso**: ad esempio una
  `POST /api/episodi/<run>/copione/approvazione` firmata, rimandata con un'altra `versione`.
  Verificato che la stessa richiesta è accettata due volte. Il rischio si basa sull'HTTPS fra
  CMS e VPS e sul firewall: è accettabile, ma va scritto.
- **Proposta di correzione**: in `DEPLOY.md`, nella sezione "Da fare prima della produzione":
  il CMS non registra `X-LIL-Firma` nei log, TLS end-to-end fino al VPS. Se in seguito si
  vuole chiudere il caso: una cache in memoria delle firme già viste entro la finestra, solo
  per i metodi che cambiano qualcosa (il processo è unico). Attenzione però: due richieste
  identiche nello stesso secondo, come un doppio clic, hanno la stessa firma. Serve quindi
  un nonce (`X-LIL-Nonce`) dentro la stringa canonica.

### [BASSA-3] Nome con caratteri bidirezionali o invisibili accettato in `--by` e nel registro
- **Dove**: `app/ingresso/cms.py:55,122-126` (`_CONTROLLO` blocca solo `\x00-\x1f\x7f`);
  `app/agente/tabella.py:467`; `app/registro.py:88-90` (`ensure_ascii=False`).
- **Voce della checklist**: 9. Firma e ingresso (nome non validato), 11. Permessi (`--by`).
- **Scenario**: un utente del CMS con nome visualizzato `Mario ‮issoR` (o con
  U+200B/U+2066…) approva un copione: "Approvato da …" e il registro mostrano un nome
  rovesciato o confondibile con un altro. Verificato: il nome passa così com'è
  (`/api/io` → `'Mario ‮issoR'`). Il nome viene dal CMS (firmato), ma spesso è
  modificabile dall'utente stesso.
- **Proposta di correzione**: rifiutare (o togliere) le categorie Unicode `Cc`, `Cf`, `Zl`,
  `Zp` e i caratteri U+202A–U+202E, U+2066–U+2069 e U+200B–U+200F. Normalizzare in NFC.
  Applicare lo stesso controllo a `DEMO_NOME` in `config.py`.

### [BASSA-4] Le risposte 500 da eccezione imprevista escono senza header di sicurezza e senza `no-store`
- **Dove**: `app/main.py:87-111` (il middleware intercetta solo `ErroreController`) e
  `app/main.py:133-136` (il gestore di `Exception` sta nel `ServerErrorMiddleware`, più
  esterno).
- **Voce della checklist**: CSRF e header di sicurezza.
- **Scenario**: ogni eccezione non prevista (vedi BASSA-5, o un errore di I/O del registro)
  produce una risposta 500 senza `Content-Security-Policy`, `X-Content-Type-Options` e
  `Cache-Control: no-store`. Verificato con `versione: "²"`: i tre header sono `None`. Oggi il
  corpo è JSON fisso, quindi l'impatto è minimo. Da A3, con le pagine servite dal controller,
  conviene che nessuna risposta esca senza CSP.
- **Proposta di correzione**: nel middleware aggiungere `except Exception:` →
  `log.exception(...)`, poi `risposta = _errore(ErroreController("ErroreInterno"))`, così gli
  header si aggiungono sempre. In alternativa, un middleware ASGI puro che aggiunge gli
  header in `send`, sui messaggi `http.response.start`.

### [BASSA-5] Un intero come `"²"` fa uscire un 500 invece di un 400
- **Dove**: `app/agente/tabella.py:363-365` (`str.isdigit()` è vero per `"²"`, `"①"`, ma
  `int()` solleva `ValueError`); stesso schema in `app/config.py:56-58`.
- **Voce della checklist**: tabella dei comandi e costruzione degli argomenti.
- **Scenario**: `{"run": "run-uno", "versione": "²"}` su un'azione con `INTERO`. Il
  `ValueError` non è fra quelli che `azioni.py:94` converte, quindi la risposta è 500
  `ErroreInterno`, con traceback nel log. Verificato. Non si lancia niente, ma il log si
  riempie a comando, e il caso si somma a BASSA-4.
- **Proposta di correzione**: usare `re.fullmatch(r"[0-9]{1,9}", valore)` al posto di
  `isdigit()` (anche in `config._intero` e in `_TIMESTAMP`, già corretto). Aggiungere un test
  con `"²"` → 400 `RichiestaNonValida`.

### [BASSA-6] Il timeout ferma solo il processo principale: i figli possono restare vivi
- **Dove**: `app/agente/esecutore.py:110-120`.
- **Voce della checklist**: 2. Sottoprocessi.
- **Scenario**: `forma_onda` o `anteprima_documento` superano il timeout. `termina` manda
  SIGTERM al solo pid del tool. Se il tool esce entro 10 s, non si arriva al `killpg`, e un
  `ffmpeg` (o un'estrazione) lanciato dal tool nello stesso gruppo continua a girare fino al
  suo limite interno (fino a 15 minuti), occupando CPU. Ripetendo la richiesta il lavoro si
  accumula: è un disservizio, nessun dato è in pericolo.
- **Proposta di correzione**: dopo che il processo principale è uscito, mandare
  `os.killpg(processo.pid, signal.SIGTERM)` al gruppo (ignorando `ProcessLookupError`), oppure
  mandare subito il SIGTERM al gruppo. Questo vale solo per i comandi brevi: per i lavori
  lunghi resta la regola "un solo SIGTERM al processo" del contratto.

### [BASSA-7] La rete di sicurezza sui percorsi è parziale
- **Dove**: `app/servizi/esiti.py:66` (`_PERCORSO_ASSOLUTO` riconosce solo stringhe che sono
  *interamente* un percorso, senza spazi), `:75-81`, `:83-99` (le chiavi dei dizionari non si
  controllano).
- **Voce della checklist**: 6. Segreti / pulizia dei percorsi negli esiti.
- **Scenario**: un messaggio dell'agente come `"ffmpeg non riesce a leggere /tmp/xyz.wav: …"`
  o `"…/srv/dati/archivio/run…"` (percorso dentro una frase, fuori da `AGENT_DIR` e da
  `HOME`) arriva al browser così com'è. Lo stesso vale per un dizionario con un percorso come
  **chiave**. Il contratto garantisce che l'agente non lo faccia: questa è solo la rete di
  sicurezza, che però non copre questi casi.
- **Proposta di correzione**: in `stringa_sospetta` cercare un percorso assoluto anche
  *dentro* il testo (`re.search(r"(?:^|[\s'\"(=:])(?:/|~/)[^\s/]+/", testo)`), oltre a
  `TMPDIR` e alle cartelle delle impostazioni (`cartelle.*` di `settings_show`). In `pulisci`
  passare anche le chiavi dei dizionari a `stringa_sospetta`.

### [BASSA-8] `verifica_tabella` non lega i flag di conferma ai gettoni
- **Dove**: `app/agente/tabella.py:293-318`.
- **Voce della checklist**: 10. Gettoni di conferma, 11. Permessi.
- **Scenario**: oggi è tutto corretto: `--confirm-clone-retry` sta nei `fissi` di
  `ritenta_clonazione`, che ha `solo_da_gettone=True`. Ma niente impedisce che una voce
  futura metta `--accept-changed-music`, `--accept-incomplete-name`,
  `--accetta-nome-incompleto` o `--confirm-clone-retry` nei `fissi` o fra gli `argomenti`
  (ad esempio un `FLAG` scelto dal browser). Questo darebbe un flag di conferma senza esito 3,
  che il contratto vieta ("mai d'ufficio").
- **Proposta di correzione**: in `verifica_tabella`, con
  `FLAG_CONFERMA = {"--accept-changed-music", "--accept-incomplete-name",
  "--accetta-nome-incompleto", "--confirm-clone-retry", "--remove-id"}`: vietarli in
  `argomenti`, ammetterli in `fissi` solo con `solo_da_gettone=True` e in `conferme` solo come
  `Conferma.flag`. Aggiungere il test corrispondente.

### [BASSA-9] Corpo JSON senza limite di dimensione
- **Dove**: `app/main.py:96-103` (si controllano solo header e tipo); le rotte leggono il
  corpo per intero (`RispostaConferma`, e da A2 tutte le rotte JSON).
- **Voce della checklist**: 3. File/input non fidati (memoria).
- **Scenario**: un utente autenticato manda un `POST /api/conferme/x` con
  `Content-Type: application/json` e 500 MB di corpo (`chunked`). Uvicorn e FastAPI lo
  tengono tutto in memoria prima di validarlo. In produzione dipende dal limite del proxy del
  CMS, che però deve accettare almeno 50 MB per i brani.
- **Proposta di correzione**: nel middleware, per `application/json`, rifiutare con 413
  `FileTroppoGrande` un `Content-Length` sopra 256 KB. Per il `chunked`, contare in un
  middleware ASGI che avvolge `receive`. I caricamenti (`octet-stream`, A2) hanno già il loro
  limite in streaming.

### [BASSA-10] Registro: azione eseguita ma riga non scritta; permessi del file esistente non verificati
- **Dove**: `app/servizi/azioni.py:105-112`; `app/registro.py:44-53, 91-97`.
- **Voce della checklist**: registro attività, 7. Lock e file temporanei.
- **Scenario**: (a) dopo un'approvazione andata a buon fine, `registro.scrivi` fallisce (disco
  pieno, file reso non scrivibile): l'eccezione diventa un 500. L'approvazione però è già
  avvenuta, e il registro non la contiene. L'utente vede un errore e magari riprova (riceve
  `NothingToChange` o simili). (b) Se il file del registro o la sua cartella esistono già con
  permessi larghi (0644/0755), l'avvio non lo segnala: la `mode` 0600/0700 vale solo alla
  creazione.
- **Proposta di correzione**: (a) se la scrittura fallisce, `log.critical` con la riga
  completa (così non si perde) e risposta con l'esito vero dell'agente più un avviso, invece
  di un 500 generico. (b) in `verifica()` fare `os.fstat` sul file aperto e rifiutare l'avvio
  (o dare un avviso) se `st_mode & 0o077`, e così per la cartella.

## Note

- **[NOTA-1] L'impronta del gettone non si usa mai** (`app/servizi/gettoni.py:34,65`,
  `app/api/conferme.py:34`). Il legame con "run e domanda" vale per costruzione, perché il
  browser manda solo il gettone e il controller rilancia gli argomenti salvati. Non c'è però
  nessun controllo che la domanda sia ancora la stessa: per `MusicChanged`,
  `--accept-changed-music` accetta anche un brano sostituito *di nuovo* nei 5 minuti fra
  domanda e conferma. È la semantica del flag dell'agente. Togliere `impronta` o documentarne
  l'uso (ad esempio nel registro della conferma); CLAUDE.md e A0 la descrivono come un
  vincolo.
- **[NOTA-2] `--by` è il nome visualizzato, non l'id** (`app/ingresso/__init__.py:22-25`). Due
  utenti con lo stesso nome non si distinguono nelle firme dell'agente ("Approvato da Mario
  Rossi"). Il registro attività ha l'id, quindi la traccia resta. Volendo:
  `"Mario Rossi (mrossi)"`.
- **[NOTA-3] Il gettone sta nel percorso** (`POST /api/conferme/{gettone}`), quindi finisce
  nell'access log di uvicorn e del proxy. È legato all'utente e monouso, quindi il rischio è
  trascurabile. Volendo, lo si sposta nel corpo o in un header.
- **[NOTA-4] In cms il CSRF dipende anche dal CMS**: `X-LIL-Richiesta` ferma i form di altri
  siti solo se il CMS non risponde ai preflight CORS in modo permissivo
  (`Access-Control-Allow-Origin`/`-Headers` con `X-LIL-Richiesta`) sui percorsi inoltrati. Va
  scritto in `DEPLOY.md` (A4).
- **[NOTA-5] Configurazione poco controllata** (`app/config.py:114-117`): `DEMO_NOME` non è
  validato (con caratteri di controllo ogni azione con `--by` dà 500); `PERMESSI_FILE` può
  essere un percorso relativo (dipende dalla cartella corrente di systemd).
- **[NOTA-6] stdout letto senza limite** (`app/agente/esecutore.py:141`): `communicate()`
  legge tutto, e `STDOUT_MAX` si applica solo dopo (`limit=` vale per `readline`). L'agente è
  fidato e il rischio è basso. Per i lavori lunghi di A2, leggere a blocchi con un tetto.
- **[NOTA-7] `OPZIONI_VIETATE` non contiene gli argomenti vietati dal web per singolo tool**
  (`--document` di `script_write.py`, `--speakers`, `--participant`, `--presenter`,
  `--interviewer`, `--format`, `--target-minutes`). Oggi non sono in tabella, e l'agente li
  rifiuta comunque. Conviene una mappa tool → opzioni vietate in `verifica_tabella`.
- **[NOTA-8] `LessonRemovalRequired`** (`app/api/conferme.py:60-62`): se gli `id` dei
  candidati sono interi, `scelta` (stringa) non corrisponde mai. È un problema funzionale, non
  di sicurezza, da controllare in A5.

## Verificato e a posto

- **1. Path traversal**: `--run-dir` passa da `controlla_run` (regex senza `/`, niente `..`,
  cartella reale e non link sotto `output/`). I nomi passano da `controlla_nome` (niente
  `/ \ ..`, caratteri di controllo, `.`/`-` iniziali, ≤ 200). I posizionali (solo `NOME`)
  vanno dopo `--`. L'agente ricontrolla da sé (contratto §1 "Percorsi"). In A1 nessuna rotta
  legge o scrive file.
- **2. Sottoprocessi**: un solo punto (`esecutore.avvia_processo`),
  `create_subprocess_exec` con lista, `stdin=DEVNULL`, nuova sessione, cwd = `AGENT_DIR`,
  python fisso `AGENT_DIR/.venv/bin/python` (non configurabile). Timeout dalla tabella.
  `None` solo per i lavori lunghi, che `esegui_e_valuta` rifiuta (vanno in coda).
- **Tabella e argomenti**: i valori liberi sono sempre `--opzione=valore`, e un valore che
  comincia con `-` resta un valore. Le opzioni a più token (`SCELTE`, `INTERI`) accettano
  solo un elenco chiuso o interi senza segno. Un parametro fuori tabella dà `ErroreTabella`
  (500, non arriva all'agente). I nomi delle opzioni sono fissi nel codice, quindi il browser
  non può scegliere né abbreviare un'opzione: niente `--trusted`. `verifica_tabella` rifiuta
  i tool vietati, `--by`, `--no-*` (tranne `--no-licenza`) e le opzioni di §1. L'agente non
  usa `fromfile_prefix_chars`. `--remove-id` arriva solo da `parametri_nascosti` e da un
  candidato dell'esito 3.
- **Ambiente dei sottoprocessi**: costruito da zero (`PODCAST_AGENT_WEB=1`, `PATH` fisso più
  `AGENT_PATH_EXTRA` assoluti, `HOME`, `LANG`, `TMPDIR`). `LIL_SEGRETO` e le altre variabili
  del controller non passano. `doctor.py --web` all'avvio controlla `web_mode` (testato
  anche il caso negativo).
- **6. Segreti**: il segreto non compare in log, risposte o errori di configurazione (si dice
  solo che è troppo corto). `_rifiuta` registra il motivo senza i valori degli header. Al
  browser, per ogni errore di firma, arriva un 401 con corpo fisso. Gli errori
  dell'adattatore (`InvalidArguments`, …) danno un messaggio generico, e stderr resta nel
  log. `voice_id` e le chiavi con percorsi di §1 si tolgono. In `voices_list.py`, nelle
  revoche e in `CloneOutcomeUnknown.details` l'agente non espone `voice_id`.
- **7. Lock e temporanei**: il registro si apre con `O_APPEND|O_NOFOLLOW`, rifiuta un link
  simbolico e scrive una riga per `write` sotto lock di thread, con `fsync`. I gettoni sono in
  memoria sotto `threading.Lock`: il consumo è atomico, quindi un gettone non si usa due
  volte nemmeno con richieste parallele. Il controller non crea temporanei. `VERSION` si
  legge con `O_NOFOLLOW` e un limite di 100 byte.
- **8. Garanzie di dominio**: nessun retry della clonazione. `ritenta_clonazione` parte solo
  da un gettone di `CloneOutcomeUnknown` (`solo_da_gettone`), con il nome preso da
  `details.speaker` (che nell'agente è il nome della voce, `src/voice_prep.py:739-744`)
  ricontrollato da `controlla_nome`, e richiede `voce_clonazione`. Approvazione con
  `--version` esplicita (niente `--current`). Consenso con `--expect-sha256` ed
  `--expect-revision`.
- **9. Firma e ingresso**: `hmac.compare_digest` su stringhe di formato fisso
  (`^v1=[0-9a-f]{64}$`). Stringa canonica con percorso **grezzo** (`raw_path`) e query: il
  percorso codificato `/api/%69o` funziona solo se firmato così com'è (verificato). Metodo in
  maiuscolo. Righe separate da `\n`, carattere che non può comparire in un valore di header,
  quindi la stringa non è ambigua. Timestamp `^[0-9]{1,12}$` entro la tolleranza (5–300 s).
  Utente validato prima della firma. Ruoli validati dopo e intersecati con quelli del file
  dei permessi. Nome decodificato in UTF-8 stretto, ≤ 100, senza caratteri di controllo.
  Ogni percorso tranne `/salute` passa dal middleware **prima** del routing, anche quelli
  inesistenti (401, nessuna enumerazione), e `docs/openapi` sono spenti. La demo rifiuta
  `HOST` ≠ `127.0.0.1` all'avvio e ogni client non di loopback per richiesta, con
  `proxy_headers=False` (`X-Forwarded-For` non conta). Per l'header `Host` vedi MEDIA-1.
- **10. Gettoni**: `secrets.token_urlsafe(32)` (256 bit), confronto a tempo costante, monouso,
  scadenza 30–1800 s, al massimo 50 per utente. Un gettone di un altro utente, scaduto o già
  usato dà sempre lo stesso errore. Il browser manda solo il gettone (e la `scelta`, che deve
  stare fra i candidati salvati): azione, run e argomenti vengono dal record. Il flag si
  aggiunge solo se la tabella lo associa a quell'`error_type` per quell'azione, e
  `costruisci_argomenti` rifiuta ogni flag fuori da `conferme`. Il gettone nasce solo con
  exit 3 e un `error_type` coerente. Per gli esiti 3 senza regola (`AmbiguousName`,
  `ConsentRequired`, …) la conferma dà `GettoneNonValido`.
- **11. Permessi**: ogni azione passa da `esegui_e_valuta`, che controlla `cmd.permesso`
  prima di lanciare, anche quando l'azione viene da una conferma (con i ruoli dell'utente al
  momento della conferma, non della domanda). `CloneOutcomeUnknown` di `produci`
  (`produrre`) porta a `ritenta_clonazione`, che vuole `voce_clonazione`: nessuna escalation.
  I rifiuti per permesso si registrano. `--by` viene solo da `utente.firma`: non c'è un
  `Arg` con nome `by`, quindi un parametro `by` dal browser dà `ErroreTabella`. Il file dei
  permessi si valida all'avvio, e un permesso sconosciuto ferma l'avvio.
- **12. Upload e download**: in A1 non ci sono rotte di file (le rotte registrate sono
  `GET /api/io`, `GET /salute`, `POST /api/conferme/{gettone}`), niente `StaticFiles`, e
  `app/file/` è vuoto. Il middleware accetta già `application/octet-stream` (vedi sotto).
- **13. SSE**: assente; `app/lavori/` è vuoto.
- **CSRF**: `POST/PUT/PATCH/DELETE` su `/api/` vogliono `X-LIL-Richiesta: 1` e, se c'è un corpo,
  `application/json` o `application/octet-stream` (verificato: senza header → 400,
  `text/plain` → 415). Il corpo `chunked` senza tipo viene rifiutato. Non ci sono GET che
  cambiano qualcosa.
- **Header di sicurezza**: CSP restrittiva senza `unsafe-inline`, `nosniff`,
  `Referrer-Policy: no-referrer`, COOP e `no-store` sulle API (eccetto i 500, BASSA-4).
- **Avvio**: `VERSION` uguale ad `AGENT_VERSIONE_ATTESA`, `doctor.py --web` e
  `settings_show.py` obbligatori. `/salute` risponde solo `{"ok": bool}`.
- **Registro**: righe JSON (niente iniezione di righe), dettagli senza percorsi e troncati a
  2000 caratteri. Il costo viene dal JSON dell'agente, mai dal browser.
- **Commit `2104260`**: aggiunge solo `podcast-agent/VERSION` (una riga), unica modifica
  autorizzata all'agente (A0 §16.1).

## Da verificare nelle prossime fasi

**A2 · caricamenti** (A0 §10):
- il nome da `X-LIL-Nome-File` va decodificato in UTF-8 stretto e normalizzato in NFC; niente
  `/ \ ..`, caratteri di controllo e invisibili (vedi BASSA-3), `.`/`-` iniziali, ≤ 150;
  estensione in elenco, mai `.json/.lock/.tmp`;
- la firma dei primi byte (un `ffconcat` o una playlist travestiti da mp3 si rifiutano);
- il limite in streaming *prima* di scrivere, con `Content-Length` mentito o assente
  (`chunked`);
- il temporaneo `.<nome>.tmp` con `O_EXCL|O_NOFOLLOW|0600`, cancellato in caso di errore o di
  disconnessione; `rename` atomico; nessun link simbolico seguito (né il file, né la
  cartella della libreria);
- la sostituzione solo con un gettone `FileEsistente` legato a utente, libreria e nome
  normalizzato (non riusabile per un altro file), con il permesso di caricare in quella
  libreria e mai durante un lavoro `in_corso` che usa il file; nel registro;
- per le voci: dopo una sostituzione, il consenso torna "superato".

**A2 · download**:
- solo nomi elencati da `episode_info` (`file_finale`, `file[]` con `interno: false`) o da
  `documents_list`, risolti dentro `output/<run>` o `documenti/` con `O_NOFOLLOW`, e un
  controllo `realpath` che il file stia lì dentro; niente `.json` accanto alle voci;
- `Range`: un solo intervallo, intervalli invertiti o fuori misura → 416, niente overflow;
- `Content-Disposition` con nome sicuro (RFC 5987, niente `"`, CR/LF),
  `Content-Type` dall'estensione, `nosniff`; permesso `leggere`.

**A2 · SSE e coda**:
- `_eventi.jsonl` aperto con `O_NOFOLLOW` dentro la run validata, solo righe complete, e
  ogni riga ripulita con `Pulitore` (gli eventi possono contenere percorsi);
- `Last-Event-ID` / `?dopo=N` validati come interi ≥ 0, con un tetto; limite di connessioni
  SSE per utente; heartbeat; chiusura quando il client si disconnette (nessun task
  orfano);
- la coda deve rispettare permesso, `solo_da_gettone`, registro (con `lavoro`), gettone per
  gli esiti 3 dei lavori lunghi (`MusicChanged`, `CloneOutcomeUnknown`, `ConsentRequired`) e
  `accoda_risposta` dalle conferme; la stima prima della produzione con il costo nel
  registro;
- interruzione: un solo SIGTERM per lavoro, mai un SIGKILL durante `preparazione_voci`;
  controllo che chi interrompe abbia il permesso dell'azione;
- `in_corso.istruzione` di una revisione: decidere se mostrarla agli altri utenti;
- cosa succede a un'azione breve che registra (approvazione, consenso) se il client si
  disconnette e la richiesta viene cancellata: `esecutore.esegui` manda SIGTERM, l'azione
  potrebbe essere già stata fatta, e la riga del registro manca. Verificare con
  Starlette 1.7 / `BaseHTTPMiddleware` e, se serve, proteggere la parte
  esecuzione-più-registro con `asyncio.shield`.

**A3/A4**:
- la CSP con le pagine reali (nessuno script inline); `frame-ancestors` se il CMS incorpora la
  pagina;
- la CORS del CMS (NOTA-4); in `DEPLOY.md`: niente log di `X-LIL-Firma`, TLS fino al VPS
  (BASSA-2); il CMS toglie gli `X-LIL-*` del browser (già scritto) e la duplicazione si
  rifiuta (BASSA-1);
- **A5 · retake**: `--direction` è già una `SCELTA_RIPETUTA` su un elenco chiuso
  (`INDICAZIONI_RETAKE`), e nessun testo libero arriva a `retake.py`. Verificare che
  l'elenco coincida con la whitelist di `config.yaml` dell'agente e che "Pronuncia diversa"
  non ci sia.
