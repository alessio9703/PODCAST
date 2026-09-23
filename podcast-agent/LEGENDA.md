# LEGENDA — a cosa serve ogni file

Mappa di manutenzione del progetto. Per ogni file: **cosa fa**, **cosa espone**,
**quando ci metti le mani** e **cosa rischi di rompere**.

Regola d'oro del progetto: *nessun file, a parte
`src/providers/fish_audio_provider.py`, sa che esiste Fish Audio.* Tutti gli
altri parlano solo con l'interfaccia astratta `VoiceProvider`. Se ti accorgi di
scrivere `fish` da qualche altra parte, stai rompendo l'architettura.

---

## Colpo d'occhio

```
podcast-agent/
├── run.py ......................... CLI classica: orchestra i 9 passi con input()
├── .claude/agents/podcast-agent.md  subagent Claude Code: stessi 9 passi, ma in conversazione
├── config.yaml .................... TUTTE le manopole (provider attivo, percorsi, audio, script)
├── prompts/copione.md ............. regole di scrittura del copione (fonte unica)
├── prompts/lezioni.md ............. lezioni approvate dagli episodi precedenti (SOLO via --accept, vedi sotto)
├── .env ........................... solo le API key (non committare)
├── requirements.txt ............... dipendenze
├── README.md ...................... come si usa + nota etica sul consenso
├── LEGENDA.md ..................... questo file
│
├── voices_registry.json ............ cache delle voci clonate (creato a runtime)
├── documenti/ ..................... input: i documenti di partenza (PDF, DOCX, TXT, MD)
├── voci_disponibili/ .............. input: le registrazioni "Nome Cognome.wav"
├── musiche/ ....................... input OPZIONALE: intro/, outro/, sottofondo/
├── output/ ........................ un episodio per sottocartella
│                                   (dentro: _tempi_turni.json con misure e
│                                   posizione di ogni turno NEL MASTER DI SOLA
│                                   VOCE, segmenti/ con un file audio per
│                                   battuta — la cache — e, se ci sono
│                                   musiche, musiche.json/musiche_usate.json)
│
├── src/
│   ├── voice_provider.py .......... CONTRATTO: interfaccia astratta + errori tipizzati
│   ├── providers/
│   │   └── fish_audio_provider.py . unico file che conosce le API di Fish Audio
│   ├── config.py .................. legge config.yaml e istanzia il provider dinamicamente
│   ├── ffmpeg_tool.py .............. UNICO punto che cerca l'eseguibile ffmpeg
│   ├── pdf_reader.py .............. documento -> testo
│   ├── voice_library.py ........... cartella voci -> elenco + risoluzione dei nomi
│   ├── music_library.py ........... cartella musiche -> elenco + risoluzione dei nomi
│   ├── script_writer.py ........... testo -> conversazione.json (via Claude)
│   ├── registry.py ................ cache anti-riclonazione (hash sha256)
│   ├── script_session.py .......... stato delle revisioni su disco (serve al subagent)
│   ├── run_layout.py .............. struttura della cartella di output (condivisa)
│   ├── cost_estimate.py ........... caratteri / durata (INTERVALLO) / costo prima di spendere
│   ├── audio_assembler.py ......... cache dei segmenti + sintesi + montaggio voce + export mp3
│   ├── music_mixer.py ............. mix di intro/outro/sottofondo — UNICO modulo che parla ffmpeg per il mix
│   ├── segment_cache.py ........... rilettura della cache di una run gia' prodotta (retake, mix)
│   ├── episode_timeline.py ........ dal minutaggio dell'episodio alla battuta
│   ├── retake.py .................. nuovi take di una battuta, a parita' di testo
│   ├── episode_signals.py ......... segnali per le lezioni (deterministico, gira dopo ogni sintesi)
│   └── lessons.py .................. aggregazione + proposta (modello) + approvazione (SOLO l'utente)
│
├── tools/ ......................... entry-point CLI: argparse + import, ZERO logica
│   ├── _common.py ................. JSON su stdout, exit code tipizzati
│   ├── _approval.py ............... il gate: niente spesa senza il si' dell'utente
│   ├── doctor.py ................... diagnosi dell'installazione (Python, dipendenze, ffmpeg)
│   └── (altri tool) ................ uno per passo, piu' turn_at.py/retake.py/mix.py
│                                   per correggere una battuta o le musiche di un episodio gia' montato
│
└── tests/
    ├── fake_provider.py ........... provider finto (toni sinusoidali) — anche da esempio
    ├── test_smoke.py .............. 51 controlli sui moduli src/
    ├── test_tools_cli.py .......... 171 controlli sui CLI del subagent
    ├── test_music.py .............. controlli su intro/outro/sottofondo (skip esplicito se manca ffmpeg)
    └── test_lessons.py ............ segnali e lezioni: mai sui file veri di prompts/
```

Come si incastrano (le frecce vanno da "chi chiama" a "chi viene chiamato"):

Due modi di guidare la stessa logica:

```
run.py  (CLI, input())          podcast-agent.md  (subagent, conversazione)
        │                                │
        │                                └─> tools/*.py  (argparse + import)
        └────────────┬───────────────────────────┘
                     ▼
                   src/*.py   ← la logica sta SOLO qui
```

```
run.py
 ├─> config.py ──────> voice_provider.py  (istanzia il provider giusto)
 │                        ▲
 │                        └── fish_audio_provider.py   (implementazione)
 ├─> pdf_reader.py
 ├─> voice_library.py
 ├─> script_writer.py ─> [API Claude]
 ├─> registry.py
 ├─> cost_estimate.py ─> script_writer.PodcastScript + voice_provider.ProviderPricing
 ├─> audio_assembler.py ─> il provider (via interfaccia), mai direttamente Fish Audio
 └─> run_layout.py ────> struttura di output/<data>_<titolo>/
```

---

## File di configurazione

### `config.yaml`
**Cosa fa:** contiene ogni parametro modificabile senza toccare il codice.
Quattro sezioni: `active_provider` + `providers` (motore vocale), `paths`
(cartelle), `audio` (pause, bitrate, estensioni accettate), `script` (modello
Claude, lingua, durata target).

**Ci metti mano quando:** cambi provider vocale, sposti una cartella, vuoi pause
più lunghe, vuoi episodi più corti, cambi modello Claude.

**Attenzione:** `providers.<nome>.class` è nella forma `modulo:Classe` e viene
importata dinamicamente. Se sbagli il percorso l'errore arriva solo a runtime,
al passo 6 (dopo che hai già approvato lo script).

### `.env` / `.env.example`
**Cosa fa:** le sole due API key. `.env` è in `.gitignore`; `.env.example` è il
modello da copiare.

**Ci metti mano quando:** ruoti una chiave o aggiungi un provider nuovo (serve
una nuova variabile, il cui nome dichiari in `api_key_env`).

**Attenzione:** il caricamento (`load_dotenv` in `src/config.py`) **non
sovrascrive** variabili già presenti nell'ambiente. Se hai un
`ANTHROPIC_API_KEY` esportato nella shell, quello vince sul `.env`.

### `requirements.txt`
Dipendenze obbligatorie, con versione fissata (`==`): `anthropic`, `requests`,
`PyYAML`, `pypdf`, `imageio-ffmpeg` (porta un eseguibile ffmpeg pronto per
macOS/Windows/Linux — vedi `src/ffmpeg_tool.py`). Opzionali commentate:
`python-docx` per i .docx, `lameenc` come encoder mp3 di riserva ESTREMA,
usato solo se ne' `imageio-ffmpeg` ne' un ffmpeg di sistema si trovano.

---

## Il cuore: il contratto con il provider vocale

### `src/voice_provider.py` — **il file più importante da non rompere**
**Cosa fa:** definisce l'interfaccia che ogni motore vocale deve rispettare, e
la gerarchia di errori che l'agente sa interpretare.

**Espone:**

| Simbolo | Ruolo |
|---|---|
| `VoiceProvider` (ABC) | i 3 metodi obbligatori: `list_cloned_voices()`, `clone_voice(name, audio_path)`, `text_to_speech(text, voice_id)` |
| `VoiceProvider.pricing()` | opzionale, default "costo sconosciuto" |
| `VoiceProvider.synthesis_fingerprint()` | opzionale, default `{}`: cosa, oltre a testo e voce, determina l'audio. Entra nella chiave della cache dei segmenti |
| `VoiceProvider.audio_format` | `"wav"` o `"mp3"`: dice al montatore quale strategia usare |
| `VoiceInfo` | una voce clonata (id, name, provider, created_at) |
| `ProviderPricing` | tariffa per la stima |
| `VoiceProviderError` | radice di tutti gli errori del provider |
| `ProviderAuthError` | chiave mancante/non valida |
| `ProviderQuotaError` | credito esaurito (HTTP 402) |
| `ProviderRateLimitError` | rate limit (HTTP 429) |
| `ProviderAudioQualityError` | audio di riferimento rifiutato |
| `ProviderTimeoutError` | provider che non risponde |

**Ci metti mano quando:** vuoi aggiungere una capacità a *tutti* i provider
(es. `delete_voice()`). Cambiare una firma qui obbliga a modificare ogni
implementazione.

**Attenzione su `synthesis_fingerprint()`:** un provider che dichiara meno di
quello che conta si porta dietro un bug silenzioso. Se un parametro cambia
l'audio ma non e' nell'impronta, cambiarlo in `config.yaml` non si sente
nell'episodio: la cache considera i segmenti ancora buoni e il montaggio
rimette dentro l'audio di prima. Nel dubbio, dichiarare in piu' costa una
risintesi; dichiarare in meno costa un pomeriggio a chiedersi perche' non
cambia niente. Non ci finisce mai dentro la API key.

**Attenzione:** le classi d'errore non sono decorative — `run.py` e
`audio_assembler.py` fanno `except VoiceProviderError` per fermarsi con un
messaggio utile invece di esplodere a metà montaggio. Un provider nuovo che
solleva `requests.HTTPError` grezzi bypassa tutta questa rete di sicurezza.

### `src/providers/fish_audio_provider.py`
**Cosa fa:** l'unica implementazione concreta. Traduce i 3 metodi astratti nelle
chiamate HTTP di Fish Audio e mappa i codici di stato sugli errori tipizzati.

| Metodo dell'interfaccia | Endpoint Fish Audio |
|---|---|
| `list_cloned_voices()` | `GET /model?self=true` (paginato) |
| `clone_voice()` | `POST /model` multipart, `train_mode=fast`, `visibility=private` |
| `text_to_speech()` | `POST /v1/tts` con header `model:` e body JSON |

Contiene anche: retry con exponential backoff sui codici 429/5xx (rispettando
`retry-after`), e `build(options)` — la factory che `config.py` chiama per
leggere la chiave dall'ambiente.

I parametri di sintesi arrivano da `providers.fish_audio.options.tts` in `config.yaml` e
finiscono tal quali nel body (`normalize`, `speed`, `volume`, `temperature`, `top_p`,
`repetition_penalty`). La lista ammessa è `_TTS_PARAMS`: una chiave fuori lista fa fallire
la costruzione del provider con un messaggio esplicito, invece di essere ignorata in
silenzio da Fish Audio. Per aggiungerne una nuova, la aggiungi lì **e** in `config.yaml`.

**`normalize` e le parentesi tonde.** Oggi è `true`: normalizza numeri, date e sigle prima
di leggerli. Le inline tag fra parentesi **quadre** non sono in conflitto e passano
comunque. Se in futuro userai anche il paralinguaggio fra parentesi **tonde** (pause,
respiri), su quei turni `normalize` va disattivato — e siccome oggi il valore è uno solo
per tutto l'episodio, servirà un override per turno: il punto dove aggiungerlo è
`text_to_speech()`, non `config.yaml`.

**Ci metti mano quando:** Fish Audio cambia le sue API, o vuoi esporre un
parametro TTS nuovo (temperatura, prosodia, `chunk_length`).

**Attenzione:** la clonazione **non** viene ritentata automaticamente
(`retry=False`): un retry cieco creerebbe voci duplicate sul provider, che poi
paghi. È voluto.

---

## Infrastruttura

### `src/config.py`
**Cosa fa:** legge `config.yaml`, espone i valori in modo tipizzato, e soprattutto
**istanzia il provider attivo senza che nessuno sappia quale sia** (`importlib`
sulla stringa `modulo:Classe`).

**Espone:** `load_config()`, `load_dotenv()`, `Config` (con `.documents_dir`,
`.voices_dir`, `.registry_file`, `.output_dir`, `.audio`, `.script`,
`.accepted_extensions`, `.build_provider()`, `.anthropic_api_key()`),
`ConfigError`, `PROJECT_ROOT`.

**Ci metti mano quando:** aggiungi una sezione a `config.yaml` e vuoi un accesso
comodo, o vuoi validare la config più a fondo.

**Attenzione:** `build_provider()` verifica con `isinstance(provider,
VoiceProvider)` — è il controllo che impedisce di collegare una classe che non
rispetta il contratto. Non toglierlo.

---

## I passi del flusso (un file per passo)

### `src/pdf_reader.py` — passo 1
**Cosa fa:** documento → testo. PDF (`pypdf`), DOCX (`python-docx`, opzionale),
txt/md/rst.

**Espone:** `extract_text(path, search_dir=None) -> Document`, `Document`
(`.text`, `.kind`, `.pages`, `.char_count`, `.word_count`, `.truncated(max)`),
`DocumentError`.

**`search_dir`** è la cartella `documenti/` (`paths.documents_dir`): un `path`
che è **solo un nome di file** viene cercato prima lì dentro, poi come percorso
relativo. La ricerca sta qui e **solo** qui: `run.py` e `tools/doc_extract.py`
la ereditano passando `config.documents_dir`, così CLI e subagent si comportano
allo stesso modo. Se la duplichi in uno dei due, prima o poi divergeranno.

**Ci metti mano quando:** vuoi supportare un formato nuovo (HTML, EPUB) o
aggiungere OCR.

**Attenzione:** i PDF scansionati (solo immagini) sollevano `DocumentError` con
un messaggio esplicito invece di restituire stringa vuota — è un caso reale e
frequente, non farlo degradare a silenzio.

### `src/voice_library.py` — passi 2-3
**Cosa fa:** scansiona `voci_disponibili/`, separa i file audio validi da quelli
in formato ignoto, e risolve i nomi digitati dall'utente.

**Espone:** `VoiceLibrary` (`.scan()`, `.voices`, `.names`, `.rejected_files`,
`.require(n)`, `.resolve(query)`), `AvailableVoice`, `VoiceLibraryError`.

**Ci metti mano quando:** vuoi cambiare la convenzione dei nomi file, o la
tolleranza ai typo.

**Attenzione:** `resolve()` ritorna `(match, candidati)` e restituisce un match
**solo** quando è certo. Su somiglianze parziali ritorna `(None, candidati)`
apposta, così `run.py` è costretto a chiedere conferma. Non "semplificarlo"
facendogli ritornare il candidato migliore: l'agente comincerebbe a indovinare
i nomi delle persone.

### `src/script_writer.py` — passi 4-5
**Cosa fa:** chiama Claude per scrivere il copione e per revisionarlo,
mantenendo la conversazione così che le revisioni abbiano contesto.

**Espone:** `ScriptWriter` (`.write(document, speakers)`, `.revise(istruzioni)`, e per
il percorso subagent `.build_prompt()`, `.run(prompt)`, `.run_messages(messages)`),
`PodcastScript` (`.titolo`, `.turni`, `.speakers`, `.total_chars`,
`.total_words`, `.validate(speakers)`, `.render()`, `.save(path)`),
`ScriptWriterError`. Contiene anche `SYSTEM_PROMPT` e `_SCHEMA`.

**Ci metti mano quando:** vuoi cambiare lo stile del podcast (→ `SYSTEM_PROMPT`),
la struttura del JSON (→ `_SCHEMA` **e** `PodcastScript`), o il modello.

**Espressività — le due leve.** La naturalezza della dizione si governa da due parti, e
conviene sapere quale stai muovendo:

| Leva | Dove | Cosa cambia |
|---|---|---|
| **inline tag** `[esitante]`, `[ride]`, `[enfasi]` | dentro `testo`, scritte da chi fa il copione (`prompts/copione.md`) | l'intenzione **di quel punto lì** |
| parametri di sintesi (`temperature`, `speed`, `repetition_penalty`, …) | `providers.fish_audio.options.tts` in `config.yaml` | la resa **di tutto l'episodio** |

Le tag sono testo normale a tutti gli effetti: `PodcastScript.from_dict()` non le tocca
(fa solo `.strip()` sui bordi) e `validate()` non le guarda nemmeno — per lui un turno con
le tag è un turno con del testo dentro. Nessun punto della pipeline le rimuove: arrivano
intatte fino al body di `POST /v1/tts`, dove è il modello vocale a interpretarle. Se un
giorno vuoi *contarle* o *vietarle*, il posto è `_validate()` in `tools/script_save.py`,
non qui.

**Attenzione:**
- `_SCHEMA` è passato come structured output all'API: la risposta è JSON valido
  per costruzione. Se lo modifichi, aggiorna `PodcastScript.from_dict()` in
  parallelo o i campi nuovi verranno scartati in silenzio.
- `stop_reason == "max_tokens"` viene trattato come errore, non come successo:
  uno script troncato a metà frase non deve arrivare alla sintesi.
- `.revise()` accumula messaggi in memoria: e' il percorso di `run.py`, e una decina
  di revisioni sullo stesso episodio costano progressivamente di piu' in token di
  input. Il subagent usa invece `run_messages()` con la conversazione ricostruita da
  `src/script_session.py`, che non ha questo problema.

### `src/registry.py` — passo 6
**Cosa fa:** gestisce `voices_registry.json`. È quello che ti fa risparmiare
clonazioni (e credito).

**Espone:** `VoiceRegistry` (`.lookup(name, path, provider)`, `.record(...)`,
`.get(name)`), `RegistryEntry`, `file_hash(path)`.

**La logica in una riga:** `lookup()` ritorna `(voice_id | None, motivo)` — il
`voice_id` c'è solo se nome, hash sha256 del file **e** provider coincidono.

| Situazione | Esito |
|---|---|
| nome + hash + provider identici | riusa il `voice_id` |
| nome assente | clona |
| hash diverso (registrazione sostituita) | riclona |
| stesso nome, provider diverso | riclona |

**Ci metti mano quando:** aggiungi campi al registro (lingua, durata del
campione, note).

**Attenzione:** il salvataggio è atomico (scrive `.tmp` e poi `replace`) per non
corrompere il registro se interrompi a metà. Il `motivo` ritornato viene
mostrato all'utente: tienilo leggibile in italiano.

### `src/cost_estimate.py` — passo 7
**Cosa fa:** conta caratteri e parole, stima la durata (~150 parole/minuto più le
pause) e, se il provider dichiara una tariffa, il costo.

**Espone:** `estimate(script, pricing, ...) -> Estimate`, `Estimate.render()`.

**Ci metti mano quando:** vuoi una tariffazione al minuto invece che a
carattere, o una stima più precisa.

**Attenzione:** `words_per_minute` vive in due posti con due scopi diversi —
`script.words_per_minute` (quanto testo chiedere a Claude) e la stessa chiave
usata qui per la durata. Se li disallinei, la durata stimata smette di
corrispondere a quella richiesta.

### `src/audio_assembler.py` — passi 8-9
**Cosa fa:** il pezzo più delicato. Sintetizza un segmento per turno, li concatena
con le pause, esporta in mp3.

**Espone:** `AudioAssembler` (`.from_config()`, `.check_voice_coverage()`,
`.synthesize()`, `.assemble()`, `.preview()`), `AssemblyResult`,
`SynthesisResult`, `AssemblyError`, `MissingSpeakerVoice`, `slugify()`, e le
primitive della cache: `segment_key()`, `script_segment_keys()`,
`segment_path()`, `existing_takes()`, `load_choices()`, `save_choices()`,
`write_segment_index()`, `segment_rms()`.

**La cache dei segmenti.** Il nome di un segmento non e' la sua posizione nel
copione ma una chiave di contenuto: `<sha256 troncato>_t<take>.<ext>`. Nella
chiave entrano testo, `voice_id`, `provider.name`, `provider.audio_format`,
`provider.synthesis_fingerprint()` e il numero di **occorrenza** (la n-esima
volta che quella voce dice esattamente quel testo).

Conseguenze, tutte volute:

- `synthesize()` e' incrementale: se il file della chiave c'e' gia', non chiama
  il TTS. Correggere una battuta costa quella battuta, e — cosa che conta di
  piu' — **non rimette in gioco le altre**, visto che il TTS non e'
  deterministico;
- cambiare modello o parametri di sintesi invalida la cache da sola, perche'
  l'impronta del provider e' dentro la chiave;
- l'occorrenza tiene distinte due battute identiche ("Esatto.", "Si'."): lo
  stesso audio ripetuto suona registrato, e un retake sull'una non deve toccare
  l'altra. Contare le occorrenze e non le posizioni e' anche cio' che fa reggere
  la cache quando si inserisce o si toglie un turno diverso;
- la scrittura passa da un `.tmp` e poi `replace`: "il file esiste" significa
  "il segmento e' valido", quindi un crash non deve poter lasciare un wav
  troncato che verrebbe riusato per sempre.

`scelte.json` (`{chiave: take}`, default 1) dice quale take montare;
`indice.json` e' una vista derivata leggibile, rigenerata a ogni sintesi e
cancellabile senza perdere niente. Lo schema del copione **non cambia**: nessun
id e nessun campo extra nei turni, perche' `PodcastScript.from_dict()` li
scarterebbe e `script_save.py` li rifiuterebbe.

**La timeline.** `AssemblyResult.turn_timeline` dice dove cade ogni turno dentro
il master: inizio del turno *i* = lead-in + durate ritagliate dei turni
precedenti + (i-1) pause. Si calcola qui, non in `tools/`, perche' e' una
proprieta' del montaggio: chi cambia le pause cambia la timeline. Finisce in
`_tempi_turni.json` e la legge `src/episode_timeline.py`. **Sul percorso mp3 non
esiste**, e il montaggio lo dice esplicitamente fra i warning.

**Due strade, scelte da `provider.audio_format`:**

| Formato | Come monta | Pause |
|---|---|---|
| `wav` | modulo standard `wave`, poi mp3 con ffmpeg → `lameenc` → (se mancano entrambi) resta wav + avviso | sì, al millisecondo |
| `mp3` | concatena i byte, togliendo eventuali tag ID3v2 | **no** |

**Ci metti mano quando:** vuoi musica di sottofondo, sigla, normalizzazione del
volume, crossfade.

**Attenzione:**
- `check_voice_coverage()` va chiamato **prima** di `synthesize()`: è il blocco
  che impedisce di accorgersi a metà sintesi che a uno speaker manca la voce.
  `run.py` lo chiama due volte apposta (una esplicita, una dentro `synthesize`).
- Se i segmenti hanno sample rate o numero di canali diversi, `assemble()`
  solleva un errore invece di concatenarli: non c'è ricampionamento, e unirli
  produrrebbe audio accelerato o distorto.
- Se la sintesi si interrompe, l'errore dice **a quale turno** e dove sono i
  segmenti già scaricati: rilanciando lo stesso comando si pagano solo i turni
  mancanti.
- `preview()` monta alcuni segmenti in un wav senza codificare: serve a
  riascoltare un take nel suo contesto, con le stesse pause e lo stesso
  ritaglio dell'episodio. Passa dallo stesso `_concat_wav()` del montaggio
  **apposta**: un'anteprima montata diversamente farebbe giudicare un audio che
  non è quello che finirà dentro.
- `from_config()` traduce la sezione `audio:` in un assembler. Esiste perché i
  valori di default, ripetuti in `run.py`, `synthesize.py` e `retake.py`, prima
  o poi divergevano.

### `src/script_session.py` — stato delle revisioni (solo percorso subagent)
**Cosa fa:** persiste su disco quello che `run.py` tiene in memoria. Con la CLI la
conversazione con Claude vive per tutta la sessione; il subagent invece chiama scrittura e
revisione come **processi separati**, e fra l'uno e l'altro la memoria non c'e' piu'.

**Espone:** `ScriptSession` (`.create()`, `.load()`, `.save()`, `.record()`,
`.seed_messages()`, `.script()`, `.approve()`, `.approval_problem(script)`),
`script_hash()`, `ScriptSessionError`, `FILENAME`.

**Due responsabilita':**

1. **Storico.** `versions[]` tiene ogni bozza per intero con l'istruzione che l'ha
   generata e il suo sha256. Su disco costa zero.
2. **Approvazione.** `approved` registra `{version, sha256, at}` del copione che
   l'utente ha approvato; `approval_problem(script)` confronta quell'hash con il
   copione che sta per essere usato.

**La scelta di progetto:** la sessione **non** e' un dump della conversazione. Tiene il
compito iniziale, **solo l'ultimo copione** e l'elenco delle istruzioni gia' date;
`seed_messages()` ricostruisce i tre messaggi a ogni chiamata. Cosi' il contesto resta (il
modello sa cosa gli hai gia' chiesto) ma le bozze scartate non tornano in contesto, quindi
il costo non cresce a ogni revisione.

**Attenzione — l'invariante da non rompere:** `seed_messages()` usa **solo**
`current_script`, mai `versions[]`. Lo storico sta su disco perche' li' e' gratis;
rimetterlo in contesto farebbe ripagare ogni bozza scartata a ogni revisione del
percorso API. Stessa ragione per cui non ci si salva l'intera conversazione.

### `src/run_layout.py` — struttura della cartella di output
**Cosa fa:** convenzione di nomi e contenuto di `output/<data>_<slug>/`. Estratto da
`run.py` per essere condiviso con `tools/run_init.py` e `tools/run_finalize.py`.

**Espone:** `make_run_dir()`, `write_voices_used()`, `archive_source_document()`.

**Ci metti mano quando:** vuoi altri file nella cartella dell'episodio, o cambiare lo
schema del nome.

**Attenzione:** e' condiviso fra CLI e subagent apposta. Se duplichi una di queste
funzioni in `tools/`, i due percorsi divergono e le cartelle smettono di essere
confrontabili.

---

### `src/episode_timeline.py` — dal minutaggio alla battuta
**Cosa fa:** legge la timeline scritta dal montaggio in `_tempi_turni.json` e
risponde alla domanda "quale battuta si sente al 3:42?".

**Espone:** `parse_timecode()` (accetta `3:42`, `1:02:07` o i secondi),
`load_timeline()`, `attach_texts()`, `turn_at()`, `TurnLocation`,
`TimelineError`.

**Perche' esiste:** l'utente non dice "il turno 17", dice "al tre e quaranta
suona male". Qui non si ricalcola nessun tempo: i tempi li scrive il montaggio,
e due formule sulle stesse pause prima o poi divergono.

**Attenzione:**
- se l'istante cade in una pausa, `turn_at()` restituisce il turno **piu'
  vicino** con `in_pausa: true` e la distanza. Non inventa un turno che non c'e'
  e non sceglie in silenzio: chi chiama deve poter chiedere conferma;
- il testo dei turni NON e' dentro `_tempi_turni.json` di proposito — sarebbe
  una seconda copia del copione. `attach_texts()` lo accoppia leggendo
  `conversazione.json`, e se i conteggi non tornano lo dice invece di mostrare
  la battuta sbagliata;
- gli episodi montati prima di questa funzione hanno il file ma non gli offset:
  il caso e' previsto e produce un errore che dice di rimontare (a costo zero,
  se i segmenti sono in cache).

### `src/segment_cache.py` — caricamento della cache di una run gia' prodotta
**Cosa fa:** legge `conversazione.json` e `voci.json` **della run**, ricalcola
le chiavi dei segmenti e offre gli accessi per turno. Estratto da
`src/retake.py` quando anche `tools/mix.py` (rimontare la voce dalla cache per
rifare il mix) ha iniziato ad averne bisogno: prima viveva solo li'.

**Espone:** `RunCache` (`.turn()`, `.key()`, `.takes()`, `.chosen()`,
`.path()`, `.missing_turns()`, `.ordered_segments()`, `.any_segment_on_disk()`),
`SegmentCacheError`, `SCRIPT_FILENAME`, `VOICES_FILENAME`.

**Attenzione:** `any_segment_on_disk()` esiste per distinguere due cause
diverse quando `missing_turns()` non e' vuoto: cartella `segmenti/` vuota o
assente (episodio vecchio, o `--purge-segments`) contro segmenti presenti ma
che non combaciano con le chiavi attuali (l'impronta di sintesi e' cambiata in
`config.yaml` dopo l'ultima sintesi). `tools/mix.py` usa questa distinzione per
dare un messaggio diverso nei due casi.

### `src/retake.py` — rifare una battuta a parita' di testo
**Cosa fa:** genera nuovi take di un singolo turno, ne monta le anteprime in
contesto e registra quale take va usato nel montaggio.

**Espone:** `estimate_retake()`, `generate_takes()`, `verify_voice()`,
`choose_take()`, `RetakeError` (sottoclasse di `SegmentCacheError`). Ri-esporta
anche `RunCache`/`SCRIPT_FILENAME`/`VOICES_FILENAME` da `segment_cache.py` per
compatibilita' con chi importava da qui.

**Attenzione — le tre regole che non vanno allentate:**
- il `voice_id` si prende da `voci.json` **della run**, mai dal registro. Se la
  voce fosse stata riclonata nel frattempo, il timbro cambierebbe a meta'
  episodio, e proprio sulla battuta che si sta correggendo;
- se quel `voice_id` non esiste piu' sul provider ci si ferma e **non si
  riclona**. E' la stessa regola di `voices_prepare.py`, per la stessa ragione:
  una clonazione non richiesta costa e cambia il risultato senza che nessuno
  l'abbia deciso;
- i take esistenti non si sovrascrivono mai: i numeri nuovi partono dal primo
  libero, cosi' si puo' tornare su un take di prima dopo averli riascoltati.

La scelta fra i take resta all'utente. Il modulo misura quello che si puo'
misurare — uno scarto di livello oltre `RMS_WARNING_DB` (3 dB) rispetto agli
altri segmenti dello stesso speaker — e si ferma li': "suona giusto" non e' una
grandezza.

### `src/episode_signals.py` — segnali per le lezioni (raccolta, deterministica)
**Cosa fa:** legge `_script_session.json`, `segmenti/scelte.json` e
`_sintesi_log.json` di una run gia' sintetizzata e ne estrae i segnali su come
e' andata la scrittura: revisioni, turni cambiati fra bozza e approvato (con
`align_turns()`, per SOMIGLIANZA TESTUALE e mai per posizione — un turno tolto
o aggiunto sposta tutti gli indici dopo), tag tolte/aggiunte/sostituite con la
loro posizione, turni rifatti e take scelto, correzioni arrivate DOPO che
l'episodio era gia' stato sintetizzato (il segnale piu' forte). Scrive
`output/<run>/_segnali.json`. Nessuna chiamata a modelli, nessun costo.

**Espone:** `align_turns()`, `TurnChange`, `extract_tags()`, `diff_tags()`,
`is_question()`, `record_synthesis_event()`, `load_synthesis_log()`,
`post_listen_signals()`, `retake_signals()`, `episode_indicators()`,
`collect_signals()`, `collect_and_write()`, `SignalsError`,
`SCHEMA_VERSION`.

**Attenzione:**
- **"non disponibile" e "zero" sono due stati diversi**, e il modulo non li
  confonde mai: `retake_signals()` ritorna `disponibile: false` con un motivo
  se i segmenti sono stati cancellati (`--purge-segments`) o l'episodio non e'
  mai stato sintetizzato, e `disponibile: true, turni: []` solo se davvero non
  c'e' stato nessun retake. Confonderli nell'aggregazione (`src/lessons.py`)
  farebbe sembrare "senza problemi" un episodio di cui in realta' non si sa
  niente.
- **"corretto dopo l'ascolto" e' un FATTO registrato, non dedotto da un
  mtime.** Un mtime cambia copiando la cartella, rimontando o ripristinando un
  backup; `record_synthesis_event()` (chiamata da `tools/synthesize.py` a ogni
  montaggio riuscito) scrive invece un log append-only con lo sha256 del
  copione appena sintetizzato. `post_listen_signals()` confronta ogni versione
  della sessione con l'evento di sintesi piu' vicino PRIMA di lei: se non ce
  n'e' nessuno, e' una revisione qualunque, non una correzione post-ascolto.
- il file porta uno `schema_version`: `src/lessons.py` scarta gli episodi con
  una versione diversa invece di aggregarli alla cieca.

### `src/lessons.py` — aggregazione, proposta, approvazione delle lezioni
**Cosa fa:** tre livelli di fiducia diversi nello stesso modulo.
`aggregate_evidence()` e' deterministica (nessun modello): raggruppa i
`_segnali.json` di piu' episodi negli schemi ricorrenti, secondo un
**vocabolario chiuso** di categorie (`PATTERN_CATEGORIES`) condiviso da
aggregazione, prompt di proposta e validazione — una categoria fuori
vocabolario non sarebbe mai piu' verificabile da `--review`. `propose()`
chiama un modello ma solo sulle prove aggregate (mai sui copioni interi), a
`temperature=0`, e ogni lezione restituita deve puntare a uno schema che
l'aggregazione ha davvero trovato sopra soglia. `accept()` e' l'**unica**
funzione del modulo che scrive in `prompts/lezioni.md`: nessun altro percorso
del codice ci scrive.

**Espone:** `aggregate_evidence()`, `validate_proposal()`,
`build_propose_prompt()`, `estimate_propose_cost()`, `call_model()`,
`propose()`, `list_lessons()`, `accept()`, `reject()`, `review()`, `trend()`,
`render_lezioni_md()`, `active_lessons_summary()`, `load_db()`, `save_db()`,
`PATTERN_CATEGORIES`, `LessonsError`, `NeedsRemoval`.

**Attenzione:**
- `prompts/_lezioni.json` e' l'**unico database**: proposte, rifiutate,
  attive e rimosse ci stanno tutte, distinte da `stato`. `lezioni.md` e'
  sempre un RENDER (`render_lezioni_md()`) di quello che e' `attiva`, mai una
  fonte a se'.
- `call_model()` prende un parametro `caller` iniettabile apposta per i test:
  senza chiamerebbe davvero l'API Anthropic, e i test del progetto non
  chiamano mai API a pagamento.
- una lezione che tocca costi, approvazione o consenso vocale viene rifiutata
  dal tool stesso (`_FORBIDDEN_TOPICS`), sia in `propose()` sia in
  `accept(..., text_override=...)`: non basta che il modello non la proponga,
  serve che il codice non la accetti nemmeno se qualcuno la riscrive a mano.
- `review()` e `propose()` scrivono `prompts/_lezioni.json` **solo se c'e'
  davvero qualcosa di nuovo** da salvare: altrimenti girarli senza che ci sia
  niente da fare lascerebbe un file vuoto dove prima non c'era.

---

## Le musiche: intro, outro, sottofondo

### `src/ffmpeg_tool.py` — unico punto che cerca ffmpeg
**Cosa fa:** `find_ffmpeg()` (percorso o `None`) e `require_ffmpeg()` (come
sopra, ma solleva `FfmpegNotFoundError` se manca). Prima strada:
`imageio_ffmpeg.get_ffmpeg_exe()` (il pacchetto pip, dipendenza obbligatoria:
porta un binario statico, cosi' il progetto si installa con il solo `pip
install` su macOS/Windows/Linux). Seconda strada: `ffmpeg` nel PATH di
sistema, come ripiego.

**Ci metti mano quando:** cambi come/dove si cerca ffmpeg.

**Attenzione:** nessun altro file chiama `shutil.which("ffmpeg")` o importa
`imageio_ffmpeg` per conto proprio — ne' `audio_assembler.py` ne'
`music_mixer.py`. Se ti accorgi di farlo altrove, stai duplicando questa
scelta, e le due strade rischiano di divergere. La variabile d'ambiente
`PODCAST_AGENT_DISABLE_FFMPEG` forza `find_ffmpeg()` a ritornare `None`: serve
solo ai test (`tests/test_music.py`), per simulare "ffmpeg assente" senza
doverlo disinstallare davvero.

### `src/music_library.py` — cartella musiche + risoluzione dei nomi
**Cosa fa:** sulla falsariga di `voice_library.py`, ma per `musiche/`:
scansione delle tre sottocartelle (assenti = categoria vuota, MAI un errore,
a differenza delle voci), lettura dei metadati JSON accanto a ogni brano,
sha256 (riusa `file_hash()` di `registry.py`), durata (`wave` per i `.wav`,
altrimenti l'eseguibile ffmpeg trovato da `ffmpeg_tool.py`, parsificando la
riga "Duration:" che stampa anche senza un output valido — nessuna dipendenza
da `ffprobe`, che `imageio-ffmpeg` non porta).

**Espone:** `MusicLibrary` (`.scan()`, `.tracks()`, `.resolve()`,
`.get_usable()`), `MusicTrack`, `TrackMetadata`, `MusicLibraryError`,
`CATEGORIES`, `load_music_choice()`/`write_music_choice()`/`has_any_music()`
(lettura/scrittura di `musiche.json` nella cartella della run).

**Attenzione:**
- `resolve()` ha la STESSA regola di `voice_library.resolve()`: nessuna
  corrispondenza parziale indovinata, solo candidati. E' duplicata (non
  importata) apposta: sono due cataloghi concettualmente indipendenti che per
  ora condividono solo la tecnica di confronto;
- un sottofondo con `parlato: true` nei metadati e' un errore di dominio,
  rilevato in `_load_metadata()`: il brano resta visibile in un elenco (con
  `metadata_error` valorizzato) ma `get_usable()` lo rifiuta.

### `src/music_mixer.py` — il mix, e SOLO qui si parla con ffmpeg per il mix
**Cosa fa:** intro (opzionale) + sezione parlata (voce [+ sottofondo]) +
outro (opzionale), normalizzati e concatenati. Vedi `preflight()` (validazione
PRIMA di ogni sintesi: nomi, ffmpeg presente, sha256 registrati) e
`mix_episode()` (il mix vero — la stessa funzione chiamata da
`tools/synthesize.py` e `tools/mix.py`, mai una copia).

**Perche' in parte Python e in parte ffmpeg.** Loop con dissolvenza
incrociata, dissolvenze, guadagni e somma dei segnali sono in **Python puro**
(`array` + `wave`, stesso stile di `_trim_edges`/`_cap_internal_silence` in
`audio_assembler.py`): deterministico e ispezionabile. ffmpeg resta per le tre
cose che in Python non avrebbe senso reimplementare: decodifica/ricampionamento
di formati compressi, normalizzazione loudness (`loudnorm` a due passaggi:
misura poi applica, con `aresample=<rate>` incatenato nello stesso filtro
perche' loudnorm elabora internamente a 192kHz), e compressione sidechain per
il ducking.

**Attenzione — un limite VERO di `sidechaincompress` (non un errore di
configurazione):** verificato su ffmpeg 7.1, il filtro tronca la coda
dell'output di ~0.7-1.1s indipendentemente dai parametri di attack/release
(riprodotto anche con i valori di default). Il rimedio, in `_apply_ducking()`,
e' accodare silenzio a ENTRAMBI gli ingressi prima del filtro e ritagliare
l'output alla lunghezza voluta dopo (`_DUCKING_PAD_SECONDS`). Se aggiorni
ffmpeg e il bug sparisce, il rimedio resta comunque innocuo — non toglierlo
senza aver riverificato su piu' versioni.

**Attenzione — il segno del guadagno del sottofondo:** `bed_level_db` e'
negativo (es. `-20` = "20 dB sotto la voce"). Il target del sottofondo e'
`target_lufs + bed_level_db`, **non** `target_lufs - bed_level_db` — e' un
errore facile da riscrivere per sbaglio (e' successo durante lo sviluppo:
produceva un sottofondo 20 dB troppo forte invece che troppo piano). Il test
in `tests/test_music.py` [5] se ne accorgerebbe.

**Attenzione — canali:** `_decide_channels()` promuove voce+musiche a stereo
SOLO se la voce e' mono e almeno una musica scelta e' stereo (altrimenti si
perderebbe la spazialita' del brano senza motivo); se tutto e' mono resta
mono. La conversione avviene PRIMA di qualunque misura di loudness — misurare
e poi cambiare canali sposterebbe il valore misurato.

**Attenzione — `voice_offset_seconds` e il file unico del wav voce:**
`assembler.assemble_voice_master()` (in `audio_assembler.py`) scrive
`segmenti/_voce_master.wav` una volta sola; sia `_write_timings()` in
`tools/synthesize.py` sia `mix_episode()` leggono da li' (tramite
`MixResult.voice_assembly`), invece di rimontare due volte gli stessi
segmenti. `_tempi_turni.json` resta SEMPRE riferito a questo master di sola
voce, mai al file finale.

**Ci metti mano quando:** cambi come si costruiscono intro/outro/sottofondo,
o vuoi un algoritmo di loop/ducking diverso.

**Non ci metti mano se:** vuoi solo cambiare un valore — quello sta in
`config.yaml`, sezione `music:`.

---

## `tools/` — gli entry-point del subagent

Questi file **non contengono logica**: sono argparse + import da `src/` + `emit()`. Se ti
accorgi di scrivere un `if` interessante dentro `tools/`, quel codice va in `src/`.

### `tools/_common.py`
**Cosa fa:** il contratto comune. `emit()` (JSON su stdout), `note()` (umano su stderr),
`bootstrap()` (`.env` + `config.yaml`), `run_tool()` che traduce le eccezioni in JSON +
exit code.

| Exit | Significato | Cosa deve fare l'agente |
|---|---|---|
| `0` | ok | prosegue |
| `1` | errore di dominio | legge `error`, corregge, richiama |
| `2` | errore del provider vocale | **si ferma e riporta**, non ritenta |
| `3` | serve l'utente | chiede con `AskUserQuestion` |

**Attenzione:** il codice `2` e' l'implementazione della regola "nessun retry sulla
clonazione". Se lo mappi su `1`, l'agente ricomincera' a ritentare e creera' voci
duplicate a pagamento.

### `tools/_approval.py` — il checkpoint, reso verificabile
**Cosa fa:** il gate condiviso dai due passi che spendono (`voices_prepare.py` e
`synthesize.py`). `enforce(args, script)` blocca se la sessione non registra
un'approvazione **o se lo sha256 approvato non corrisponde al copione che sta per essere
usato**.

**Perche' esiste:** nella CLI e' il ciclo `while` di `run.py` a impedirti di proseguire.
Con il subagent quel ciclo non c'e', e una regola scritta nel prompt e' una promessa, non
una garanzia. L'hash la rende una garanzia.

**Attenzione:** il confronto e' contro il **copione su disco**, non contro
`session.current_script`. E' questo che intercetta anche il caso in cui
`conversazione.json` venga riscritto aggirando `script_save.py`. Se lo cambi in un
confronto con la sessione, apri esattamente quel buco.

`--no-approval-gate` esiste per l'uso manuale e la CI: e' esplicito nel comando, quindi
resta visibile quando viene usato.

### I tool

| File | Importa da `src/` | Nota di manutenzione |
|---|---|---|
| `doc_extract.py` | `pdf_reader`, `config` | con `--save-to` scrive il testo su file per chi scrive il copione; un nome nudo lo cerca in `documenti/` |
| `script_save.py` | `script_session`, `script_writer`, `music_library` | **valida** il copione scritto dal subagent: rifa' a mano i controlli che lo schema JSON garantiva. Vedi sotto. Alla prima stesura cattura anche `music_context` da `musiche.json` (solo intro/outro con `parlato: true`) |
| `script_approve.py` | `script_session` | registra il si' dell'utente legandolo allo sha256 |
| `voices_list.py` | `voice_library`, `registry` | **ritorna `match: null` sui parziali**: e' cosi' che la regola anti-indovinare sopravvive al passaggio a subagent |
| `music_list.py` | `music_library` | elenca i brani, o risolve un nome. **Diverge da `voices_list.py`**: `--resolve CATEGORIA NOME` esce col codice 3 (ambiguo) o 1 (non trovato) invece di ritornare sempre `match: null` — e' esplicitamente richiesto perche' serve anche a `music_set.py`, che deve potersi fermare da solo senza un agente a interpretare `match: null` |
| `music_set.py` | `music_library` | scrive `musiche.json`: gratis, nessuna approvazione. Stessa risoluzione dei nomi di `music_list.py`. `warnings[]` per i brani senza licenza |
| `run_init.py` | `run_layout`, `script_session` | va chiamato prima del copione: la sessione vive nella cartella della run |
| `script_write.py` | `script_writer`, `script_session` | percorso API (`run.py`), **non** usato dal subagent. Espone `build_writer()` e `report()` |
| `script_revise.py` | `script_session` | idem: ricostruisce la conversazione dal disco |
| `voices_prepare.py` | `config`, `registry`, `voice_library` | **nessun retry**; passa dal gate; `--dry-run` non spende quindi non lo richiede; scrive `_audio_paths.json` |
| `estimate.py` | `cost_estimate`, `audio_assembler`, `music_library` | non spende: nessun gate. Con `--run-dir` conta solo i turni non in cache. Aggiunge `duration_with_music_seconds` (durata + intro/outro/pause) accanto a `duration_seconds` (solo parlato): NON sostituirle una con l'altra, restano entrambe apposta |
| `synthesize.py` | `audio_assembler`, `music_mixer` | passa dal gate, poi `check_voice_coverage()`. Se `musiche.json` esiste, `preflight()` valida PRIMA della sintesi (ffmpeg, nomi, sha256), poi il mix e' l'ultimo passo. I segmenti restano: si cancellano solo con `--purge-segments` |
| `mix.py` | `music_mixer`, `segment_cache`, `audio_assembler` | rifa' SOLO il mix dalla cache: zero chiamate TTS. Passa dal gate (verifica che l'episodio sia stato approvato, non che il mix lo sia — produce l'output finale ascoltabile). Distingue "nessun segmento" da "impronta di sintesi cambiata" (due cause, due messaggi) |
| `turn_at.py` | `episode_timeline`, `music_mixer` | dal minutaggio DEL FILE FINALE alla battuta. Sottrae `voice_offset_seconds` da `musiche_usate.json` se esiste; un istante nell'intro/outro lo dice esplicitamente. Non tocca nessun provider; senza timeline dice di rimontare |
| `retake.py` | `retake`, `audio_assembler`, `music_mixer` | nuovi take a parita' di testo. Passa dal gate; `--dry-run` no, perche' non spende. `--with-music` aggiunge un'anteprima con il sottofondo sotto (stessi parametri del mix vero, ma senza le dissolvenze ai bordi: l'anteprima e' quasi sempre a meta' episodio). Usa il `voice_id` di `voci.json` **della run** e non riclona mai |
| `run_finalize.py` | `run_layout`, `registry` | produce i dati del riepilogo finale |
| `doctor.py` | `ffmpeg_tool`, `config` | diagnosi: Python, dipendenze, ffmpeg (e da dove), filtri richiesti, cartelle di config.yaml. **Non segue la convenzione 0/1/2/3**: l'exit code e' un riassunto di controlli (0/1), non la traduzione di un'eccezione |
| `signals.py` | `episode_signals` | raccoglie i segnali di una run gia' sintetizzata e scrive `_segnali.json`. Gira gia' da solo dentro `synthesize.py`: serve per rilanciarla a mano. Nessun costo, nessuna approvazione |
| `lessons.py` | `lessons`, `episode_signals` | `--evidence`/`--list`/`--review`/`--trend` non spendono e non scrivono `lezioni.md`. `--propose` chiama un modello (dichiara il costo prima) e scrive proposte in `prompts/_lezioni.json`. **Solo `--accept` scrive `prompts/lezioni.md`** |

#### I controlli di `script_save.py`

Con l'API la forma del JSON era garantita **per costruzione** dagli structured output.
Quando il copione lo scrive il subagent quella garanzia non c'e' piu', quindi
`_validate()` la ricostruisce. **Bloccanti** (exit 1, niente viene salvato):

| # | Controllo | Veniva da |
|---|---|---|
| 1 | la radice e' un oggetto | schema |
| 2 | `titolo` presente ed e' una stringa | schema `required` |
| 3 | `turni` presente ed e' una lista | schema `required` |
| 4 | ogni turno e' un oggetto | schema `items.type` |
| 5 | ogni turno ha `speaker` e `testo`, stringhe | schema `items.required` |
| 6 | nessuna chiave extra, radice e turni | `additionalProperties: false` |
| 7-11 | titolo non vuoto, almeno un turno, speaker ammessi, nessuno speaker muto, nessun testo vuoto | `PodcastScript.validate()` |
| 12 | `titolo` e `testo` non fatti di soli spazi | **nuovo**: `"   "` passava lo schema |

**Non bloccanti** (finiscono in `warnings[]`): markdown residuo, indicazioni di regia fra
parentesi quadre, emoji, turni oltre le 600 battute. Sono violazioni di stile: vanno viste,
non devono far fallire un salvataggio per un asterisco.

**Attenzione generale:** l'agente parsa le **chiavi JSON**. Rinominarne una e' un breaking
change silenzioso: `tests/test_tools_cli.py` esiste per accorgersene.

---

## Il prompt condiviso

### `prompts/copione.md`
**Cosa fa:** le regole di scrittura del copione. **Fonte unica**, letta da due consumatori:
`src/script_writer.py` la usa come system prompt per l'API, e il subagent la legge prima di
scrivere il copione a mano.

**Ci metti mano quando:** vuoi cambiare tono, ritmo, lunghezza dei turni, stile del podcast,
o come vengono usate le **inline tag** di espressività (`[esitante]`, `[ride]`, `[enfasi]`):
la sezione `## Tag di espressivita'` è quella che insegna a chi scrive dove metterle e
quanto dosarle.

**Attenzione:** `script_writer.py` ha ancora `_FALLBACK_SYSTEM_PROMPT`, una copia di
riserva usata solo se il file manca — serve a degradare invece di esplodere, non e' una
seconda fonte. Se modifichi `prompts/copione.md` e vuoi che il fallback resti allineato,
aggiornalo; il test in `test_tools_cli.py` verifica che la parte di stile coincida.

La sezione `## Formato` del file esiste perche' nel percorso API la forma del JSON era
descritta dallo schema strutturato: chi scrive a mano quell'informazione non la
riceverebbe.

---

## La dizione italiana: cosa abbiamo imparato

Questa sezione raccoglie il risultato di un giro di lavoro dedicato a una cosa
sola: **le domande uscivano piatte**. È la parte meno ovvia del progetto, e la
più facile da smontare per sbaglio, quindi qui c'è sia la conclusione sia la
prova, così chi arriva dopo può rimetterla in discussione con dei numeri invece
che a naso.

Le leve sono tre e **non hanno lo stesso peso**. In ordine di forza:

1. il campione vocale;
2. il modello TTS;
3. le tag di espressività nel testo.

### 1. Il campione è il soffitto

Il cloning istantaneo non impara una voce in astratto: riproduce la **gamma
prosodica presente nel campione**. Se il campione è una lettura piana, il
modello non andrà oltre, per quanto lungo sia il campione — e nessun parametro
di `config.yaml` recupera quello che nel materiale di partenza non c'è.

Misure fatte sui campioni reali del progetto (metodo sotto):

| campione | contorni ascendenti | escursione mediana |
|---|---|---|
| lettura piana, prima del protocollo | 3 su 23 frasi | **+2,7 st** |
| lettura piana, altro parlante | 0 su 16 frasi | +3,8 st |
| registrato col protocollo nuovo | **7 su 25 frasi** | **+4,8 st** |

L'episodio sintetizzato dal primo campione aveva escursione mediana +3,0 st
sulle affermative: cioè il modello stava riproducendo fedelmente il campione,
e il campione era il problema. Il protocollo di registrazione sta in
`README.md`, sezione *"Cosa registrare: la gamma di pitch, non solo la voce"*.

Il punto da ricordare: **prima di toccare qualsiasi parametro, guarda il
campione.** Se le domande escono piatte, quasi sempre nel campione non c'è
nemmeno una domanda.

### 2. `drama-3-preview` invece di `s2.1-pro`, per l'italiano

In italiano i due tipi di domanda hanno contorni **opposti**:

- **con parola interrogativa** (*chi, come, dove, perché*): picco sulla parola
  interrogativa, poi la frase **scende**;
- **sì/no a ordine dichiarativo** (*"E il prezzo sale?"*): nessuna parola
  interrogativa, e l'unica cosa che la marca è il **rialzo finale**.

Confronto diretto sulle stesse tre frasi, stessa voce, stessi parametri:

| frase | `s2.1-pro` | `drama-3-preview` |
|---|---|---|
| wh — *"Dove hai messo…?"* | +3,6 st (**sale**) | −1,1 st (scende) |
| polare — *"Quindi la consegna è slittata…?"* | +2,1 st (sale) | −5,7 st (**scende**) |
| breve — *"E la seconda proposta?"* | +3,0 st (sale) | +1,7 st |

`s2.1-pro` applica un **rialzo finale a tutto ciò che finisce con punto
interrogativo**, indistintamente. È il contorno interrogativo inglese, e su una
wh-question italiana è sbagliato: è questo che si sentiva come "innaturale"
molto più della mancanza di ampiezza. `drama-3-preview` distingue i due casi e
sulla wh fa la cosa giusta.

**Il rovescio:** sulla polare `drama-3` scende, e lì il rialzo invece serve.
È il caso in cui la tag esplicita nel testo non è un abbellimento ma una
correzione. Vedi il punto 3.

`drama-3-preview` è un modello **preview**: la documentazione dichiara che
"behavior and availability may change". Il fallback è rimettere `s2.1-pro` in
`config.yaml` — stesso body, stessi parametri, stesso prezzo, nessun'altra
modifica — ed è scritto anche nel commento accanto a `tts_model`.

### 3. Le tag sulle domande

Verificato sintetizzando 3 turni interrogativi × 4 varianti × 3 prese
(`temperature: 0.7` rende una presa sola inutilizzabile: serve la mediana).

Il guadagno più netto: `[incalzante]` su una domanda **breve** porta
l'escursione da +4,9 a +11,0 st **lasciando intatta la discesa finale**. Ha
senso: su quattro parole il modello ha pochissimo su cui costruire, e la tag è
l'unica informazione in più.

Sulle domande lunghe le differenze misurate stavano **dentro la dispersione fra
le prese**, quindi lì i numeri non decidono. Ha deciso l'ascolto:
`[incalzante]` e `[enfasi sulla domanda]` funzionano **entrambe**, e quale sia
meglio dipende dalla battuta; `[incuriosito]` no. Le regole d'uso stanno in
`prompts/copione.md`.

Nota metodologica che vale oltre questo caso: **la misura ha battuto l'intuizione
sul contorno** (nessuno si aspettava che il problema fosse la salita sulle wh)
**e l'ascolto ha battuto la misura sulle tag** (n=3 non bastava a distinguere).
Non sono in competizione: servono a cose diverse.

### 4. Le pause fra i turni: ritagliare, non accorciare

Sintomo: con `drama-3-preview` le pause fra i turni suonano lunghe e
**irregolari**. La tentazione è abbassare `pause_between_turns_ms`. Sarebbe
sbagliato, perché il problema non è quel numero.

Il TTS aggiunge silenzio ai bordi di ogni segmento, e il montaggio ci somma la
pausa configurata. Il vuoto reale è quindi `coda + pausa + testa`. Misurato sui
segmenti prima del montaggio:

| | testa | coda |
|---|---|---|
| `drama-3-preview` | 60 ms mediani (0–80) | **905 ms mediani (160–1490)** |
| `s2.1-pro` | 0 ms sempre | 200 ms mediani (10–230) |

Il silenzio è quasi tutto **in coda**, e con drama-3 è 4,5 volte più lungo e
molto più variabile: il vuoto percepito era ~1415 ms contro i 450 configurati,
oscillando fra 610 e 2020 ms da un turno all'altro. **È la variabilità a
dare fastidio più della durata**, ed è nata col cambio di modello.

La soluzione è `_trim_edges()` in `src/audio_assembler.py`, che ritaglia i bordi
prima del montaggio (`audio.trim_silence` in `config.yaml`). A bordi puliti il
vuoto diventa `pausa + i due margini`, **costante**. Da lì
`pause_between_turns_ms` torna a significare quello che dice, ed è stato
portato a 550 (≈660 ms percepiti, il ritmo che si sentiva con `s2.1-pro`).

Sui segmenti veri toglie 833 ms mediani ciascuno, circa 38 s su un episodio di
45 turni.

**Due cose da non rifare:**

- *Non si possono separare coda e testa analizzando l'episodio già montato.*
  Il silenzio generato dal TTS è **digitale** quanto la pausa inserita: per
  ampiezza sono indistinguibili, e un'analisi ancorata al silenzio a zero dà
  numeri plausibili ma falsi. Vanno misurati i segmenti, prima del montaggio.
- *`prosody.speed` non compensa le pause.* Verificato: a `speed 1.15` il
  parlato si accorcia del 13% e le pause restano **identiche** (2,73 s → 2,72 s
  di silenzio interno). Accelerare rende le pause proporzionalmente più
  evidenti, non meno. `speed` serve alla velocità di eloquio, non al ritmo.

Nota a margine sulle pause **interne** al parlato: drama-3 le fa un po' più
lunghe (410 ms mediani contro 350) ma **meno numerose** (14,4 al minuto contro
17,4), e la quota di silenzio dentro il parlato è quasi identica (12,8% contro
11,6%). Non è lì il problema, e non serve intervenire.

### Come sono state fatte le misure

Non serve nessuna dipendenza: su macOS bastano `afconvert` (decodifica) e
Python puro. Gli script del giro non sono in repo perché sono usa e getta, ma
il metodo si rifà in mezz'ora:

1. `afconvert -f WAVE -d LEI16@4000 -c 1 in.m4a out.wav` — 4 kHz mono basta e
   avanza per l'F0 di una voce umana.
2. F0 per **autocorrelazione normalizzata** su finestre di 40 ms, passo 25 ms,
   con interpolazione parabolica del picco.
3. **Correzione degli errori di ottava**: un frame che sta più di 6 semitoni
   sopra o sotto la mediana locale è quasi sempre un raddoppio; senza questo
   passaggio i numeri sono spazzatura (la prima versione dava +15 st fasulli).
4. Segmentazione in frasi sulle pause di energia (300 ms).
5. Due indicatori, entrambi in **semitoni** perché il pitch è logaritmico:
   - **escursione** = `12·log2(p90 / mediana)` dei valori sonori della frase,
     cioè quanto il tono si alza sopra il proprio livello tipico;
   - **contorno finale** = `12·log2(fine / corpo)`, ultimi 450 ms contro la
     mediana della frase: positivo = sale, negativo = scende.

Per ritagliare un episodio già montato nei suoi turni: le pause fra i turni
sono **silenzio digitale** di durata nota (`audio.pause_between_turns_ms`),
quindi si tagliano cercando i run a ampiezza quasi zero di quella durata. E
l'allineamento **si verifica**: le voci hanno F0 mediana diversa, quindi la
sequenza degli speaker misurata deve combaciare con quella del copione. Se non
combacia, i numeri non valgono — è successo, ed è per questo che il controllo
c'è.

---

## Il subagent

### `.claude/agents/podcast-agent.md`
**Cosa fa:** il prompt del subagent. Frontmatter (`name`, `description`, `tools`) e poi:
regole non negoziabili, riferimenti, tabella dei tool con gli exit code, la procedura in 9
passi, il riepilogo finale, i limiti noti.

**Ci metti mano quando:** cambi il flusso, aggiungi un tool, o vuoi che l'agente chieda o
riporti qualcosa di diverso.

**Attenzione:** le "Regole non negoziabili" in testa sono la trasposizione della sezione
"Cosa NON fare" di questo file. Se aggiungi un vincolo qui sotto, aggiungilo anche li' —
altrimenti vale per te che leggi e non per l'agente che esegue.

---

## La CLI classica

### `run.py`
**Cosa fa:** l'orchestratore interattivo. Una funzione per passo, in ordine:

| Funzione | Passo |
|---|---|
| `step_document()` | 1 — chiede ed estrae il documento (elenca `documenti/` se non gliene passi uno) |
| `step_speaker_count()` | 2 — quante voci |
| `step_select_voices()` | 3 — quali voci, con conferma sui nomi ambigui |
| `step_write_script()` | 4-5 — scrittura **e checkpoint di revisione obbligatorio** |
| `make_run_dir()` | crea `output/<data>_<titolo>/` |
| `step_prepare_voices()` | 6 — clona o riusa dalla cache |
| `estimate()` + `render()` | 7 — mostra la stima |
| `synthesize()` / `assemble()` | 8-9 — sintesi e montaggio |
| `write_voices_used()` | scrive `voci_usate.json` (da `src/run_layout.py`) |
| `archive_source_document()` | copia il documento nella cartella della run (idem) |

**Ci metti mano quando:** cambi il flusso, aggiungi flag da riga di comando,
vuoi una modalità non interattiva.

**Attenzione:** il ciclo dentro `step_write_script()` **non esce senza un "sì"
esplicito**. Se aggiungi una scorciatoia (`--yes`, `--auto`) stai rimuovendo
l'unica difesa contro il pagare due volte la sintesi di uno script sbagliato.
L'ordine dei passi non è arbitrario: ogni cosa che costa (clonazione, sintesi)
sta **dopo** l'approvazione.

---

## Test

### `tests/fake_provider.py`
Provider finto che genera toni sinusoidali invece di chiamare un servizio.
Doppio ruolo: fa girare i test senza spendere, ed è l'**esempio di riferimento**
di quanto costa scrivere un provider nuovo (~60 righe).

### `tests/test_smoke.py`
51 controlli end-to-end, zero chiamate a pagamento:

```bash
.venv/bin/python tests/test_smoke.py
```

Copre: estrazione testo, scansione voci, risoluzione nomi (esatto / typo /
inesistente / per indice), i quattro casi del registro, validazione script,
stima, blocco pre-sintesi, sintesi, montaggio con verifica della durata reale
contro quella attesa, semantica della chiave dei segmenti (cosa invalida la
cache e cosa no) e caricamento dinamico del provider.

**Lancialo dopo ogni modifica.** È veloce e prende quasi tutte le regressioni.

### `tests/test_tools_cli.py`
171 controlli sugli entry-point di `tools/`, la superficie che usa il subagent:

```bash
.venv/bin/python tests/test_tools_cli.py
```

Ogni tool viene lanciato via `subprocess` e se ne verificano **chiavi JSON** ed **exit
code**. Copre anche i tre vincoli che devono sopravvivere al passaggio a subagent: typo →
`match: null` + candidati, clonazione fallita → exit `2`, speaker scoperto → blocco
*prima* della sintesi. Piu' il round-trip di `ScriptSession` (salva → ricarica → le
istruzioni precedenti sono ancora nel contesto), senza chiamare l'API.

I due tool che parlano con Claude vengono saltati con uno skip esplicito se manca
`ANTHROPIC_API_KEY`, per non spendere in CI.

La sezione `[15]` copre la correzione di una battuta end-to-end, contando le
chiamate al TTS finte una per una: seconda sintesi identica a zero chiamate,
testo di un turno cambiato a **una** sola chiamata (e gli altri segmenti byte per
byte identici), impronta di sintesi cambiata a tutti i turni rigenerati, sintesi
interrotta a meta' che riprende pagando solo il resto, retake che non tocca il
take precedente ne' l'altra battuta con lo stesso testo, `--choose` seguito da un
rimontaggio a costo zero, e i due blocchi del retake (senza approvazione, e con
una voce sparita dal provider — dove si verifica anche che **non** sia stata
clonata di nascosto).

### `tests/test_music.py`
Controlli su intro/outro/sottofondo, con brani di prova sintetici (toni,
nessun file audio nel repository). Le parti che non richiedono ffmpeg (elenco,
risoluzione dei nomi, `music_set.py`) girano sempre; il resto della suite fa
**skip esplicito**, non fallisce, se ffmpeg non si trova (ne' come pacchetto
`imageio-ffmpeg` ne' nel PATH).

```bash
.venv/bin/python tests/test_music.py
```

Copre: nessuna musica -> comportamento e ffmpeg invariati; durata finale
coerente con intro+gap+voce+gap+outro; loop del sottofondo con dissolvenza
incrociata; loudness finale entro ±1 LU e picco sotto soglia (misurati con
ffmpeg); musica richiesta e ffmpeg assente -> `synthesize.py` si ferma PRIMA
della sintesi (simulato con la variabile d'ambiente
`PODCAST_AGENT_DISABLE_FFMPEG`, vedi `src/ffmpeg_tool.py`); `mix.py` a zero
chiamate TTS, con e senza segmenti in cache; cambiare musica senza toccare i
segmenti; brano sostituito dopo il mix -> exit 3, poi `--accept-changed-music`;
`turn_at.py` con l'offset dell'intro; retake `--with-music` poi `mix.py`;
riproducibilita' bit-esatta del wav prima della codifica mp3; riduzione
misurata del ducking; contesto musicale nella sessione e nel prompt.

### `tests/test_lessons.py`
Test di `src/episode_signals.py` e `src/lessons.py`, zero chiamate a modelli o
a provider vocali reali:

```bash
.venv/bin/python tests/test_lessons.py
```

Copre: allineamento dei turni con aggiunte/eliminazioni/spostamenti (mai per
posizione); tag rimosse/aggiunte/sostituite con la loro posizione; retake letti
da `scelte.json`, e "non disponibile" tenuto distinto da "zero" quando i
segmenti sono stati cancellati; correzioni post-ascolto distinte da revisioni
pre-approvazione, e verificato che un retake/rimontaggio a parita' di testo non
ne crea di fantasma; soglia di ricorrenza; aggregazione deterministica;
`--propose` che scrive solo in `_lezioni.json` (mai in `lezioni.md`), che
tratta "nessuna proposta" come esito valido, e che rifiuta una categoria fuori
dal vocabolario chiuso; `--accept` come unico scrittore di `lezioni.md` e il
suo comportamento al tetto di righe; `--reject` che persiste il motivo e blocca
una proposta identica; una lezione non puo' toccare costi/approvazione/consenso
vocale; `ScriptWriter` col prompt identico byte per byte senza `lezioni.md`;
`--review` che segnala una lezione il cui schema si ripresenta ancora.

**Non tocca mai `prompts/copione.md` o `prompts/lezioni.md` del progetto
vero**: ogni test che scrive lo fa in una cartella temporanea passata
esplicitamente alle funzioni di `src/lessons.py` — per questo prendono
`prompts_dir` come parametro invece di un percorso fisso.

---

## Dove metto le mani se voglio...

| Obiettivo | File da toccare |
|---|---|
| cambiare motore vocale | `config.yaml` (`active_provider`) + un nuovo file in `src/providers/` |
| cambiare tono/stile del podcast | `SYSTEM_PROMPT` in `src/script_writer.py` |
| episodi più lunghi o più corti | `script.target_minutes` in `config.yaml` |
| pause diverse fra i turni | `audio.pause_between_turns_ms` in `config.yaml` — ma leggi prima *Le pause fra i turni* |
| **le pause suonano lunghe o irregolari** | `audio.trim_silence` e i margini, non `pause_between_turns_ms` |
| spostare la cartella dei documenti di partenza | `paths.documents_dir` in `config.yaml` |
| accettare un nuovo formato di registrazione | `audio.accepted_extensions` in `config.yaml` |
| leggere un nuovo formato di documento | `src/pdf_reader.py` |
| aggiungere/cambiare intro, outro o sottofondo di un episodio | `tools/music_set.py` poi `tools/mix.py` — gratis, zero chiamate TTS |
| cambiare come si costruisce il mix (loop, dissolvenze, ducking) | `src/music_mixer.py` — **non** `audio_assembler.py`, che resta il montaggio voce-sola |
| cambiare i livelli/parametri del mix senza toccare il codice | `config.yaml`, sezione `music:` |
| il sottofondo e' troppo forte/debole rispetto alla voce | `music.bed_level_db` |
| il ducking pompa nelle pause, o non si sente | `music.ducking_release_ms` (deve stare sopra `pause_between_turns_ms`), poi `ducking_threshold_db`/`ducking_ratio` |
| cambiare i campi di `conversazione.json` | `_SCHEMA` **e** `PodcastScript` in `src/script_writer.py` |
| cambiare i campi del registro | `src/registry.py` (e cancella il registro esistente) |
| stima costo al minuto invece che a carattere | `src/cost_estimate.py` + `pricing()` del provider |
| far girare l'agente senza domande interattive | `run.py` — ma leggi prima l'avvertenza sul checkpoint |
| cambiare tono o stile del podcast | `prompts/copione.md` (vale per CLI **e** subagent) |
| **rendere il parlato più espressivo, punto per punto** | le **inline tag** `[esitante]`, `[ride]`, `[enfasi]` dentro il testo dei turni — le regole d'uso stanno in `prompts/copione.md` |
| **rendere il parlato più espressivo, su tutto l'episodio** | `providers.fish_audio.options.tts` in `config.yaml` (`temperature`, `top_p`, `speed`, `repetition_penalty`) |
| **le domande escono piatte** | nell'ordine: il **campione** (vedi *La dizione italiana*), poi `tts_model` in `config.yaml`, poi le tag — non il contrario |
| cambiare modello TTS / tornare a `s2.1-pro` | `providers.fish_audio.options.tts_model` in `config.yaml` |
| capire perche' una domanda suona sbagliata | `## La dizione italiana` in questo file: i due contorni interrogativi italiani e come misurarli |
| esporre un parametro di sintesi nuovo | `_TTS_PARAMS` in `src/providers/fish_audio_provider.py` **e** `config.yaml` |
| irrigidire/allentare la validazione del copione | `_validate()` in `tools/script_save.py` (è lì che si contano le tag per turno) |
| cambiare come si comporta il subagent | `.claude/agents/podcast-agent.md` |
| aggiungere un passo richiamabile dal subagent | un file nuovo in `tools/` + riga nella tabella dell'agente |
| cambiare la gestione delle revisioni | `src/script_session.py` |
| **correggere una battuta di un episodio gia' montato** | `tools/turn_at.py` per trovarla, poi `script_save.py` (testo sbagliato) o `tools/retake.py` (resa sbagliata) |
| liberare lo spazio dei segmenti | `synthesize.py --purge-segments` — ma cosi' la correzione successiva ripaga tutto l'episodio |
| cambiare cosa invalida la cache dei segmenti | `synthesis_fingerprint()` del provider, non `segment_key()` |
| cambiare la soglia dell'avviso sul livello di un take | `RMS_WARNING_DB` in `src/retake.py` |
| aggiungere un nuovo tipo di segnale per le lezioni | `src/episode_signals.py` (estrazione) **e**, se serve un pattern nuovo, `PATTERN_CATEGORIES` in `src/lessons.py` — in un solo punto, condiviso da aggregazione, prompt di proposta e `--review` |
| cambiare le soglie di ricorrenza, il tetto di righe o il modello di `--propose` | sezione `lessons:` in `config.yaml` |
| **aggiungere una riga a `prompts/lezioni.md`** | mai a mano: solo `tools/lessons.py --accept ID` |

## Cosa NON fare

1. **Non importare `fish_audio_provider` fuori da `config.py`.** Il resto del
   codice deve conoscere solo `VoiceProvider`.
2. **Non far ritornare a `resolve()` il candidato migliore.** L'agente
   comincerebbe a indovinare i nomi delle persone.
3. **Non saltare il checkpoint di approvazione.** È lì per non pagare la
   sintesi due volte.
4. **Non mettere le API key in `config.yaml`.** Vanno in `.env`; il config
   contiene solo il *nome* della variabile d'ambiente.
5. **Non ritentare automaticamente la clonazione.** Crea voci duplicate a
   pagamento sul provider.
6. **Non cancellare `voices_registry.json` a cuor leggero.** Tutte le voci
   verranno riclonate alla prossima esecuzione, e le vecchie resteranno
   orfane sul provider.
7. **Non mettere logica dentro `tools/`.** Quei file sono adattatori: se ci
   finisce una decisione, la CLI e il subagent smettono di comportarsi allo
   stesso modo.
8. **Non mappare l'exit code `2` su `1`.** E' il segnale che ferma i retry
   sulla clonazione.
9. **Non far confrontare al gate `session.current_script` invece del copione
   su disco.** Aprirebbe la scorciatoia di riscrivere `conversazione.json`
   a mano dopo l'approvazione.
10. **Non far leggere a `seed_messages()` lo storico `versions[]`.** Le bozze
    scartate stanno su disco apposta: in contesto si ripagano a ogni giro.
11. **Non rimettere la posizione del turno dentro la chiave di un segmento.**
    Inserire una battuta a meta' copione sposterebbe tutte le chiavi
    successive, e una correzione tornerebbe a costare l'episodio intero.
12. **Non far condividere un segmento a due turni con lo stesso testo.**
    Si risparmierebbe una chiamata, ma lo stesso identico audio ripetuto si
    sente come un nastro, e un retake sull'uno cambierebbe di nascosto anche
    l'altro. E' a questo che serve l'occorrenza dentro la chiave.
13. **Non scrivere un segmento senza passare da un `.tmp`.** La cache decide
    "gia' fatto" dalla sola esistenza del file: un wav troncato verrebbe
    riusato per sempre, e il difetto non lo segnalerebbe niente.
14. **Non far riusare al montaggio l'mp3 esistente.** I wav grezzi si rileggono
    tutti e l'mp3 si ri-codifica da zero: tagliare e ricucire un mp3 gia'
    codificato lascia artefatti agli stacchi.
15. **Non far riclonare una voce a `retake.py`.** Una voce nuova ha un timbro
    diverso dal resto dell'episodio, e lo stacco si sentirebbe proprio sulla
    battuta che si sta correggendo.
16. **Non far scegliere un take al codice.** Si puo' misurare il livello, non
    "suona giusto". La scelta e' dell'utente.
17. **Non costruire comandi ffmpeg fuori da `src/music_mixer.py`.** E' l'unico
    modulo che parla ffmpeg per il mix; nessun altro file, tool compreso, deve
    farlo — stessa regola d'oro della sola `fish_audio_provider.py` per il
    provider vocale.
18. **Non cercare l'eseguibile ffmpeg con `shutil.which("ffmpeg")` o
    `imageio_ffmpeg` fuori da `src/ffmpeg_tool.py`.** E' l'unico punto che sa
    le due strade (pacchetto pip, poi PATH di sistema) e il messaggio da dare
    se manca; duplicarlo altrove le fa divergere.
19. **Non far ripagare la voce a chi cambia solo le musiche.** `mix.py` deve
    rimontare dalla cache dei segmenti, mai richiamare `assembler.synthesize()`:
    e' quello che rende gratis cambiare intro/outro/sottofondo.
20. **Non far cadere `_apply_ducking()` nella truncation di `sidechaincompress`.**
    Il filtro tronca la coda dell'output indipendentemente dai parametri (vedi
    la sezione *Le musiche* sopra): il rimedio col padding-e-ritaglio non va
    tolto senza aver riverificato su piu' versioni di ffmpeg.
21. **Non far scrivere `prompts/lezioni.md` a nessuna funzione diversa da
    `accept()` in `src/lessons.py`.** E' quello che rende vero "nessuna riga
    entra nel prompt senza approvazione esplicita" a livello di codice, non
    solo di procedura — vedi `tests/test_lessons.py`.
22. **Non far dedurre "corretto dopo l'ascolto" da un mtime.** Un mtime cambia
    copiando la cartella, rimontando o ripristinando un backup:
    `src/episode_signals.py` registra invece un evento esplicito
    (`record_synthesis_event()`), e lo confronta col testo, non con
    l'orologio del filesystem.
23. **Non far proporre a `--propose` un pattern fuori da
    `PATTERN_CATEGORIES`.** Se ci riuscisse, `--review` non lo ritroverebbe
    mai piu' e la lezione resterebbe non verificabile per sempre — per questo
    la validazione e' nel tool, non solo nel prompt del modello.


---

## Aggiunte recenti — stima della durata e formati

### La durata si stima sui CARATTERI, e si mostra come intervallo

`src/cost_estimate.py` non usa piu' `words_per_minute` come strada principale.

**Perche' i caratteri e non le parole.** Le parole italiane cambiano lunghezza
da un documento all'altro (6,02 contro 6,35 caratteri di media sui due
documenti gia' prodotti), quindi un tasso in parole/minuto misura anche il
vocabolario, non solo la velocita' di lettura. I caratteri no. In piu' sui
caratteri si possono togliere le inline tag `[fra quadre]`, che il provider
interpreta e non pronuncia: `PodcastScript.spoken_chars` le esclude,
`total_chars` no.

**Le due grandezze non si contano allo stesso modo**, ed e' l'errore facile:

| | si conta su | perche' |
|---|---|---|
| costo | `total_chars` (grezzi) | le tag vengono inviate all'API, quindi si pagano |
| durata | `spoken_chars` (netti) | le tag non vengono pronunciate |

**Il tasso sta nel provider, non in `script:`.** `chars_per_second` e' un
attributo di `VoiceProvider` accanto ad `audio_format`, valorizzato da
`providers.fish_audio.options.chars_per_second` in `config.yaml`. E' una
proprieta' del modello TTS: lo stesso copione esce a 15,18 car/s con
`drama-3-preview` e a 16,77 con `s2.1-pro`. **Se cambi `tts_model`, questo
valore va rimisurato.** `words_per_minute` resta come ripiego per i provider
che non dichiarano un tasso.

**Il termine delle pause include i margini del ritaglio.** Con
`audio.trim_silence: true` il vuoto percepito fra due turni e' `pause + coda +
testa`, cioe' 350 + 80 + 30 ms, non i soli 350. Prima la stima contava solo la
pausa e sbagliava di 110 ms per stacco. Senza ritaglio i margini non si
sommano, perche' li' i bordi sono quelli naturali del TTS, variabili.

**La stima e' un intervallo** (`DURATION_UNCERTAINTY`, oggi ±10%, arrotondato a
30 secondi). Sulle 5 run misurate l'errore medio e' 5,9% e il peggiore 10,1%:
un valore al secondo comunicherebbe una precisione che la formula non ha. Per
le macchine restano `total_seconds`, `low_seconds`, `high_seconds`.

**Ci metti mano quando:** cambi modello TTS (rimisura `chars_per_second`), o
quando avrai abbastanza `_tempi_turni.json` per stringere l'intervallo.

### `_tempi_turni.json` — raccolta dati, piu' la timeline dei turni

`tools/synthesize.py` scrive in ogni run un file con, per ogni turno, i
caratteri parlati, la durata reale dopo il ritaglio e la posizione nel master
(`inizio` / `fine`), piu' l'aggregato per voce. `AudioAssembler` lo rende
possibile esponendo `AssemblyResult.segment_seconds` e
`AssemblyResult.turn_timeline`.

`inizio` e `fine` non sono raccolta dati: sono **in uso**, e li legge
`tools/turn_at.py` per tradurre un minutaggio nella battuta corrispondente. Il
resto del file resta materiale di misura.

**A cosa serve:** il residuo del ±10% non e' spiegato. Il modello TTS e' stato
escluso (stesso copione su due modelli), le cifre da normalizzare pure (8 e 20
su 6.500 caratteri), e la coppia di voci anche (la run piu' veloce e le due
piu' lente usano le stesse due voci). L'ipotesi rimasta e' che ogni voce
clonata legga a velocita' sua: questi file servono a verificarla.

**Attenzione:** la stima **non usa** le misure di velocita', di proposito. Vanno
accumulate per qualche run prima di decidere se un tasso per voce spieghi
davvero il residuo. Sul percorso `audio_format: mp3` il file non viene scritto:
li' i segmenti non vengono decodificati, quindi non si conosce ne' la durata di
un turno ne' la sua posizione — e `turn_at.py` lo dice invece di tirare a
indovinare.

Il file va riscritto a ogni montaggio: se il copione cambia e l'episodio non
viene rimontato, la timeline resta quella vecchia. `attach_texts()` se ne
accorge dal numero di turni e si ferma.

### Formati del copione: `conversazione` e `intervista`

**Dove stanno le regole:** `prompts/copione.md`, sezione `## Formati`. Come per
tutto il resto dello stile, e' la fonte unica letta sia da `run.py` sia dal
subagent.

**Le due deroghe sono la parte delicata.** Il formato `intervista` contraddice
due regole generali, e la contraddizione e' risolta per iscritto invece che
lasciata all'intuito: le regole originali sono marcate `REGOLA A`
(alternanza naturale) e `REGOLA B` (dosaggio delle tag) nel punto in cui sono
scritte, e la sezione `## Formati` dice con cosa vengono sostituite. Se tocchi
una delle due regole, il rimando va aggiornato da entrambe le parti.

**Dove viene ricordato:** `ScriptSession.script_format` e `.interviewer`, cioe'
nella sessione, come le voci. `--format` e `--interviewer` di `script_save.py`
servono alla prima stesura; le revisioni li riusano, e ometterli non riporta il
formato al default.

**I controlli sono AVVISI, non errori** (`_check_format` in
`tools/script_save.py`). Una domanda retorica dell'intervistato e una citazione
che finisce col punto interrogativo sono scrittura legittima: il gate
dell'approvazione non deve bloccarsi su una scelta stilistica. Sul copione
reale di riferimento il controllo produce 1 avviso su 30 turni, ed e' un falso
positivo di quel tipo.

**Attenzione:** `_FALLBACK_SYSTEM_PROMPT` in `src/script_writer.py` e' una copia
verbatim di `copione.md` fino a `## Formato`, e un test lo verifica
(`test_tools_cli.py`, blocco 13). Se modifichi la parte di stile del prompt,
quella costante va rigenerata, altrimenti la suite fallisce.

### Dove vive il pattern delle inline tag

`INLINE_TAG` e `spoken_text()` stanno in `src/script_writer.py`, non in
`tools/script_save.py` dov'erano: servono sia alla validazione sia alla stima
della durata, e due copie dello stesso pattern sarebbero divergiute alla prima
modifica. `tools/` resta argparse + import, come da regola d'oro.
