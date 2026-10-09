# Revisione di sicurezza · A2

Intervallo esaminato: modifiche non committate su `3c4e5d9` (A1 + correzioni, solo come contesto).
File esaminati: 16 (4 modificati: `app/api/conferme.py`, `app/main.py`, `app/servizi/azioni.py`,
`app/servizi/errori.py`; 12 nuovi: `app/api/{episodi,librerie,lavori}.py`,
`app/lavori/{coda,eventi}.py`, `app/file/{nomi,caricamenti,download}.py`, `docs/avanzamento/A2-piano.md`,
`tests/{test_caricamenti_download,test_flusso_completo,test_lavori}.py`), più il codice che chiamano
(`app/agente/tabella.py`, `app/agente/esecutore.py`, `app/servizi/{gettoni,permessi,esiti}.py`,
`app/ingresso/cms.py`). Data: 2026-10-09.

Test: `pytest` verde (219 test). Verifiche con script in `/private/tmp/claude-501/revisione-a2/`
(agente finto di `tests/conftest.py`, uvicorn reale su 127.0.0.1 dove serviva lo streaming).

## Sintesi

| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 3 |
| Bassa | 8 |
| Nota | 8 |

Nessun bypass di identità o permessi, nessuna lettura o scrittura fuori dalle cartelle, nessun flag
di conferma senza gettone: i percorsi nuovi passano tutti dalla tabella e da `controlla_azione`,
i download aprono solo nomi elencati dall'agente con `O_NOFOLLOW` sul descrittore della cartella, i
caricamenti seguono quasi tutte le regole del contratto. I problemi sono nella coda: un `await`
(la stima) fra il controllo "un lavoro per run" e l'accodamento lascia passare produzioni doppie
(K16); un'interruzione chiesta mentre il processo sta partendo si perde (K21). Il limite al corpo
JSON di A1 si aggira con `Content-Type: application/octet-stream` sulle rotte JSON nuove.

## Problemi

### [MEDIA-1] Due "Produci" ravvicinati accodano due (o più) produzioni sulla stessa run e superano `CODA_MAX`
- **Dove**: `app/lavori/coda.py:131-158` (`accoda`: controllo a 132-136, `await esecutore.esegui(... "stima" ...)` a 142, `self._attesa.append` a 158)
- **Voce della checklist**: 13. SSE e coda (K16); 8. garanzie di dominio (spesa)
- **Scenario**: il controllo `lavoro_della_run(run)` e quello su `coda_max` girano **prima**
  della stima, che è un sottoprocesso (fino a 30 s); l'accodamento avviene dopo. Due richieste
  `POST /api/episodi/run-uno/produzione` arrivate durante la stima (un doppio clic, due
  approvatori, due schede) passano entrambe. Verificato (`t_doppia_produzione.py`, stima finta di
  1 s, `CODA_MAX=1`, worker occupato su un'altra run): tre POST simultanei → tre `202`, in attesa
  `['run-uno', 'run-uno', 'run-uno']` (oltre `CODA_MAX=1`), `produce.py` lanciato 4 volte, quattro
  righe `accodato` nel registro. Le produzioni girano una dopo l'altra (worker unico) e la cache
  dei segmenti contiene la spesa delle successive, ma ognuna riparte e rimonta. La stessa
  finestra lascia superare il limite della coda a chiunque abbia `produrre`, e il limite c'è
  proprio per evitare che il worker resti occupato.
- **Proposta di correzione**: rendere atomico "controllo + prenotazione". Per esempio, in `accoda`
  creare il `Lavoro` e metterlo in `self._attesa` (o in un insieme `self._prenotati` che
  `lavoro_della_run` e il conteggio della coda considerano) **prima** dell'`await` della stima,
  con stato `in_coda`. Se la stima fallisce o la richiesta viene cancellata, si toglie il lavoro
  in un `try/except BaseException`. In alternativa un `asyncio.Lock` attorno a tutto `accoda`.
  Il worker non deve prendere un lavoro prenotato prima che la stima abbia scritto il costo:
  basta un flag `pronto` sul `Lavoro`, impostato dopo la riga `accodato`. Test: tre POST
  simultanei con una stima lenta → un 202 e due 409 `LavoroGiaInCorso`.

### [MEDIA-2] Un'interruzione chiesta mentre il processo sta partendo si perde: la produzione arriva in fondo
- **Dove**: `app/lavori/coda.py:291-295` (`lavoro.stato = "in_corso"` prima di `await esecutore.avvia_processo`, `lavoro.processo` assegnato dopo), `app/lavori/coda.py:237-247` (`_sigterm` imposta `interruzione_richiesta` anche quando `processo` è `None`), `app/lavori/coda.py:229-230`, `102-104`
- **Voce della checklist**: 13. SSE e coda (K21); 11. permessi (spesa che l'utente ha chiesto di fermare)
- **Scenario**: fra `stato = "in_corso"` e l'assegnazione di `lavoro.processo` c'è l'`await` di
  `create_subprocess_exec` (fork + exec; sotto carico, decine di millisecondi o più). Se
  `POST /interruzione` arriva in quella finestra, `_sigterm` segna `interruzione_richiesta = True`
  ma non manda niente (`processo` è `None`). La risposta è "Interruzione richiesta." e il registro
  scrive `interrompi/richiesta`. Ogni richiesta successiva risponde "Interruzione gia'
  richiesta." e non manda alcun segnale. Verificato (`t_interruzione_persa.py`,
  `avvia_processo` rallentato di 1 s): prima interruzione → "richiesta"; seconda → "gia'
  richiesta"; `produce.log` mostra `avvio` e `fine naturale`, nessun SIGTERM; esito `ok: true`.
  La produzione (che costa) va avanti fino in fondo anche se l'utente ha chiesto di fermarla e il
  sistema gli ha detto che era fermata. Allo spegnimento succede lo stesso: con `processo is None`,
  `chiudi()` non manda il SIGTERM e non aspetta.
- **Proposta di correzione**: in `_esegui`, subito dopo `lavoro.processo = processo`, se
  `lavoro.interruzione_richiesta` è già vero mandare il SIGTERM (una volta, tramite una funzione
  che separa "richiesta registrata" da "segnale inviato", ad esempio un campo
  `sigterm_inviato`). In `_sigterm` impostare `sigterm_inviato` solo quando il segnale parte
  davvero. In alternativa, se l'interruzione è già chiesta prima dell'avvio, non avviare il
  processo e chiudere il lavoro come annullato. Test: `avvia_processo` rallentato, interruzione
  nella finestra → il processo riceve esattamente un SIGTERM.

### [MEDIA-3] Il limite di 256 KB al corpo JSON si aggira con `Content-Type: application/octet-stream`
- **Dove**: `app/main.py:73-76` (`LimiteCorpo` lascia passare **ogni** richiesta octet-stream, su qualsiasi rotta), `app/api/episodi.py:100-101` (`scegli_musiche` chiama `await request.json()` senza guardare il tipo), le rotte con modello pydantic (`episodi.py`, `librerie.py`, `lavori.py`, `conferme.py`)
- **Voce della checklist**: 3. file non fidati (memoria); 12. upload (K4)
- **Scenario**: il middleware esenta octet-stream perché i caricamenti hanno un limite proprio, ma
  l'esenzione vale per tutte le rotte, e il middleware d'ingresso ammette octet-stream su ogni
  metodo che cambia qualcosa. Un utente autenticato manda
  `PUT /api/episodi/run-uno/musiche` con `Content-Type: application/octet-stream` e un corpo JSON
  di centinaia di MB. Verificato (`t_corpo.py`): un corpo di 40 MB su `/musiche` viene letto e
  **interpretato** per intero (risposta `400 RichiestaNonValida` dal controllo delle chiavi,
  che viene dopo il parsing). Lo stesso corpo con `application/json` dà 413. Le rotte pydantic
  leggono il corpo intero prima di rifiutarlo. L'effetto è memoria e CPU senza limite nel
  processo unico, che tiene anche la coda: lo frena solo il limite del proxy del CMS, che deve
  accettare almeno 50 MB. Il problema annulla di fatto la correzione BASSA-9 di A1 sulle rotte
  JSON aggiunte in A2.
- **Proposta di correzione**: in `LimiteCorpo` esentare octet-stream solo per le rotte di
  caricamento (`POST /api/documenti`, e in A5 voci e brani), con un elenco esplicito di
  `(metodo, percorso)`. In alternativa, nel middleware d'ingresso, accettare octet-stream solo su
  quelle rotte e `application/json` altrove (415 sulle altre combinazioni). In `scegli_musiche`
  usare un modello pydantic, oppure controllare il `Content-Type` prima di `request.json()`.
  Test: octet-stream oltre 256 KB su `/musiche` e su `/copione/approvazione` → 413 o 415, senza
  leggere il corpo.

### [BASSA-1] La sostituzione toglie ogni file non nascosto con la stessa chiave, anche `.json` e `.lock`
- **Dove**: `app/file/caricamenti.py:62-70` (`esistenti_con_chiave`), `146-154` (unlink)
- **Voce della checklist**: 12. upload (K6); 7. lock; 8. garanzie di dominio
- **Scenario**: verificato (`t_caricamenti.py`): con `Report.pdf`, `Report.json` e `report.lock` in
  `documenti/`, il caricamento di `report.txt` dà 409 con `esistenti: [Report.json, Report.pdf,
  report.lock]`. Confermato con il gettone, **toglie tutti e tre**. In `documenti/` oggi
  l'impatto è basso: `.json`, `.lock` e `.tmp` non si caricano, e i file riservati dell'agente
  sono nascosti. Con lo stesso codice in A5 ("voci e brani con lo stesso codice"), sostituire
  `Mario Rossi.wav` toglierebbe `Mario Rossi.json`, cioè metadati e consenso della voce (sidecar
  dell'agente, `voice_library._SIDECAR_EXTENSIONS`). La voce risulterebbe nuova invece che con
  consenso `superato`. Si perderebbero dati condivisi e si rischierebbe una nuova clonazione.
  Si cancellerebbe anche un `.lock` in uso.
- **Proposta di correzione**: in `esistenti_con_chiave` considerare solo i file con
  un'estensione ammessa per quella libreria (il parametro `estensioni` di `nome_caricato`). Non
  includere mai `.json`, `.lock` e `.tmp` né fra gli esistenti né fra i file da togliere. In A5,
  per le voci, il `.json` resta (lo aggiorna l'agente). Test con un sidecar accanto al file
  sostituito.

### [BASSA-2] K7 controllato solo all'inizio: una sostituzione lunga si completa durante un lavoro
- **Dove**: `app/api/librerie.py:102-103` (controllo prima di leggere il corpo), `app/file/caricamenti.py:133-143` (rename senza ricontrollo)
- **Voce della checklist**: 12. upload (K7)
- **Scenario**: verificato: si comincia una sostituzione con il gettone mentre la coda è vuota,
  il corpo arriva lentamente, nel frattempo parte una produzione
  (`in_corso`), e la sostituzione finisce con `201`. Per i documenti l'effetto è solo che una
  stesura contemporanea usa la versione vecchia o la nuova (il rename è atomico). Per le voci di
  A5 il controllo deve valere al momento del rename, non all'inizio.
- **Proposta di correzione**: passare a `salva` una funzione `puo_sostituire()` da richiamare
  subito prima del `rename`, nello stesso tratto senza `await` del ricontrollo di
  `esistenti_con_chiave` (righe 133-143), che sollevi `LavoroGiaInCorso`. Il gettone andrebbe
  consumato solo dopo i controlli che possono fallire (oggi un 409 per lavoro in corso o un 413
  lo bruciano).

### [BASSA-3] Il limite SSE di 4 per utente si supera di uno con richieste simultanee
- **Dove**: `app/api/lavori.py:141-146` (controllo nella rotta, incremento dentro il generatore)
- **Voce della checklist**: 13. SSE (K15)
- **Scenario**: verificato con uvicorn reale (`t_sse.py`): 12 e poi 60 connessioni simultanee dello
  stesso utente danno 5 risposte `200` (non 4), il resto `429`. Dopo la chiusura il contatore
  torna `{}`. La violazione è piccola: il commento "uno stream mai avviato non lo tocca"
  scambia la correttezza del conteggio con la precisione del limite.
- **Proposta di correzione**: incrementare il contatore nella rotta, prima di restituire la
  `StreamingResponse`. Decrementarlo nel `finally` del generatore e, per lo stream mai avviato,
  con una `BackgroundTask` o un wrapper ASGI che decrementa se il corpo non è mai stato iterato.

### [BASSA-4] Un nome di 150 caratteri non ASCII supera i 255 byte del filesystem: 500 invece di un rifiuto
- **Dove**: `app/file/nomi.py:45` (limite in caratteri), `app/file/caricamenti.py:98-99` (`.{nome}.{12 hex}.tmp`)
- **Voce della checklist**: 12. upload (K1)
- **Scenario**: `X-LIL-Nome-File` con 140 "è" + `.txt` (284 byte in UTF-8): su macOS (APFS, 255
  caratteri) passa (verificato). Su Linux ext4/xfs (`NAME_MAX` = 255 **byte**), cioè sul VPS,
  `os.open` del temporaneo (lungo 302 byte) solleva `ENAMETOOLONG`, che nessuno gestisce:
  `500 ErroreInterno` con il traceback nel log. Non c'è effetto sui file. Il problema
  riguarda già nomi da circa 120 caratteri accentati.
- **Proposta di correzione**: in `nome_caricato` limitare anche la lunghezza in byte UTF-8
  (es. `len(nome.encode()) <= 200`, che lascia spazio al prefisso e al suffisso del
  temporaneo), con `FileNonAmmesso`. In `salva` tradurre ogni `OSError` in un errore del
  controller.

### [BASSA-5] Stesura o revisione oltre il timeout: dopo il SIGTERM l'attesa non ha limite e la coda si ferma
- **Dove**: `app/lavori/coda.py:300-310`
- **Voce della checklist**: 13. coda (K21, robustezza)
- **Scenario**: un `script_write.py` che non esce dopo il SIGTERM (un handler dell'agente che
  attende la risposta del modello, un blocco in una libreria) tiene il worker in
  `await comunicazione` senza fine. Tutte le produzioni in coda restano ferme finché qualcuno non
  riavvia il servizio. Per stesura e revisione il contratto non vieta il SIGKILL ("Se un
  processo va ucciso (SIGKILL), il lock si libera da solo"); il divieto riguarda la produzione
  in `preparazione_voci`.
- **Proposta di correzione**: per i lavori con `timeout` non nullo (stesura, revisione,
  `lezioni_proponi`), dopo il SIGTERM aspettare un tempo di grazia (es. 60 s), poi SIGKILL al
  processo (non al gruppo, per coerenza con "un solo SIGTERM"). Le produzioni (`timeout=None`)
  restano senza SIGKILL. Annotarlo nel commento della coda.

### [BASSA-6] Registro senza riga d'esito quando il lavoro fallisce nel controller
- **Dove**: `app/lavori/coda.py:268-273` (`except Exception`), `282-295`
- **Voce della checklist**: 13. coda (K18)
- **Scenario**: la run viene cancellata fra l'accodamento e l'esecuzione, oppure
  `avvia_processo` fallisce (python dell'agente mancante, `EMFILE`). `riga_di_comando` o
  `avvia_processo` sollevano un errore, il lavoro diventa `500 ErroreInterno` e nel registro
  resta solo `accodato`, senza esito. Lo stesso vale allo spegnimento (`CancelledError`):
  produzione lasciata girare, nessuna riga. Le produzioni hanno il registro obbligatorio.
- **Proposta di correzione**: nel `except Exception` (e, allo spegnimento, in `chiudi()` quando il
  processo resta vivo) scrivere una riga `esito: "ErroreInterno"` o
  `"spegnimento_processo_lasciato"` con `lavoro=id`.

### [BASSA-7] La produzione si accoda anche se la stima fallisce: nessun costo nel registro
- **Dove**: `app/lavori/coda.py:139-147`
- **Voce della checklist**: 13. coda (K20)
- **Scenario**: `estimate.py` va in timeout (30 s), o esce con un errore che la produzione poi non
  ripete. Il lavoro si accoda lo stesso con `costo_stimato_usd: null`, e resta solo un warning
  nel log. Chi ha lanciato una spesa resta nel registro, ma senza importo, contro la regola 6 del
  `CLAUDE.md` ("costo stimato").
- **Proposta di correzione**: se la stima non è `ok` con un `cost_usd` numerico, rispondere con
  l'esito della stima (o un `ServizioEsterno`/`Timeout`) senza accodare. Se invece si sceglie di
  accodare comunque, scriverlo nel registro (`dettagli.stima: "non riuscita"`) e documentarlo nel
  piano.

### [BASSA-8] Allo spegnimento le azioni brevi protette vengono cancellate: SIGTERM e SIGKILL al gruppo
- **Dove**: `app/servizi/azioni.py:160-165` (`asyncio.shield`), `app/agente/esecutore.py:155-157` (`except CancelledError: await termina(processo)`)
- **Voce della checklist**: 14. disconnessione (K23); 8. garanzie di dominio
- **Scenario**: lo `shield` protegge dalla cancellazione della richiesta, ma il compito interno
  resta un task del loop. Allo spegnimento, dopo il lifespan, `asyncio.run` cancella tutti i task
  rimasti, compresi quelli in `_PROTETTI`. L'esecutore li tratta come timeout: SIGTERM al gruppo,
  10 s, **SIGKILL al gruppo**. Il caso che conta è `ritenta_clonazione`
  (`--confirm-clone-retry`, fino a 11 minuti, spesa): fermato così, l'esito della clonazione resta
  incerto (`CloneOutcomeUnknown`, possibile voce duplicata sul provider). Succede solo
  allo spegnimento, con una conferma di clonazione in corso.
- **Proposta di correzione**: nel lifespan, prima di `coda.chiudi()`, aspettare i compiti in
  `_PROTETTI` (con lo stesso tetto di `ATTESA_SPEGNIMENTO_S`). Per le azioni `registra`
  cancellate allo spegnimento, mandare un solo SIGTERM al processo, senza SIGKILL. In alternativa,
  eseguire `ritenta_clonazione` come lavoro della coda.

## Note

- **NOTA-1 · K24 nella pratica.** Il test `test_lavori.py:282-328` cancella a mano la coroutine. Con
  uvicorn reale la disconnessione del browser **non** cancella l'handler di una risposta non in
  streaming. Verificato: `GET /stato` con `run_status.py` che dorme 4 s e client chiuso dopo
  0,8 s → il processo è ancora vivo 1 s dopo. K23 vale comunque (nessuna scrittura si ferma), ma
  "le letture si fermano" non succede. Per esempio, un `documents_list --name` (fino a 240 s)
  lanciato e abbandonato gira fino in fondo. Se serve, aggiungere un controllo di
  `request.is_disconnected()` in parallelo alle letture lunghe.
- **NOTA-2 · K1 "decodifica stretta".** `decodifica_header` (`unquote(errors="strict")`) rifiuta
  l'UTF-8 non valido ma lascia passare `%` malformati: `a%zzb.txt` viene salvato così
  (verificato). Passano anche U+2028/U+2029 (categorie Zl/Zp, non Cf) e i byte non ASCII
  grezzi nell'header (letti come latin-1). Non si esce dalla cartella e non c'è iniezione
  (Content-Disposition li ripulisce), ma il piano dice "stretto": rifiutare `%` non seguito da
  due cifre esadecimali, valori non ASCII e le categorie `Z*` diverse dallo spazio.
- **NOTA-3 · `X-LIL-Gettone` e il CMS.** `DEPLOY.md` §1 chiede al CMS di togliere ogni `X-LIL-*`
  del browser "a parte `X-LIL-Richiesta` e `X-LIL-Nome-File`". Con quella regola
  `X-LIL-Gettone` viene tolto e la sostituzione di un file non si può mai confermare in
  produzione. Aggiungerlo all'elenco in `DEPLOY.md`. Non dà identità: è un gettone casuale
  legato all'utente.
- **NOTA-4 · Esiti 3 dei lavori (K19).** `ConsentRequired` riceve un gettone che `/api/conferme`
  poi rifiuta (nessuna regola in tabella). È il comportamento voluto, perché la UI registra i
  consensi e rilancia, ma il gettone è inutile: si può evitare di crearlo. Il gettone di
  `CloneOutcomeUnknown` è legato a chi ha prodotto (di solito `approvatore`), che con i permessi
  di default non ha `voce_clonazione`. Il `gestore_voci` non può usarlo: in A3 la UI deve dire
  "chiedi a chi gestisce le voci", oppure il gettone va legato al permesso invece che
  all'utente.
- **NOTA-5 · Cartelle delle librerie.** `settings_show` dà solo `config.documents_dir.name`. Il
  controller cerca `AGENT_DIR/<nome>` (`nomi.cartella_libreria`). Se `config.yaml` mette le
  librerie fuori dalla cartella dell'agente, i caricamenti finiscono in una cartella che l'agente
  non legge, o falliscono con 404. Verificarlo all'avvio (la cartella esiste e non è un link) o
  documentarlo in `DEPLOY.md`.
- **NOTA-6 · I/O bloccante nel loop.** `os.fsync` di un caricamento da 20 MB, `os.scandir` e
  `Lettore.salta_fino_a` (fino a 20.000 righe rilette da zero a ogni polling) girano nel loop
  di asyncio, con un solo processo. Con molti client in polling la latenza sale per tutti, coda
  compresa. Valutare `anyio.to_thread.run_sync` per `fsync` e per la lettura iniziale degli
  eventi.
- **NOTA-7 · Azzeramento di `_eventi.jsonl` (K14).** Si riconosce solo se il file nuovo è più
  corto della posizione già letta. Se fra due letture una nuova produzione scrive più byte di
  quelli già letti, lo stream continua a metà riga (le righe spezzate si scartano) con id sfalsati.
  Oggi lo stream si chiude a fine lavoro, quindi il caso è raro. Un controllo dell'inode o del
  primo evento (`iniziata.at`) lo chiuderebbe.
- **NOTA-8 · Log.** Quando `LimiteCorpo` supera il limite, l'app vede `ClientDisconnect` e il
  middleware d'ingresso lo scrive come "errore imprevisto" con traceback (verificato). È solo
  rumore, ma un utente può riempire il log a piacere. Trattare `ClientDisconnect` come caso
  normale, senza traceback.

## Controlli K1-K24

| K | Stato | Note |
|---|---|---|
| K1 | parziale | regole su `/ \ ..`, controllo, Cf, `.`/`-`, spazi, 150 caratteri: sì; decodifica non stretta (NOTA-2); 150 caratteri possono superare 255 byte (BASSA-4) |
| K2 | sì | elenco documenti, `.json .lock .tmp` vietati |
| K3 | sì | `%PDF-`, `PK\x03\x04`, UTF-8 stretto e senza NUL su tutto il file (anche sotto i 16 byte); audio in A5 |
| K4 | sì sulla rotta di caricamento | `Content-Length` → 413 prima di leggere; conteggio prima di `os.write`; limite da `documenti.max_bytes`. L'esenzione octet-stream apre però le rotte JSON (MEDIA-3) |
| K5 | sì | temporaneo nascosto, `O_CREAT|O_EXCL|O_NOFOLLOW` 0600 via `dir_fd`, cartella `O_DIRECTORY|O_NOFOLLOW`, `fsync` di file e cartella, `rename`, pulizia in `finally` (anche su disconnessione e cancellazione) |
| K6 | parziale | gettone legato a utente, azione-libreria e chiave; rifiutato da `/api/conferme`; registro con nome, byte, sha256; ma toglie anche i sidecar con la stessa chiave (BASSA-1) |
| K7 | parziale | controllato solo prima di leggere il corpo (BASSA-2) |
| K8 | A5 | — |
| K9 | sì | `file_finale.nome` e `file[]` `interno: false`, più un elenco di file mai serviti; documenti solo da `documents_list` |
| K10 | sì | `dir_fd` con `O_NOFOLLOW`, file `O_NOFOLLOW`, solo `S_ISREG`, nome senza separatori né punto iniziale; un link → 404 |
| K11 | sì | un intervallo, 416 con `bytes */N`, sintassi non valida o più intervalli → 200, numeri di al più 15 cifre |
| K12 | sì | ASCII di ripiego senza `" \ ;` e non stampabili, `filename*`, `inline`/`attachment`, `nosniff`, permesso `leggere` (tramite l'azione `episodio`/`documenti`) |
| K13 | sì | `O_NOFOLLOW` su cartella e file, solo righe complete, 64 KB, 20.000 righe, `Pulitore` |
| K14 | sì (con NOTA-7) | `[0-9]{1,6}` fullmatch per `Last-Event-ID` e `dopo` |
| K15 | parziale | heartbeat, controllo della disconnessione a ogni ciclo, chiusura a fine lavoro o dopo 2 h, contatore che torna a zero (verificato con uvicorn); limite 5 invece di 4 (BASSA-3) |
| K16 | **no** | un lavoro per run e `CODA_MAX` aggirabili durante la stima (MEDIA-1) |
| K17 | sì | `controlla_azione` prima di accodare (permesso, `solo_da_gettone`, argomenti → 400/404/403), `--by` dall'ingresso |
| K18 | parziale | righe `accodato` ed esito con `lavoro`; manca l'esito sugli errori del controller e allo spegnimento (BASSA-6) |
| K19 | sì (con NOTA-4) | gettone dell'utente che ha lanciato il lavoro, visibile solo a lui (`Lavoro.vista`); `MusicChanged` → di nuovo in coda con permesso ricontrollato; `CloneOutcomeUnknown` → `ritenta_clonazione` |
| K20 | parziale | stima prima di accodare e costo dall'agente; se la stima fallisce si accoda senza costo (BASSA-7) |
| K21 | **parziale** | permesso dell'azione del lavoro, rimozione dalla coda, un solo SIGTERM al processo, "gia' richiesta", nessun SIGKILL alla produzione (verificato anche allo spegnimento: il processo sopravvive e finisce da sé); ma l'interruzione si perde se arriva mentre il processo parte (MEDIA-2) |
| K22 | sì | `Lavoro.vista` non mostra `istruzioni`; `in_corso.istruzione` arriva a tutti da `run_status`/`script_show`, come deciso |
| K23 | sì (con BASSA-8) | `asyncio.shield` + riferimento in `_PROTETTI` per le azioni `registra`; allo spegnimento vengono cancellate |
| K24 | parziale | nel codice le letture sono annullabili, ma in produzione nessuno le annulla (NOTA-1) |

## Verificato e a posto

1. **Path traversal**: ogni `{run}` passa da `controlla_run` (regex senza `/`, niente `..`,
   cartella reale non link), sia nelle azioni sia in `_run_valida` (eventi, interruzione); i
   `{nome}` dei documenti passano da `controlla_nome` e dall'elenco dell'agente; i nomi caricati
   da `nome_caricato`; le cartelle delle librerie da `cartella_libreria` (niente `/`, `.`, `..`) e
   si aprono con `O_NOFOLLOW`. Un `%2F` nel percorso non arriva al parametro (Starlette decodifica
   prima del routing).
2. **Sottoprocessi**: nessun nuovo punto di lancio; la coda usa `esecutore.riga_di_comando` e
   `avvia_processo` (lista, nuova sessione, ambiente minimo, posizionali dopo `--`).
3. **File non fidati**: caricamenti limitati in streaming sulla rotta dedicata; il contenuto dei
   documenti lo legge solo l'agente con i suoi limiti; `_eventi.jsonl` con righe e numero di righe
   limitati (resta MEDIA-3 per i corpi JSON).
4. **Testo nei prompt**: `istruzioni` di revisione e testi delle battute passano da
   `controlla_testo` (lunghezza, caratteri di controllo); il gate di approvazione resta
   nell'agente (sha256), nessun bypass nuovo.
5. **Retake**: non in A2 (tabella `fase="A5"`, `INDICAZIONI_RETAKE` già come whitelist).
6. **Segreti**: esiti dei lavori ed eventi SSE passano da `valuta`/`Pulitore`; le risposte di
   caricamento e coda non hanno percorsi; gli `OSError` finiscono nel 500 generico; il gettone
   dei lavori arriva solo a chi li ha lanciati (`Lavoro.vista`, SSE `lavoro`, `/api/lavori/{id}`).
7. **Lock e temporanei**: temporaneo nascosto 0600 con `O_EXCL|O_NOFOLLOW` e pulizia in ogni uscita;
   ricontrollo della collisione e rename senza `await` in mezzo (nessuna corsa fra due
   caricamenti nello stesso processo); i `.lock` sono esclusi dal download.
8. **Garanzie di dominio**: i flag di conferma arrivano solo da `regola.flag` di un gettone
   (`verifica_tabella` invariata); `ritenta_clonazione` è `solo_da_gettone` e la coda accoda sempre
   con `da_gettone=False`; nessun retry automatico della clonazione; nessun SIGKILL alla
   produzione (verificato allo spegnimento con un processo che sopravvive ai 120 s).
9. **Firma e ingresso**: tutte le rotte nuove stanno sotto il middleware d'ingresso (solo
   `/salute` è esclusa); nessuna decide identità o permessi da header del browser (`Range`,
   `Last-Event-ID`, `X-LIL-Nome-File`, `X-LIL-Gettone` sono dati, il gettone è legato all'utente
   dell'ingresso); POST/PUT/PATCH/DELETE con `X-LIL-Richiesta: 1`.
10. **Gettoni**: i gettoni `FileEsistente` (azione `sostituisci_documento`, fuori tabella) sono
    rifiutati da `/api/conferme`; un gettone di tabella non vale per il caricamento (controllo su
    `azione` e `chiave`); le conferme che rilanciano un lavoro passano da `accoda` e quindi da
    `controlla_azione` (permesso ricontrollato al momento della conferma); gli esiti 3 dei lavori
    hanno il gettone dell'utente che li ha lanciati.
11. **Permessi**: archivio, viste, eventi (SSE e polling), elenco e dettaglio dei lavori, download
    → `leggere`; caricamento → `documento_carica` prima di leggere nome e corpo; stesura e revisione
    → `copione_scrivi`; produzione → `produrre`; interruzione → permesso dell'azione del lavoro
    (anche per lavori di altri, come da piano); consenso → `voce_consenso`; clonazione →
    `voce_clonazione`; gli altri utenti vedono di un lavoro solo `ok`/`esito`/`error_type`.
    Coerente con "Autorizzazioni" del contratto.
12. **Download**: vedi K9-K12 (tutti rispettati).

## Da verificare nelle prossime fasi

- **A4, systemd e spegnimento**: con `KillMode=control-group` (default) systemd manda il proprio
  SIGTERM a **tutti** i processi del gruppo, quindi anche alla produzione (un secondo SIGTERM
  oltre a quello del controller). Dopo `TimeoutStopSec` manda SIGKILL a tutti, produzione
  compresa. Serve `KillMode=mixed` o `process` (o l'agente in uno scope separato) e un
  `TimeoutStopSec` maggiore di 30 s (uvicorn) + 120 s (`ATTESA_SPEGNIMENTO_S`). Da provare sul
  VPS.
- **A5, caricamento di voci e brani**: correggere prima BASSA-1 (mai togliere il `.json` del
  consenso o dei metadati) e BASSA-2 (K7 al momento del rename: una voce non si sostituisce durante
  la `preparazione_voci` di una produzione che la usa); firme audio (K3) contro `ffconcat` e
  playlist; consenso `superato` dopo la sostituzione (K8); i gettoni `FileEsistente` delle tre
  librerie devono avere azioni distinte (oggi la libreria è implicita nel nome dell'azione, e
  `details.libreria` non viene ricontrollato).
- **A5, download di campioni e anteprime**: mai i `.json` accanto a voci e brani; registrazioni
  solo agli autenticati.
- **A3, interfaccia**: polling come ripiego al 429; messaggio per il gettone di
  `CloneOutcomeUnknown` (NOTA-4); i PDF caricati vengono serviti `inline` dalla stessa origine:
  tenere la CSP attuale anche sulle risposte di download (oggi la mette il middleware).
