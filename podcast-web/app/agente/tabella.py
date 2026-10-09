"""TABELLA DICHIARATIVA dei comandi dell'agente.

Ogni voce e' un'AZIONE (non un tool): gli argomenti ammessi con il loro tipo, il
timeout del contratto ("Processi, tempi e coda"), se e' un lavoro lungo (va in
coda), il permesso che serve e quali esiti 3 si confermano con un gettone.
Cio' che non e' in tabella non parte (`app/agente/esecutore.py`).

Regole di costruzione della riga di comando (`costruisci_argomenti`):
- argomenti sempre in lista, mai shell;
- valori nella forma `--opzione=valore`; posizionali dopo `--`;
- eccezione: le opzioni a piu' valori (`--none intro outro`, `--diff 1 3`) si
  passano come token separati, ma solo con valori da un elenco chiuso o interi;
- `--by` solo dall'utente dell'ingresso, mai da un dato del browser;
- i flag di conferma solo da un gettone valido (`conferme`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# ----------------------------------------------------------------------
# Tipi di argomento
# ----------------------------------------------------------------------

RUN = "run"            # nome di una run: sottocartella reale di output/
NOME = "nome"          # nome di documento, voce o brano
TESTO = "testo"        # testo libero dell'utente
INTERO = "intero"
SCELTA = "scelta"
SCELTE = "scelte"      # piu' valori da un elenco chiuso, token separati
INTERI = "interi"      # piu' interi, token separati
FLAG = "flag"
RUOLO_VALORE = "ruolo_valore"  # {ruolo: valore} -> --opz=RUOLO=valore ripetuto
RUOLI_ELENCO = "ruoli_elenco"  # [ruolo, ...] -> --opz=RUOLO ripetuto
SCELTA_RIPETUTA = "scelta_ripetuta"  # [scelta, ...] -> --opz=scelta ripetuto

_RE_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")
_CONTROLLO = re.compile(r"[\x00-\x1f\x7f]")
_CONTROLLO_TESTO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # ammessi \t \n \r
NOME_MAX = 200

RUOLI_EPISODIO = ("presentatore", "intervistatore", "ospite", "voce_a", "voce_b")
CATEGORIE_MUSICA = ("intro", "outro", "sottofondo")
INDICAZIONI_RETAKE = ("Più lento", "Più veloce", "Più energico", "Più calmo", "Più serio", "Più sorridente", "Più pause")


class ErroreArgomento(Exception):
    """Un valore non rispetta il tipo dichiarato: la richiesta e' da rifiutare (400).

    `campo` e' il nome del parametro, da mostrare accanto al campo nella UI."""

    def __init__(self, campo: str, messaggio: str) -> None:
        super().__init__(messaggio)
        self.campo = campo
        self.messaggio = messaggio


class ErroreTabella(Exception):
    """Errore di programmazione del controller (parametro sconosciuto, azione
    inesistente): 500, nel log."""


class RunNonTrovata(Exception):
    def __init__(self, run: str) -> None:
        super().__init__(run)
        self.run = run


@dataclass(frozen=True)
class Arg:
    param: str                      # nome del parametro lato controller
    opzione: str | None             # "--run-dir"; None = posizionale (dopo --)
    tipo: str
    obbligatorio: bool = False
    scelte: tuple[str, ...] = ()
    minimo: int | None = None
    massimo: int | None = None
    max_len: int = 2000
    valore: str = TESTO             # per RUOLO_VALORE: tipo del valore (NOME o TESTO)


@dataclass(frozen=True)
class Conferma:
    """Cosa sblocca un gettone per un dato error_type di esito 3.

    - `flag`: si rilancia la stessa azione con gli stessi argomenti + il flag;
    - `azione` + `da_details`: si lancia un'ALTRA azione, con parametri presi da
      `details` dell'esito 3 (es. CloneOutcomeUnknown -> ritenta_clonazione);
    - `scelta`: (`param`, chiave in details con i candidati, campo dell'id) per
      gli esiti che chiedono di scegliere fra candidati (LessonRemovalRequired).
    """

    flag: str | None = None
    azione: str | None = None
    da_details: tuple[tuple[str, str], ...] = ()
    scelta: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class Comando:
    tool: str
    argomenti: tuple[Arg, ...] = ()
    fissi: tuple[str, ...] = ()     # flag sempre presenti (--events, --revoke, --list…)
    timeout: float | None = 30
    lungo: bool = False
    permesso: str = "leggere"
    by: bool = False                # aggiunge --by=<utente dell'ingresso>
    conferme: Mapping[str, Conferma] = field(default_factory=dict)
    registra: bool = False          # scrive nel registro attivita'
    solo_da_gettone: bool = False   # non si lancia da una rotta, solo da una conferma
    fase: str = "A2"


def _run(obbligatorio: bool = True) -> Arg:
    return Arg("run", "--run-dir", RUN, obbligatorio=obbligatorio)


MUSIC_CHANGED = {"MusicChanged": Conferma(flag="--accept-changed-music")}

TABELLA: dict[str, Comando] = {
    # --- Archivio ---------------------------------------------------------
    "archivio": Comando("runs_list.py", (
        Arg("gruppo", "--status", SCELTA, scelte=("bozze", "da_approvare", "in_produzione", "pronti")),
        Arg("cerca", "--search", TESTO, max_len=200),
    )),
    "stato_run": Comando("run_status.py", (_run(),)),
    "crea_episodio": Comando("run_init.py", (
        Arg("titolo", "--title", TESTO, obbligatorio=True, max_len=200),
        Arg("documento", "--document", NOME),
    ), timeout=120, permesso="episodio_modifica", registra=True),
    # --- Passo 1 · Documento ---------------------------------------------
    "documenti": Comando("documents_list.py", (
        Arg("filtro", "--filter", SCELTA, scelte=("mai_usati", "gia_usati", "con_problemi")),
        Arg("cerca", "--search", TESTO, max_len=200),
    ), timeout=40),
    "documento": Comando("documents_list.py", (Arg("nome", "--name", NOME, obbligatorio=True),), timeout=240),
    "anteprima_documento": Comando("doc_extract.py", (
        Arg("caratteri", "--preview-chars", INTERO, minimo=0, massimo=5000),
        Arg("nome", None, NOME, obbligatorio=True),
    ), timeout=120),
    "cambia_documento": Comando("episode_config.py", (
        _run(), Arg("documento", "--documento", NOME, obbligatorio=True),
    ), timeout=120, permesso="episodio_modifica", registra=True),
    # --- Passo 2 · Cast e formato ------------------------------------------
    "leggi_configurazione": Comando("episode_config.py", (_run(),)),
    "modifica_configurazione": Comando("episode_config.py", (
        _run(),
        Arg("titolo", "--titolo", TESTO, max_len=200),
        Arg("formato", "--formato", SCELTA, scelte=("conversazione", "intervista")),
        Arg("durata", "--durata", TESTO, max_len=8),
        Arg("minuti", "--minuti", INTERO, minimo=1, massimo=180),
        Arg("voci", "--voce", RUOLO_VALORE, scelte=RUOLI_EPISODIO, valore=NOME),
        Arg("nomi", "--nome", RUOLO_VALORE, scelte=RUOLI_EPISODIO, max_len=100),
        Arg("descrizioni", "--descrizione", RUOLO_VALORE, scelte=RUOLI_EPISODIO, max_len=300),
        Arg("togli", "--togli", RUOLI_ELENCO, scelte=RUOLI_EPISODIO),
    ), permesso="episodio_modifica", registra=True,
        conferme={"SpokenNameIncomplete": Conferma(flag="--accetta-nome-incompleto")}),
    "voci": Comando("voices_list.py", (
        Arg("filtro", "--filter", SCELTA, scelte=("clonate", "da_clonare", "consenso_da_confermare")),
        Arg("cerca", "--search", TESTO, max_len=200),
    )),
    # --- Passo 3 · Musiche -------------------------------------------------
    "musiche": Comando("music_list.py", (
        Arg("filtro", "--filter", SCELTA, scelte=("strumentali", "con_parlato", "licenza_mancante")),
        Arg("cerca", "--search", TESTO, max_len=200),
    )),
    "scegli_musiche": Comando("music_set.py", (
        _run(),
        Arg("intro", "--intro", NOME),
        Arg("outro", "--outro", NOME),
        Arg("sottofondo", "--sottofondo", NOME),
        Arg("nessuna", "--none", SCELTE, scelte=CATEGORIE_MUSICA),
    ), permesso="episodio_modifica", registra=True),
    # --- Passo 4 · Copione -------------------------------------------------
    "scrivi_copione": Comando("script_write.py", (_run(),), timeout=600, lungo=True,
                              permesso="copione_scrivi", by=True, registra=True),
    "revisiona_copione": Comando("script_revise.py", (
        _run(), Arg("istruzioni", "--instructions", TESTO, obbligatorio=True, max_len=4000),
    ), timeout=600, lungo=True, permesso="copione_scrivi", by=True, registra=True),
    "copione": Comando("script_show.py", (_run(),)),
    "modifica_battuta": Comando("script_edit.py", (
        _run(),
        Arg("battuta", "--turn", INTERO, obbligatorio=True, minimo=1, massimo=100000),
        Arg("testo", "--text", TESTO, obbligatorio=True, max_len=5000),
        Arg("versione_attesa", "--expected-version", INTERO, obbligatorio=True, minimo=1, massimo=1000000),
    ), permesso="copione_modifica", by=True, registra=True),
    "approva_copione": Comando("script_approve.py", (
        _run(), Arg("versione", "--version", INTERO, obbligatorio=True, minimo=1, massimo=1000000),
    ), permesso="copione_approva", by=True, registra=True),
    "ritira_approvazione": Comando("script_approve.py", (_run(),), fissi=("--revoke",),
                                   permesso="copione_approva", by=True, registra=True),
    "versioni": Comando("script_versions.py", (_run(),), fissi=("--list",)),
    "ripristina_versione": Comando("script_versions.py", (
        _run(),
        Arg("versione", "--restore", INTERO, obbligatorio=True, minimo=1, massimo=1000000),
        Arg("versione_attesa", "--expected-version", INTERO, obbligatorio=True, minimo=1, massimo=1000000),
    ), permesso="copione_modifica", by=True, registra=True),
    "mostra_versione": Comando("script_versions.py", (
        _run(), Arg("versione", "--show", INTERO, obbligatorio=True, minimo=1, massimo=1000000),
    ), fase="A5"),
    "confronta_versioni": Comando("script_versions.py", (
        _run(),
        Arg("versioni", "--diff", INTERI, obbligatorio=True, minimo=1, massimo=1000000),
        Arg("solo_modifiche", "--only-changes", FLAG),
    ), fase="A5"),
    # --- Passo 5 · Stima e consenso ----------------------------------------
    "stima": Comando("estimate.py", (_run(),)),
    "stato_voce": Comando("voice_meta.py", (Arg("nome", "--name", NOME, obbligatorio=True),)),
    "registra_consenso": Comando("voice_meta.py", (
        Arg("nome", "--name", NOME, obbligatorio=True),
        Arg("audio_sha256", "--expect-sha256", TESTO, obbligatorio=True, max_len=100),
        Arg("revisione", "--expect-revision", TESTO, obbligatorio=True, max_len=100),
    ), fissi=("--consent",), permesso="voce_consenso", by=True, registra=True),
    "revoca_consenso": Comando("voice_meta.py", (Arg("nome", "--name", NOME, obbligatorio=True),),
                               fissi=("--revoke-consent",), permesso="voce_consenso", by=True, registra=True),
    "ritenta_clonazione": Comando("voice_meta.py", (Arg("nome", "--name", NOME, obbligatorio=True),),
                                  fissi=("--confirm-clone-retry",), timeout=660, permesso="voce_clonazione",
                                  by=True, registra=True, solo_da_gettone=True),
    # --- Passo 6 · Generazione ---------------------------------------------
    "produci": Comando("produce.py", (_run(),), fissi=("--events",), timeout=None, lungo=True,
                       permesso="produrre", registra=True, conferme={
                           **MUSIC_CHANGED,
                           "CloneOutcomeUnknown": Conferma(azione="ritenta_clonazione", da_details=(("nome", "speaker"),)),
                       }),
    # --- Passo 7 · Episodio ------------------------------------------------
    "episodio": Comando("episode_info.py", (_run(),)),
    "forma_onda": Comando("waveform.py", (
        _run(), Arg("punti", "--points", SCELTA, obbligatorio=True, scelte=("50", "100", "200", "400", "800", "1600")),
    ), timeout=900, fase="A5"),
    "battuta_al_tempo": Comando("turn_at.py", (_run(), Arg("al", "--at", TESTO, obbligatorio=True, max_len=12)), fase="A5"),
    "stima_retake": Comando("retake.py", (
        _run(),
        Arg("battuta", "--turn", INTERO, obbligatorio=True, minimo=1, massimo=100000),
        Arg("take", "--takes", INTERO, obbligatorio=True, minimo=1, massimo=3),
        Arg("indicazioni", "--direction", SCELTA_RIPETUTA, scelte=INDICAZIONI_RETAKE),
    ), fissi=("--dry-run",), fase="A5"),
    "genera_retake": Comando("retake.py", (
        _run(),
        Arg("battuta", "--turn", INTERO, obbligatorio=True, minimo=1, massimo=100000),
        Arg("take", "--takes", INTERO, obbligatorio=True, minimo=1, massimo=3),
        Arg("indicazioni", "--direction", SCELTA_RIPETUTA, scelte=INDICAZIONI_RETAKE),
    ), timeout=None, lungo=True, permesso="produrre", by=True, registra=True, fase="A5"),
    "ascolta_take": Comando("retake.py", (
        _run(),
        Arg("battuta", "--list", INTERO, obbligatorio=True, minimo=1, massimo=100000),
        Arg("con_musica", "--with-music", FLAG),
    ), timeout=900, fase="A5"),
    "scegli_take": Comando("retake.py", (
        _run(), Arg("scelta", "--choose", TESTO, obbligatorio=True, max_len=20),
    ), timeout=60, permesso="episodio_modifica", registra=True, fase="A5"),
    "rimonta": Comando("mix.py", (_run(),), timeout=900, permesso="episodio_modifica", registra=True,
                       conferme=MUSIC_CHANGED, fase="A5"),
    # --- Librerie (A5) -----------------------------------------------------
    "nome_voce": Comando("voice_meta.py", (
        Arg("nome", "--name", NOME, obbligatorio=True),
        Arg("nome_pronunciato", "--spoken-name", TESTO, obbligatorio=True, max_len=100),
    ), permesso="voce_nome", by=True, registra=True,
        conferme={"SpokenNameIncomplete": Conferma(flag="--accept-incomplete-name")}, fase="A5"),
    "metadati_brano": Comando("music_meta.py", (
        Arg("categoria", "--category", SCELTA, obbligatorio=True, scelte=CATEGORIE_MUSICA),
        Arg("nome", "--name", NOME, obbligatorio=True),
        Arg("licenza", "--licenza", TESTO, max_len=2000),
        Arg("senza_licenza", "--no-licenza", FLAG),
        Arg("parlato", "--parlato", SCELTA, scelte=("true", "false")),
        Arg("trascrizione", "--trascrizione", TESTO, max_len=500),
    ), permesso="brano_metadati", by=True, registra=True, fase="A5"),
    # --- Avanzate (A5) -----------------------------------------------------
    "lezioni_elenco": Comando("lessons.py", (), fissi=("--list",), permesso="lezioni", fase="A5"),
    "lezioni_proponi_stima": Comando("lessons.py", (), fissi=("--propose", "--dry-run"), permesso="lezioni", fase="A5"),
    "lezioni_proponi": Comando("lessons.py", (), fissi=("--propose",), timeout=300, lungo=True,
                               permesso="lezioni", registra=True, fase="A5"),
    "lezioni_accetta": Comando("lessons.py", (
        Arg("id", "--accept", TESTO, obbligatorio=True, max_len=20),
        Arg("testo", "--text", TESTO, max_len=400),
    ), permesso="lezioni", by=True, registra=True, fase="A5",
        conferme={"LessonRemovalRequired": Conferma(scelta=("remove_id", "candidates", "id"))}),
    "lezioni_rifiuta": Comando("lessons.py", (
        Arg("id", "--reject", TESTO, obbligatorio=True, max_len=20),
        Arg("motivo", "--reason", TESTO, obbligatorio=True, max_len=400),
    ), permesso="lezioni", by=True, registra=True, fase="A5"),
    "impostazioni": Comando("settings_show.py", (), permesso="diagnostica"),
    "diagnostica": Comando("doctor.py", (), fissi=("--web",), timeout=60, permesso="diagnostica"),
}

# Opzione nascosta per LessonRemovalRequired: non si passa mai dal browser.
_OPZIONI_DA_SCELTA = {"remove_id": "--remove-id"}

# Tool che il controller non lancia MAI (il contratto li esclude dal web).
TOOL_VIETATI = frozenset({"voices_prepare.py", "synthesize.py", "run_finalize.py", "script_save.py", "signals.py"})
OPZIONI_VIETATE = frozenset({
    "--config", "--trusted-paths", "--no-approval-gate", "--no-consent-gate", "--save-to", "--text-file",
    "--session", "--script-json", "--script", "--out", "--voices", "--audio-paths", "--current",
})


def verifica_tabella() -> None:
    """Controllo di coerenza all'avvio e nei test: nessun tool o opzione vietata."""
    from app.servizi.permessi import PERMESSI

    for nome, cmd in TABELLA.items():
        if cmd.tool in TOOL_VIETATI or "/" in cmd.tool or not cmd.tool.endswith(".py"):
            raise ErroreTabella(f"{nome}: tool non ammesso {cmd.tool}")
        if cmd.permesso not in PERMESSI:
            raise ErroreTabella(f"{nome}: permesso sconosciuto {cmd.permesso}")
        opzioni = [a.opzione for a in cmd.argomenti if a.opzione] + list(cmd.fissi)
        opzioni += [c.flag for c in cmd.conferme.values() if c.flag]
        for opz in opzioni:
            if opz in OPZIONI_VIETATE or opz == "--by" or opz.startswith("--no-") and opz != "--no-licenza":
                raise ErroreTabella(f"{nome}: opzione non ammessa {opz}")
        for c in cmd.conferme.values():
            if c.azione and c.azione not in TABELLA:
                raise ErroreTabella(f"{nome}: conferma verso un'azione inesistente {c.azione}")
        posizionali = [a for a in cmd.argomenti if a.opzione is None]
        if posizionali and cmd.argomenti[-1].opzione is not None:
            raise ErroreTabella(f"{nome}: i posizionali vanno in fondo")


# ----------------------------------------------------------------------
# Controllo dei valori
# ----------------------------------------------------------------------


def controlla_nome(campo: str, valore: Any) -> str:
    """Nome di documento, voce o brano: niente separatori, `..`, caratteri di
    controllo, punto o trattino iniziali."""
    if not isinstance(valore, str):
        raise ErroreArgomento(campo, "serve un nome")
    if not valore or len(valore) > NOME_MAX or valore != valore.strip():
        raise ErroreArgomento(campo, "nome vuoto, troppo lungo o con spazi ai bordi")
    if "/" in valore or "\\" in valore or ".." in valore or _CONTROLLO.search(valore):
        raise ErroreArgomento(campo, "il nome contiene caratteri non ammessi")
    if valore[0] in ".-":
        raise ErroreArgomento(campo, "il nome non puo' cominciare con un punto o un trattino")
    return valore


def controlla_testo(campo: str, valore: Any, max_len: int) -> str:
    if not isinstance(valore, str):
        raise ErroreArgomento(campo, "serve un testo")
    if len(valore) > max_len:
        raise ErroreArgomento(campo, f"testo troppo lungo (massimo {max_len} caratteri)")
    if _CONTROLLO_TESTO.search(valore):
        raise ErroreArgomento(campo, "il testo contiene caratteri non ammessi")
    return valore


def controlla_run(valore: Any, output_dir: Path) -> str:
    """Una run valida e' una sottocartella reale (non link) di output/, come
    nell'elenco di runs_list.py. L'agente lo ricontrolla da se'."""
    if not isinstance(valore, str) or not _RE_RUN.match(valore) or ".." in valore:
        raise ErroreArgomento("run", "nome di episodio non valido")
    cartella = output_dir / valore
    if cartella.is_symlink() or not cartella.is_dir():
        raise RunNonTrovata(valore)
    return valore


def _intero(arg: Arg, valore: Any) -> int:
    if isinstance(valore, bool) or not isinstance(valore, int):
        if isinstance(valore, str) and valore.isdigit() and len(valore) < 10:
            valore = int(valore)
        else:
            raise ErroreArgomento(arg.param, "serve un numero intero")
    if arg.minimo is not None and valore < arg.minimo or arg.massimo is not None and valore > arg.massimo:
        raise ErroreArgomento(arg.param, f"numero fuori intervallo ({arg.minimo}-{arg.massimo})")
    return valore


def _scelta(arg: Arg, valore: Any) -> str:
    if isinstance(valore, int) and not isinstance(valore, bool):
        valore = str(valore)
    if not isinstance(valore, str) or valore not in arg.scelte:
        raise ErroreArgomento(arg.param, "valore non ammesso")
    return valore


def costruisci_argomenti(
    nome_azione: str,
    parametri: Mapping[str, Any],
    *,
    output_dir: Path,
    by: str | None = None,
    flag_conferme: tuple[str, ...] = (),
    parametri_nascosti: Mapping[str, str] | None = None,
) -> list[str]:
    """Riga di comando (senza python e tool) per un'azione della tabella.

    `parametri` arriva dalle rotte; `by` SOLO dall'ingresso; `flag_conferme` e
    `parametri_nascosti` SOLO da un gettone validato (`servizi/gettoni.py`)."""
    cmd = TABELLA.get(nome_azione)
    if cmd is None:
        raise ErroreTabella(f"azione fuori tabella: {nome_azione}")
    noti = {a.param for a in cmd.argomenti}
    ignoti = set(parametri) - noti
    if ignoti:
        raise ErroreTabella(f"{nome_azione}: parametri non in tabella {sorted(ignoti)}")
    consentiti = {c.flag for c in cmd.conferme.values() if c.flag}
    if not set(flag_conferme) <= consentiti:
        raise ErroreTabella(f"{nome_azione}: flag di conferma non previsto {flag_conferme}")

    argv: list[str] = []
    posizionali: list[str] = []
    for arg in cmd.argomenti:
        valore = parametri.get(arg.param)
        vuoto = valore is None or valore == [] or valore == {} or valore is False
        if vuoto:
            if arg.obbligatorio:
                raise ErroreArgomento(arg.param, "campo obbligatorio")
            continue
        if arg.tipo == RUN:
            argv.append(f"{arg.opzione}={controlla_run(valore, output_dir)}")
        elif arg.tipo == NOME:
            v = controlla_nome(arg.param, valore)
            if arg.opzione is None:
                posizionali.append(v)
            else:
                argv.append(f"{arg.opzione}={v}")
        elif arg.tipo == TESTO:
            argv.append(f"{arg.opzione}={controlla_testo(arg.param, valore, arg.max_len)}")
        elif arg.tipo == INTERO:
            argv.append(f"{arg.opzione}={_intero(arg, valore)}")
        elif arg.tipo == SCELTA:
            argv.append(f"{arg.opzione}={_scelta(arg, valore)}")
        elif arg.tipo == FLAG:
            if valore is not True:
                raise ErroreArgomento(arg.param, "serve vero o falso")
            argv.append(arg.opzione)
        elif arg.tipo in (SCELTE, INTERI):
            if not isinstance(valore, list) or len(valore) > 10:
                raise ErroreArgomento(arg.param, "serve un elenco")
            convertiti = [_scelta(arg, v) if arg.tipo == SCELTE else str(_intero(arg, v)) for v in valore]
            if arg.tipo == SCELTE:
                convertiti = list(dict.fromkeys(convertiti))
            argv.extend([arg.opzione, *convertiti])  # valori chiusi o interi: mai opzioni
        elif arg.tipo == SCELTA_RIPETUTA:
            if not isinstance(valore, list) or len(valore) > 10:
                raise ErroreArgomento(arg.param, "serve un elenco")
            argv.extend(f"{arg.opzione}={_scelta(arg, v)}" for v in dict.fromkeys(valore))
        elif arg.tipo == RUOLI_ELENCO:
            if not isinstance(valore, list):
                raise ErroreArgomento(arg.param, "serve un elenco di ruoli")
            argv.extend(f"{arg.opzione}={_scelta(arg, v)}" for v in dict.fromkeys(valore))
        elif arg.tipo == RUOLO_VALORE:
            if not isinstance(valore, dict):
                raise ErroreArgomento(arg.param, "serve un oggetto {ruolo: valore}")
            for ruolo, v in valore.items():
                if ruolo not in arg.scelte:
                    raise ErroreArgomento(arg.param, f"ruolo non ammesso: {ruolo}")
                v = controlla_nome(arg.param, v) if arg.valore == NOME else controlla_testo(arg.param, v, arg.max_len)
                argv.append(f"{arg.opzione}={ruolo}={v}")
        else:  # pragma: no cover - tabella incoerente
            raise ErroreTabella(f"tipo sconosciuto {arg.tipo}")

    argv.extend(cmd.fissi)
    for nome, valore in (parametri_nascosti or {}).items():
        opzione = _OPZIONI_DA_SCELTA.get(nome)
        if opzione is None:
            raise ErroreTabella(f"{nome_azione}: parametro nascosto sconosciuto {nome}")
        argv.append(f"{opzione}={controlla_testo(nome, valore, 40)}")
    argv.extend(flag_conferme)
    if cmd.by:
        if not by:
            raise ErroreTabella(f"{nome_azione}: manca l'utente per --by")
        argv.append(f"--by={controlla_testo('by', by, 100)}")
    if posizionali:
        argv.append("--")
        argv.extend(posizionali)
    return argv
