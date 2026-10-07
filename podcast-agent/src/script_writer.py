"""Generazione (e revisione) dello script del podcast con Claude.

Produce la struttura di conversazione.json:

{
  "titolo": "...",
  "turni": [{"speaker": "Mario Rossi", "testo": "..."}]
}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from .fileio import write_json_atomic
from .pdf_reader import Document


class ScriptWriterError(RuntimeError):
    """Errore durante la scrittura o la revisione dello script."""

    #: Nome stabile per il JSON di errore (vedi tools/_common.py). None = nome
    #: della classe, come per qualunque altra eccezione.
    error_type: str | None = None
    details: dict[str, Any] | None = None

    @property
    def kind(self) -> str:
        return self.error_type or type(self).__name__


class ModelAccessError(ScriptWriterError):
    """Il servizio del modello rifiuta l'accesso: chiave non valida, permessi,
    credito esaurito. Exit 2: non si risolve riprovando, serve un intervento
    su account o configurazione (come un errore del provider vocale)."""

    def __init__(self, message: str, *, error_type: str, status: int | None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.details = {"status": status}


class ModelTemporarilyUnavailable(ScriptWriterError):
    """Problema passeggero: rate limit, servizio sovraccarico, rete. Exit 1:
    si puo' riprovare, dopo `details.retry_after_seconds` se il servizio lo dice."""

    def __init__(
        self, message: str, *, error_type: str, status: int | None, retry_after: float | None
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.details = {"status": status, "retry_after_seconds": retry_after}


def _retry_after(exc: Exception) -> float | None:
    """Il valore dell'header retry-after (secondi), se il servizio l'ha mandato."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


#: Il credito esaurito NON ha una classe propria nella libreria: l'API risponde
#: 400 (invalid_request_error) con un messaggio sul saldo crediti. Lo si
#: riconosce dal testo: e' un'euristica, dichiarata nel contratto.
_CREDIT_HINTS = ("credit balance", "billing", "insufficient credit")


def classify_api_error(exc: Exception) -> ScriptWriterError:
    """Traduce un errore della libreria anthropic in un ScriptWriterError stabile.

    Il messaggio non include MAI la richiesta ne' gli header: la chiave API
    viaggia negli header e non deve finire in un JSON o in un log.
    """
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ModelAccessError(
            f"Accesso all'API Anthropic rifiutato (HTTP {status}): chiave non valida o "
            "senza permessi. Controlla la chiave Anthropic nel file .env (la variabile "
            "indicata in config.yaml, di solito ANTHROPIC_API_KEY).",
            error_type="ModelAuthError",
            status=status,
        )
    if isinstance(exc, anthropic.BadRequestError) and any(h in detail.lower() for h in _CREDIT_HINTS):
        return ModelAccessError(
            "Credito dell'API Anthropic esaurito: ricarica il credito dell'account "
            "prima di scrivere o rivedere il copione.",
            error_type="ModelCreditExhausted",
            status=status,
        )
    if isinstance(exc, anthropic.RateLimitError):
        return ModelTemporarilyUnavailable(
            "Rate limit dell'API Anthropic: troppe richieste, riprova fra poco.",
            error_type="ModelRateLimited",
            status=status,
            retry_after=_retry_after(exc),
        )
    overloaded = tuple(
        cls
        for cls in (
            getattr(anthropic, "OverloadedError", None),
            getattr(anthropic, "ServiceUnavailableError", None),
            getattr(anthropic, "InternalServerError", None),
            getattr(anthropic, "DeadlineExceededError", None),
        )
        if cls is not None
    )
    if isinstance(exc, overloaded) or (status is not None and status >= 500):
        return ModelTemporarilyUnavailable(
            f"L'API Anthropic e' sovraccarica o non disponibile (HTTP {status}): riprova fra poco.",
            error_type="ModelOverloaded",
            status=status,
            retry_after=_retry_after(exc),
        )
    if isinstance(exc, anthropic.APIConnectionError):  # comprende APITimeoutError
        return ModelTemporarilyUnavailable(
            "Impossibile contattare l'API Anthropic (rete o timeout): riprova fra poco.",
            error_type="ModelUnreachable",
            status=None,
            retry_after=None,
        )
    if isinstance(exc, anthropic.APIStatusError):
        return ScriptWriterError(f"Errore dell'API Anthropic (HTTP {status}): {detail}")
    return ScriptWriterError(f"Errore dell'API Anthropic: {detail}")


#: Le inline tag di espressivita' ([esitante], [ride], [enfasi]...) scritte
#: dentro `testo`. Il provider le INTERPRETA e non le pronuncia: per tutto cio'
#: che riguarda la durata del parlato vanno tolte, per tutto cio' che riguarda
#: il costo no (vengono comunque inviate all'API e quindi fatturate).
#:
#: Definito qui e non in tools/: e' la regola di cosa viene detto ad alta voce,
#: cioe' logica del copione, e serve sia alla stima sia alla validazione.
INLINE_TAG = re.compile(r"\[[^\]]{1,40}\]")


def spoken_text(text: str) -> str:
    """Il testo come verra' effettivamente pronunciato: senza le inline tag."""
    return re.sub(r"\s+", " ", INLINE_TAG.sub(" ", text)).strip()


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titolo": {
            "type": "string",
            "description": "Titolo dell'episodio, breve e concreto.",
        },
        "turni": {
            "type": "array",
            "description": "I turni di parola, in ordine di riproduzione.",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {
                        "type": "string",
                        "description": "Nome esatto di uno degli speaker forniti.",
                    },
                    "testo": {
                        "type": "string",
                        "description": "La battuta, in linguaggio parlato.",
                    },
                },
                "required": ["speaker", "testo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["titolo", "turni"],
    "additionalProperties": False,
}


@dataclass
class PodcastScript:
    titolo: str
    turni: list[dict[str, str]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PodcastScript":
        titolo = str(data.get("titolo", "")).strip()
        turni_raw = data.get("turni") or []
        turni = [
            {"speaker": str(t.get("speaker", "")).strip(), "testo": str(t.get("testo", "")).strip()}
            for t in turni_raw
            if isinstance(t, dict)
        ]
        return cls(titolo=titolo, turni=turni)

    def to_dict(self) -> dict[str, Any]:
        return {"titolo": self.titolo, "turni": self.turni}

    def save(self, path: str | Path) -> Path:
        """Scrittura atomica: il gate di approvazione confronta lo sha256 di
        questo file, e un file scritto a meta' (crash, interruzione) farebbe
        sembrare cambiato un copione che nessuno ha toccato."""
        return write_json_atomic(Path(path), self.to_dict())

    # -- metriche ---------------------------------------------------------

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for turn in self.turni:
            if turn["speaker"] not in seen:
                seen.append(turn["speaker"])
        return seen

    @property
    def total_chars(self) -> int:
        return sum(len(t["testo"]) for t in self.turni)

    @property
    def total_words(self) -> int:
        return sum(len(t["testo"].split()) for t in self.turni)

    @property
    def spoken_chars(self) -> int:
        """Caratteri realmente pronunciati (tag escluse): base per la DURATA."""
        return sum(len(spoken_text(t["testo"])) for t in self.turni)

    @property
    def spoken_words(self) -> int:
        """Parole realmente pronunciate (tag escluse)."""
        return sum(len(spoken_text(t["testo"]).split()) for t in self.turni)

    def validate(self, expected_speakers: list[str]) -> list[str]:
        """Ritorna la lista dei problemi bloccanti (vuota = script utilizzabile)."""
        problems: list[str] = []
        if not self.titolo:
            problems.append("Manca il titolo dell'episodio.")
        if not self.turni:
            problems.append("Lo script non contiene nessun turno di parola.")

        allowed = set(expected_speakers)
        unknown = sorted({t["speaker"] for t in self.turni} - allowed)
        if unknown:
            problems.append(
                "Nello script compaiono speaker non selezionati: "
                + ", ".join(unknown)
                + f" (attesi: {', '.join(expected_speakers)})"
            )
        missing = [s for s in expected_speakers if s not in self.speakers]
        if missing:
            problems.append(
                "Questi speaker selezionati non hanno nessuna battuta: "
                + ", ".join(missing)
            )
        empty = [i for i, t in enumerate(self.turni, start=1) if not t["testo"]]
        if empty:
            problems.append(f"Turni senza testo alle posizioni: {empty}")
        return problems

    def render(self) -> str:
        """Versione leggibile per il checkpoint di revisione."""
        lines = [f"TITOLO: {self.titolo}", ""]
        for index, turn in enumerate(self.turni, start=1):
            lines.append(f"[{index:02d}] {turn['speaker']}:")
            lines.append(f"     {turn['testo']}")
            lines.append("")
        return "\n".join(lines).rstrip()


PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "copione.md"
#: Le lezioni approvate dall'utente (vedi src/lessons.py): SOLO lettura da
#: qui, mai scritte da questo modulo. Se il file manca o e' vuoto il prompt
#: resta identico a quello di sempre — vedi load_system_prompt().
LESSONS_FILE = Path(__file__).resolve().parent.parent / "prompts" / "lezioni.md"

#: Copia di riserva delle regole di scrittura. La fonte e' prompts/copione.md,
#: letto sia da qui (percorso API) sia dal subagent (che il copione lo scrive
#: lui). Questa costante serve solo se quel file manca: senza, un progetto con
#: prompts/ cancellato fallirebbe a runtime invece di degradare.
_FALLBACK_SYSTEM_PROMPT = """Sei l'autore di un podcast. Ricevi un documento e scrivi il copione di un episodio.

Come lavori:
- Prima individui gli argomenti principali, la struttura e i punti salienti del documento; poi scrivi il copione che li racconta.
- Scrivi in linguaggio parlato, non scritto: frasi brevi, contrazioni, ritmo naturale. Niente elenchi puntati letti ad alta voce, niente titoli di paragrafo.
- Alterni gli speaker in modo naturale: si interrompono, rilanciano, si fanno domande, non recitano monologhi a turno. **(REGOLA A — sostituita nel formato `intervista`: vedi `## Formati`.)**
- Ogni turno di parola sta fra una e sei frasi. Se un concetto è lungo, spezzalo fra più turni.
- **Una domanda non sta mai in un turno di due o tre parole.** Ogni turno viene sintetizzato da solo, senza sapere cosa viene prima: su `"Dove?"` il sintetizzatore non ha nessun materiale su cui costruire l'intonazione e la domanda esce piatta. Se una domanda è breve, tienila nello stesso turno della frase che la introduce: invece di un turno `"Dove?"` scrivi `"Milletrecento alberghi, dici. E dove sono?"`.
- Il testo viene letto da un sintetizzatore vocale: usa solo punteggiatura normale. Niente emoji, niente markdown, niente indicazioni di regia fra parentesi tonde.
- **Scrivi le lettere accentate italiane per esteso: è, é, à, ò, ù, ì.** Mai l'apostrofo al posto dell'accento: si scrive `è`, non `e'`; `più`, non `piu'`; `così`, non `cosi'`; `città`, non `citta'`; `perché`, non `perche'`; `università`, non `universita'`. L'accento grafico è l'informazione con cui il sintetizzatore decide dove cade l'accento tonico: toglierlo peggiora la pronuncia più di qualsiasi parametro di sintesi.
- Apri presentando l'argomento e chiudi con una conclusione: l'ascoltatore non ha letto il documento.
- Resti fedele al documento: non inventare dati, nomi o citazioni che non ci sono.

Usi esattamente i nomi degli speaker che ti vengono dati, senza aggiungerne altri.

## Tag di espressività

Il sintetizzatore capisce delle *inline tag*: brevi indicazioni fra parentesi
**quadre**, scritte in linguaggio naturale italiano, che cambiano il modo in cui
la frase viene recitata. Non vengono lette ad alta voce.

Le scrivi **dentro il testo del turno**, nel punto esatto in cui l'intenzione
cambia — non come campo separato, non in fondo, non fra parentesi tonde:

```
"Quindi il margine è cresciuto del dodici per cento. [esitante] Almeno, questo dice la tabella a pagina nove."
```

Tag utili, come esempi e non come elenco chiuso: `[enfasi]`, `[esitante]`,
`[ride]`, `[sorride]`, `[pausa breve]`, `[tono professionale]`, `[incuriosito]`,
`[sottovoce]`, `[serio]`, `[entusiasta]`. Puoi scriverne altre con lo stesso
criterio: due o tre parole, in italiano, che descrivono **come** si dice la
battuta.

Dove le metti conta più di quante ne metti:
- **La tag va nel punto esatto in cui l'intenzione cambia**, cioè in mezzo alla
  battuta, sulla frase che cambia colore rispetto a quella prima.
- **Metterla sistematicamente in apertura di turno è l'errore tipico**: lì non
  segna nessun cambio, colora tutta la battuta in modo uniforme e il risultato
  è un parlato piatto, esattamente il contrario di quello che serve.
- Una tag in apertura si giustifica solo se è il turno intero a cambiare
  registro rispetto al turno precedente — una reazione, un rilancio ironico —
  e anche allora non su un turno dopo l'altro.

Confronta:

```
NO  "[incuriosito] E hanno guardato sei mesi di prezzi. Poi hanno incrociato le previsioni del meteo. Il risultato è che una giornata di pioggia in più vale l'uno per cento in meno."
SÌ  "E hanno guardato sei mesi di prezzi, incrociati con le previsioni del meteo. [incuriosito] Il risultato è che una giornata di pioggia in più vale l'uno per cento in meno."
```

Nel primo caso la curiosità è spalmata su tutto, anche sulla parte descrittiva.
Nel secondo arriva dove arriva il dato, cioè dove l'intenzione cambia davvero.

Sulle domande le tag servono piu' che altrove. Le due che funzionano sono
`[incalzante]` e `[enfasi sulla domanda]`, **senza una gerarchia fissa**: vanno
bene entrambe su qualsiasi tipo di domanda, e quale sta meglio dipende dalla
battuta. Provale mentalmente sulla frase e scegli quella che le somiglia di
piu'. Vanno messe **prima** della frase, dove l'intenzione parte.

Quello che invece cambia fra un tipo di domanda e l'altro e' **il contorno che
ti aspetti di sentire**, e serve a te per capire se una battuta sta funzionando:

- **Domanda con parola interrogativa** (*chi, come, quando, dove, perche',
  quanto*): in italiano il tono fa un **picco sulla parola interrogativa** e poi
  **scende**. Non va spinta verso l'alto alla fine: un rialzo finale qui suona
  sbagliato. La tag serve a dare slancio all'attacco, non a sollevare la
  chiusura.

  ```
  "[incalzante] E allora chi sono gli altri autori dello studio?"
  ```

- **Domanda si'/no a ordine dichiarativo** (*"E il prezzo sale?"*): qui non c'e'
  nessuna parola interrogativa, la frase e' costruita come un'affermazione e
  l'unica cosa che la rende domanda e' il **rialzo finale**. E' il caso piu'
  fragile: senza niente che la marchi, a volte il rialzo non arriva e la frase
  suona come un'affermazione nonostante il punto interrogativo.

  ```
  "Quindi piove, e il prezzo invece di scendere sale. [enfasi sulla domanda] Davvero funziona cosi'?"
  ```

Su una polare vanno bene anche `[dubbioso]`, `[interrogativo]`, `[incredulo]`
quando l'intenzione della battuta e' davvero quella: il dubbio o l'incredulita'
sono cose che il testo sta gia' dicendo, non trucchi di intonazione.

Come le dosi:
- **Una o due per turno, e non su ogni turno.** **(REGOLA B — sostituita nel formato `intervista`: vedi `## Formati`.)** Sono accenti: se ci sono
  dappertutto non si sentono più e il parlato suona caricaturale.
- **Solo dove la battuta le chiede davvero**: un dato sorprendente, un dubbio,
  una battuta divertente, un cambio di tono fra due frasi, l'attacco di una
  conclusione.
- **Mai per sostituire le parole.** La battuta deve funzionare anche
  togliendo le tag: non scrivere `[ride]` al posto di una reazione, scrivi la
  reazione e mettici `[ride]` davanti.
- **Mai per dare indicazioni di regia o di montaggio** (`[musica]`,
  `[stacco]`, `[sigla]`): la tag descrive la voce, non l'episodio.

## Formati

Il formato decide **chi fa cosa**, non di cosa si parla. Ti viene detto quale
usare; se non te lo dicono vale `conversazione`.

### `conversazione` (default)

Due voci alla pari. Entrambe portano contenuto, entrambe possono chiedere.
Valgono tutte le regole scritte sopra, senza eccezioni.

### `intervista`

Un ruolo **conduce** (l'intervistato: espone, spiega, porta i dati), l'altro
**chiede** (l'intervistatore: fa procedere il discorso con le domande). Chi
interpreta quale ruolo ti viene detto insieme ai nomi.

Regole proprie del formato:

- **L'intervistatore non spiega mai.** Non aggiunge dati, non completa una
  risposta, non commenta nel merito. Puo' reagire a quello che ha appena
  sentito ("dieci per cento e' tanto") **solo** come rampa verso la domanda che
  segue, mai come contributo a se' stante.
- **Ogni turno dell'intervistatore contiene una domanda.** Se non ce l'ha, quel
  turno non ha motivo di esistere: o diventa una domanda o sparisce.
- **L'intervistato non rimanda domande.** Non chiede "e tu che ne pensi?": i
  ruoli non si scambiano a meta' episodio.
- L'intervistatore apre l'episodio presentando l'argomento e chiude chiedendo
  la conclusione. L'ultima parola e' dell'intervistato.

#### Deroga alla REGOLA A — alternanza

In `intervista` la regola dell'alternanza naturale (`si interrompono,
rilanciano, si fanno domande`) **e' sostituita da questa**:

> Gli speaker si alternano in modo rigido, un turno ciascuno. Non si
> interrompono e non si scambiano i ruoli. La varieta' che in `conversazione`
> arriva dall'alternanza, qui deve arrivare **dal tipo di domanda**: una
> richiesta di dati, una di spiegazione, un'obiezione, una richiesta di
> esempio, un dubbio. Una fila di domande tutte uguali e' il modo in cui questo
> formato suona peggio di una conversazione.

Resta invece **pienamente valida** la regola che vieta le domande di due o tre
parole — e anzi qui conta il doppio, perche' in `intervista` quasi ogni turno
di un ruolo e' una domanda. Ogni domanda va tenuta nello stesso turno della
frase che la introduce.

#### Deroga alla REGOLA B — dosaggio delle tag

In `intervista` la regola del dosaggio (`una o due per turno, e non su ogni
turno`) **e' sostituita da questa**:

> Sui turni dell'**intervistatore** la tag sulla domanda e' ammessa anche su
> ogni turno: li' non e' un accento, e' l'unica cosa che distingue una domanda
> dall'altra quando il turno e' corto. Vale **una sola tag per turno**, e va
> variata (`[incalzante]`, `[enfasi sulla domanda]`, `[incuriosito]`,
> `[dubbioso]`): la stessa tag ripetuta trenta volte produce la cantilena che
> la regola generale vuole evitare.
>
> Sui turni dell'**intervistato** la REGOLA B vale come scritta, senza deroghe:
> una o due tag per turno, non su ogni turno.

## Presentazione iniziale (solo se richiesta)

Se ti viene indicata una **voce di presentazione**, e' una terza voce, distinta
dai due che dialogano. Il copione si apre con uno o due turni suoi, **prima**
che parli chiunque altro, e dopo non parla piu'. Sono turni come gli altri
(stesso JSON, stesso `speaker`/`testo`): nessuna chiave in piu'.

- **Presenta tutti i partecipanti, per nome e cognome**, esattamente come ti
  vengono dati (il campo "nome pronunciato"), e dice **chi sono e di cosa si
  occupano** usando le informazioni fornite. Non aggiungi titoli, ruoli o
  dettagli che non ti sono stati dati: se ne manca uno, non lo inventi.
- Una presentazione e' breve: qualche frase, non un curriculum. Il ruolo si
  dice in modo parlato ("responsabile del reparto acquisti"), non come un elenco.
- Se l'episodio ha un'intro con parlato, non ripetere quello che dice gia'
  (vedi la sezione sulle musiche): la presentazione comincia da dove l'intro finisce.
- Il dialogo che segue **non si ripresenta**: nessun "ciao, sono Mario" ne'
  "ci presentiamo". Entra subito nel merito, come se l'ascoltatore li avesse gia'
  conosciuti, e il saluto fra i due, se serve, e' una battuta.
- I turni del presentatore **non contano** per le regole dei formati: non e'
  ne' intervistatore ne' intervistato, e le regole su alternanza e domande
  valgono solo per i due che dialogano.

## Musiche: parlato in intro o outro

Se all'episodio e' stata associata un'intro o un'outro che contiene gia' del
parlato (un benvenuto, il nome del podcast, i saluti finali), te lo diranno
insieme alla trascrizione di quel parlato, come contesto prima del documento.
**La trascrizione e' informazione per te, non testo da includere nel
copione**: ti dice cosa l'ascoltatore ha gia' sentito prima che il copione
cominci (o sentira' subito dopo che finisce), non va ripetuta ne' parafrasata.

- Se l'intro si presenta gia' e saluta, il copione non apre con un altro
  benvenuto: puo' entrare direttamente nel merito.
- Se l'outro contiene gia' i saluti finali, il copione non li richiude di
  nuovo: puo' fermarsi alla conclusione dei contenuti.
- Il sottofondo non ha mai parlato (e' solo musica): non richiede nessun
  accorgimento nel copione.
"""


def load_system_prompt(prompt_file: Path = PROMPT_FILE, lessons_file: Path = LESSONS_FILE) -> str:
    """Regole di scrittura del copione, da prompts/copione.md, piu' le lezioni
    approvate dagli episodi precedenti (prompts/lezioni.md) se ce ne sono.

    Fonte unica condivisa con il subagent: se cambi lo stile del podcast, lo
    cambi li' e vale per entrambi i percorsi.

    Senza prompts/lezioni.md (o con il file vuoto) il prompt e' ESATTAMENTE
    quello di sempre: nessuna sezione in piu', nessun byte in piu'. E'
    intenzionale — vedi src/lessons.py, "Criteri di accettazione".

    I due percorsi sono parametri (con default sui file veri del progetto)
    solo perche' i test devono poter verificare il comportamento con e senza
    lezioni.md senza toccare il prompts/ vero.
    """
    try:
        text = prompt_file.read_text(encoding="utf-8").strip()
    except OSError:
        text = _FALLBACK_SYSTEM_PROMPT
    if not text:
        text = _FALLBACK_SYSTEM_PROMPT

    try:
        lessons_text = lessons_file.read_text(encoding="utf-8").strip()
    except OSError:
        lessons_text = ""
    if lessons_text:
        # lezioni.md porta gia' il proprio titolo "# Lezioni dagli episodi
        # precedenti" (src/lessons.py:render_lezioni_md): non se ne aggiunge
        # un altro qui, altrimenti la sezione avrebbe due intestazioni.
        text = f"{text}\n\n{lessons_text}"
    return text


#: Risolto all'import per non rileggere il file a ogni chiamata.
SYSTEM_PROMPT = load_system_prompt()


#: Lunghezza massima della trascrizione di un brano nel prompt.
MAX_TRANSCRIPT_CHARS = 500


def _quoted_transcript(text: str) -> str:
    """La trascrizione come DATO fra virgolette: una riga sola, senza virgolette
    doppie ne' delimitatori '---', limitata. E' scritta dall'utente nella
    libreria Musiche: non deve poter uscire dal blocco CONTESTO MUSICALE
    (revisione di sicurezza C4, BASSA-3)."""
    flat = " ".join(str(text).split()).replace('"', "'")
    while "---" in flat:
        flat = flat.replace("---", "-")
    return flat[:MAX_TRANSCRIPT_CHARS]


def _render_music_context(music_context: dict[str, Any] | None) -> str:
    """Il blocco --- CONTESTO MUSICALE --- del prompt, o stringa vuota.

    Solo intro/outro con `parlato: true` hanno senso qui (vedi
    prompts/copione.md): la trascrizione e' INFORMAZIONE per non farla
    ripetere nel copione, non testo da includere. Il sottofondo non ha
    parlato per definizione (src/music_library.py lo tratta come errore di
    dominio se lo ha), quindi non compare mai in questo blocco.
    """
    if not music_context:
        return ""
    labels = {
        "intro": ("L'episodio si apre con un'intro", "l'intro"),
        "outro": ("L'episodio si chiude con un'outro", "l'outro"),
    }
    parts: list[str] = []
    for category, (intro_label, short_name) in labels.items():
        entry = music_context.get(category)
        if not entry or not entry.get("trascrizione"):
            continue
        trascrizione = _quoted_transcript(entry["trascrizione"])
        parts.append(
            f"{intro_label} che contiene gia' del parlato:\n"
            f'"{trascrizione}"\n'
            f"Non ripetere nel copione quello che {short_name} dice gia'."
        )
    if not parts:
        return ""
    return "\n--- CONTESTO MUSICALE ---\n" + "\n\n".join(parts) + "\n--- FINE CONTESTO MUSICALE ---\n"


def _render_format(
    script_format: str, speakers: list[str], interviewer: str | None, presenter: str | None
) -> str:
    """La riga "Formato:" quando il formato e' stato scelto esplicitamente."""
    dialogue = [s for s in speakers if s != presenter]
    if script_format == "intervista":
        others = [s for s in dialogue if s != interviewer]
        return (
            "intervista (segui le regole del formato `intervista`).\n"
            f"Fa le domande (intervistatore): {interviewer}\n"
            f"Risponde (intervistato): {', '.join(others)}"
        )
    return (
        "conversazione (segui le regole del formato `conversazione`): "
        f"{' e '.join(dialogue)} discutono alla pari"
    )


def _render_presentation(presenter: str | None, participants: list[dict[str, str]] | None) -> str:
    """Il blocco sulla voce di presentazione, o stringa vuota."""
    if not presenter:
        return ""
    people = "\n".join(
        f'- "{p["nome"]}" (speaker {p["speaker"]}): {p["ruolo"]}' for p in (participants or [])
    )
    return (
        f"\nVoce di presentazione: {presenter}. Apre l'episodio e poi non parla piu' "
        "(vedi \"Presentazione iniziale\" nelle regole). Presenta, con il nome "
        "esattamente come scritto fra virgolette e con il loro ruolo:\n"
        f"{people}\n"
    )


@dataclass
class ScriptWriter:
    """Mantiene la conversazione con Claude per poter iterare sulle revisioni."""

    api_key: str = ""
    model: str = "claude-opus-5"
    max_tokens: int = 16000
    effort: str = "high"
    language: str = "italiano"
    target_minutes: int = 8
    words_per_minute: int = 150
    max_document_chars: int = 120000
    #: Client gia' pronto (vedi Config.anthropic_client): se c'e', `api_key`
    #: non serve. Iniettabile per i test, che non devono mai chiamare l'API.
    client: Any = field(default=None, repr=False)
    _client: Any = field(init=False, repr=False)
    _messages: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = self.client if self.client is not None else anthropic.Anthropic(api_key=self.api_key)

    # -- API pubblica -----------------------------------------------------

    def build_prompt(
        self,
        document: Document,
        speakers: list[str],
        music_context: dict[str, Any] | None = None,
        *,
        script_format: str | None = None,
        interviewer: str | None = None,
        presenter: str | None = None,
        participants: list[dict[str, str]] | None = None,
    ) -> tuple[str, bool]:
        """Costruisce il prompt iniziale. Pubblico perche' ScriptSession lo salva
        su disco: e' il "compito" da cui si ricostruisce la conversazione quando
        scrittura e revisione avvengono in processi separati.

        `music_context` (opzionale): {"intro": {"trascrizione": str}, "outro":
        {...}} SOLO per le categorie con parlato: true — vedi
        prompts/copione.md, sezione sul parlato in intro/outro. Va nel prompt
        e non passato a parte perche' ScriptSession salva `prompt` per
        intero: cosi' anche le revisioni successive lo ritrovano senza dover
        essere ripassato ogni volta.

        `script_format` / `interviewer` / `presenter` / `participants`: le
        stesse scelte che il subagent passa a tools/script_save.py, con lo
        stesso significato (vedi prompts/copione.md, "Formati" e
        "Presentazione iniziale"). Senza `script_format` il prompt e' quello
        di sempre (monologo / dialogo / tavola rotonda secondo il numero di
        speaker): run.py e le chiamate senza configurazione non cambiano.
        """
        text, truncated = document.truncated(self.max_document_chars)
        target_words = self.target_minutes * self.words_per_minute
        note = (
            "\n\n[NOTA: il documento e' stato troncato per lunghezza; lavora su "
            "quello che vedi.]"
            if truncated
            else ""
        )
        speaker_block = "\n".join(f"- {s}" for s in speakers)
        if script_format:
            formato = _render_format(script_format, speakers, interviewer, presenter)
        else:
            formato = (
                "un monologo con un solo speaker"
                if len(speakers) == 1
                else f"un dialogo fra {len(speakers)} speaker"
                if len(speakers) == 2
                else f"una tavola rotonda con {len(speakers)} speaker"
            )
        presentation = _render_presentation(presenter, participants)
        music_block = _render_music_context(music_context)

        prompt = f"""Scrivi il copione di un episodio di podcast in {self.language} a partire dal documento qui sotto.

Formato: {formato}.
Speaker disponibili (usa esattamente questi nomi, tutti almeno una volta):
{speaker_block}
{presentation}
Durata indicativa: circa {self.target_minutes} minuti di parlato, cioe' all'incirca {target_words} parole complessive.
{music_block}
--- DOCUMENTO ---
{text}
--- FINE DOCUMENTO ---{note}"""
        return prompt, truncated

    def write(
        self,
        document: Document,
        speakers: list[str],
        music_context: dict[str, Any] | None = None,
    ) -> PodcastScript:
        prompt, _ = self.build_prompt(document, speakers, music_context)
        return self.run(prompt)

    def run(self, prompt: str) -> PodcastScript:
        """Avvia una conversazione nuova a partire da un prompt gia' costruito."""
        self._messages = [{"role": "user", "content": prompt}]
        return self._generate()

    def run_messages(self, messages: list[dict[str, Any]]) -> PodcastScript:
        """Esegue una conversazione ricostruita da fuori (vedi ScriptSession).

        Serve al subagent: la revisione avviene in un processo diverso da quello
        che ha scritto il copione, quindi i messaggi arrivano dal disco invece
        che dalla memoria.
        """
        if not messages:
            raise ScriptWriterError("Nessun messaggio da inviare al modello.")
        self._messages = list(messages)
        return self._generate()

    def revise(self, instructions: str) -> PodcastScript:
        if not self._messages:
            raise ScriptWriterError(
                "Nessuno script da revisionare: chiama prima write()."
            )
        self._messages.append(
            {
                "role": "user",
                "content": (
                    "Rivedi il copione secondo queste indicazioni e restituiscilo "
                    "per intero, non solo le parti modificate:\n\n"
                    f"{instructions.strip()}"
                ),
            }
        )
        return self._generate()

    # -- interno ----------------------------------------------------------

    def _generate(self) -> PodcastScript:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=self._messages,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                },
            )
        except anthropic.AnthropicError as exc:
            raise classify_api_error(exc) from exc

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise ScriptWriterError(
                "Claude ha rifiutato di scrivere il copione per questo documento"
                + (f" (categoria: {detail.category})" if detail else "")
                + "."
            )
        if response.stop_reason == "max_tokens":
            raise ScriptWriterError(
                f"Lo script ha superato il limite di {self.max_tokens} token e "
                "sarebbe troncato. Aumenta script.max_tokens in config.yaml oppure "
                "riduci script.target_minutes."
            )

        text = next(
            (block.text for block in response.content if block.type == "text"), ""
        )
        if not text.strip():
            raise ScriptWriterError("Claude ha restituito una risposta vuota.")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ScriptWriterError(
                f"La risposta di Claude non e' JSON valido: {exc}"
            ) from exc

        self._messages.append({"role": "assistant", "content": text})
        return PodcastScript.from_dict(payload)


__all__ = [
    "ScriptWriter",
    "PodcastScript",
    "ScriptWriterError",
    "ModelAccessError",
    "ModelTemporarilyUnavailable",
    "classify_api_error",
    "INLINE_TAG",
    "spoken_text",
]
