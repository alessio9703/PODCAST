"""Coda dei lavori lunghi: UN worker, nel processo uvicorn unico.

Lavori lunghi = azioni con `lungo=True` nella tabella (stesura, revisione,
produzione, retake, proposta di lezioni). Punti K16-K21 di A2-piano.md:

- un lavoro per run (il secondo -> 409), coda limitata (-> 503);
- permesso, `solo_da_gettone` e argomenti controllati PRIMA di accodare;
- registro: una riga all'accodamento (chi ha lanciato una spesa, con il costo
  stimato per la produzione) e una all'esito, entrambe con l'id del lavoro;
- esiti 3 con il gettone dell'utente che ha lanciato il lavoro;
- interruzione: un lavoro in attesa si toglie; uno in corso riceve UN solo
  SIGTERM (al processo, come dice il contratto); mai un secondo, mai SIGKILL.

Lo stato degli episodi non sta qui: qui c'e' solo cosa sta facendo il worker.
Dopo un riavvio la coda e' vuota e lo stato si legge dall'agente.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from fastapi.responses import JSONResponse

from app.agente import esecutore
from app.agente.tabella import TABELLA
from app.ingresso import Utente
from app.registro import adesso_iso
from app.servizi.errori import ErroreController
from app.servizi.stima import STIME

log = logging.getLogger("podcast_web.lavori")

STORICO_MAX = 50
ATTESA_SPEGNIMENTO_S = 120


@dataclass
class Lavoro:
    id: str
    azione: str
    run: str | None
    utente: Utente
    parametri: dict[str, Any]
    flag_conferme: tuple[str, ...]
    risposta_a: str | None
    stato: str = "in_coda"               # in_coda | in_corso | finito | annullato
    accodato_il: str = field(default_factory=adesso_iso)
    iniziato_il: str | None = None
    finito_il: str | None = None
    http: int | None = None
    esito: dict[str, Any] | None = None
    interruzione_richiesta: bool = False
    sigterm_inviato: bool = False
    impronta: str | None = None
    processo: asyncio.subprocess.Process | None = None
    fatto: asyncio.Event = field(default_factory=asyncio.Event)

    def vista(self, per: Utente | None = None, posizione: int | None = None) -> dict[str, Any]:
        """Cosa vede il browser. L'esito (con l'eventuale gettone) solo a chi ha lanciato il lavoro."""
        dati: dict[str, Any] = {
            "lavoro": self.id, "azione": self.azione, "run": self.run, "stato": self.stato,
            "di": self.utente.nome, "accodato_il": self.accodato_il, "iniziato_il": self.iniziato_il,
            "finito_il": self.finito_il, "interruzione_richiesta": self.interruzione_richiesta,
        }
        if posizione is not None:
            dati["posizione"] = posizione
        if self.esito is not None:
            if per is not None and per.id == self.utente.id:
                dati["esito"] = self.esito
                dati["http"] = self.http
            else:
                dati["esito"] = {k: v for k, v in self.esito.items() if k in ("ok", "esito", "error_type")}
        return dati


class Coda:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._attesa: deque[Lavoro] = deque()
        self._in_corso: Lavoro | None = None
        self._storico: deque[Lavoro] = deque(maxlen=STORICO_MAX)
        self._evento = asyncio.Event()
        self._cambio = asyncio.Condition()
        self._worker: asyncio.Task[None] | None = None
        self._compiti: set[asyncio.Task[Any]] = set()
        self._chiusura = False
        # Run con un accodamento in corso (fra i controlli e l'append c'e' l'await
        # della stima): contano come lavori, cosi' due richieste ravvicinate non passano entrambe.
        self._prenotate: set[str | None] = set()
        self._n_prenotate = 0

    # --- ciclo di vita ------------------------------------------------------

    def avvia(self) -> None:
        self._worker = asyncio.create_task(self._ciclo(), name="podcast-web-worker")

    async def chiudi(self) -> None:
        """Allo spegnimento: i lavori in attesa si annullano, quello in corso riceve
        UN SIGTERM (se non l'ha gia' avuto) e lo si aspetta. Mai SIGKILL."""
        self._chiusura = True
        while self._attesa:
            lavoro = self._attesa.popleft()
            self._chiudi_annullato(lavoro, "spegnimento del servizio")
        lavoro = self._in_corso
        if lavoro is not None:
            # Anche se il processo sta ancora partendo: la richiesta resta e il
            # worker manda il SIGTERM appena c'e' il processo.
            self._sigterm(lavoro, motivo="spegnimento del servizio")
            try:
                await asyncio.wait_for(lavoro.fatto.wait(), ATTESA_SPEGNIMENTO_S)
            except asyncio.TimeoutError:
                log.error("spegnimento: il lavoro %s non e' finito in %ss; resta il processo dell'agente "
                          "(il lock si libera quando finisce)", lavoro.id, ATTESA_SPEGNIMENTO_S)
        self._evento.set()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass

    # --- accodare -----------------------------------------------------------

    async def accoda(
        self, utente: Utente, azione: str, parametri: Mapping[str, Any], *,
        flag_conferme: tuple[str, ...] = (), risposta_a: str | None = None, da_gettone: bool = False,
        impronta: str | None = None,
    ) -> JSONResponse:
        from app.servizi.azioni import controlla_azione

        ctx = self._ctx
        if self._chiusura:
            raise ErroreController("NonPronto")
        controlla_azione(ctx, utente, azione, parametri, da_gettone=da_gettone, lungo=True,
                         flag_conferme=flag_conferme)
        run = parametri.get("run")
        if run is not None and (self.lavoro_della_run(run) is not None or run in self._prenotate):
            raise ErroreController("LavoroGiaInCorso", "Su questo episodio c'e' gia' un lavoro in coda o in corso.",
                                   {"run": run})
        if len(self._attesa) + self._n_prenotate >= ctx.config.coda_max:
            raise ErroreController("CodaPiena")
        # Da qui fino all'append non ci sono controlli: il posto e' prenotato.
        if run is not None:
            self._prenotate.add(run)
        self._n_prenotate += 1
        try:
            return await self._accoda_prenotato(utente, azione, parametri, run, flag_conferme, risposta_a, impronta)
        finally:
            self._prenotate.discard(run)
            self._n_prenotate -= 1

    async def _accoda_prenotato(self, utente: Utente, azione: str, parametri: Mapping[str, Any], run: str | None,
                                flag_conferme: tuple[str, ...], risposta_a: str | None,
                                impronta: str | None) -> JSONResponse:
        ctx = self._ctx

        costo_usd = costo_eur = None
        if azione in STIME:
            # Stima subito prima di accodare: il costo nel registro viene dall'agente,
            # mai dal browser, e deve essere quello che l'utente ha confermato (A4
            # MEDIA-1). Senza un costo il lavoro non parte (A2 BASSA-7): si risponde con
            # l'esito della stima.
            from app.servizi.esiti import valuta

            azione_stima, parametri_stima, calcola = STIME[azione]
            stima = await esecutore.esegui(ctx.config, azione_stima, parametri_stima(parametri))
            if not stima.ok:
                stato, corpo = valuta(stima, ctx.pulitore)
                corpo.pop("gettone", None)
                ctx.registro.scrivi(utente=utente, azione=azione, run=run, esito=corpo.get("error_type", "ErroreInterno"),
                                    http=stato, dettagli={"motivo": "stima non riuscita: lavoro non accodato"})
                return JSONResponse(corpo, status_code=stato)
            if not impronta or calcola(stima.json) != impronta:
                ctx.registro.scrivi(utente=utente, azione=azione, run=run, esito="StimaCambiata", http=409,
                                    dettagli={"motivo": "stima diversa da quella confermata: lavoro non accodato"})
                raise ErroreController("StimaCambiata")
            costo_usd = _numero(stima.json.get("cost_usd"))
            costo_eur = _numero(stima.json.get("cost_eur"))
            if costo_usd is None:
                log.error("stima senza cost_usd per %s (%s): lavoro non accodato", run, azione)
                ctx.registro.scrivi(utente=utente, azione=azione, run=run, esito="ErroreInterno", http=500,
                                    dettagli={"motivo": "stima senza costo: lavoro non accodato"})
                raise ErroreController("ErroreInterno", "La stima non ha dato un costo: il lavoro non e' partito.")

        lavoro = Lavoro(id=secrets.token_hex(8), azione=azione, run=run, utente=utente, parametri=dict(parametri),
                        flag_conferme=flag_conferme, risposta_a=risposta_a, impronta=impronta)
        dettagli: dict[str, Any] = {}
        if flag_conferme:
            dettagli["conferme"] = list(flag_conferme)
        if risposta_a:
            dettagli["risposta_a"] = risposta_a
        scritto = ctx.registro.scrivi(utente=utente, azione=azione, run=run, esito="accodato", lavoro=lavoro.id,
                                      costo_stimato_usd=costo_usd, costo_stimato_eur=costo_eur, dettagli=dettagli)
        self._attesa.append(lavoro)
        self._evento.set()
        await self._notifica()
        corpo: dict[str, Any] = {"ok": True, "dati": lavoro.vista(utente, posizione=self._posizione(lavoro))}
        if not scritto:
            from app.servizi.azioni import AVVISO_REGISTRO
            corpo["avviso"] = AVVISO_REGISTRO
        return JSONResponse(corpo, status_code=202)

    # --- consultare ---------------------------------------------------------

    def _posizione(self, lavoro: Lavoro) -> int | None:
        for i, l in enumerate(self._attesa):
            if l is lavoro:
                return i + 1 + (1 if self._in_corso else 0)
        return 0 if lavoro is self._in_corso else None

    def lavoro_in_corso(self) -> Lavoro | None:
        return self._in_corso

    def lavoro_della_run(self, run: str) -> Lavoro | None:
        if self._in_corso and self._in_corso.run == run:
            return self._in_corso
        return next((l for l in self._attesa if l.run == run), None)

    def ultimo_della_run(self, run: str) -> Lavoro | None:
        attivo = self.lavoro_della_run(run)
        if attivo:
            return attivo
        return next((l for l in reversed(self._storico) if l.run == run), None)

    def trova(self, id_lavoro: str) -> Lavoro | None:
        for l in [self._in_corso, *self._attesa, *self._storico]:
            if l is not None and l.id == id_lavoro:
                return l
        return None

    def elenco(self, per: Utente) -> dict[str, Any]:
        return {
            "in_corso": self._in_corso.vista(per, posizione=0) if self._in_corso else None,
            "in_attesa": [l.vista(per, posizione=self._posizione(l)) for l in self._attesa],
            "recenti": [l.vista(per) for l in reversed(self._storico) if l.utente.id == per.id][:20],
        }

    def vista_run(self, run: str, per: Utente) -> dict[str, Any] | None:
        lavoro = self.ultimo_della_run(run)
        return lavoro.vista(per, posizione=self._posizione(lavoro)) if lavoro else None

    async def aspetta_cambio(self, timeout: float) -> None:
        async with self._cambio:
            try:
                await asyncio.wait_for(self._cambio.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    async def _notifica(self) -> None:
        async with self._cambio:
            self._cambio.notify_all()

    # --- interrompere -------------------------------------------------------

    async def interrompi(self, run: str, utente: Utente) -> dict[str, Any]:
        lavoro = self.lavoro_della_run(run)
        if lavoro is None:
            raise ErroreController("NonTrovato", "Nessun lavoro in coda o in corso su questo episodio.")
        self._ctx.permessi.richiedi(utente, TABELLA[lavoro.azione].permesso)
        if lavoro.stato == "in_coda":
            self._attesa.remove(lavoro)
            self._chiudi_annullato(lavoro, f"tolto dalla coda da {utente.nome}", da=utente)
            await self._notifica()
            return {"lavoro": lavoro.id, "stato": "annullato"}
        if lavoro.interruzione_richiesta:
            return {"lavoro": lavoro.id, "stato": lavoro.stato, "messaggio": "Interruzione gia' richiesta."}
        self._sigterm(lavoro, motivo=f"richiesta da {utente.nome}")
        self._ctx.registro.scrivi(utente=utente, azione="interrompi", run=run, esito="richiesta", lavoro=lavoro.id,
                                  dettagli={"azione_interrotta": lavoro.azione})
        await self._notifica()
        return {"lavoro": lavoro.id, "stato": lavoro.stato, "messaggio": "Interruzione richiesta."}

    def _sigterm(self, lavoro: Lavoro, *, motivo: str) -> None:
        """UN solo SIGTERM per lavoro, al processo (non al gruppo), mai un secondo.

        Se il processo non e' ancora partito (richiesta arrivata durante l'avvio),
        resta la richiesta: il worker manda il SIGTERM appena il processo esiste."""
        lavoro.interruzione_richiesta = True
        if lavoro.sigterm_inviato:
            return
        if lavoro.processo and lavoro.processo.returncode is None:
            lavoro.sigterm_inviato = True
            log.info("lavoro %s (%s): SIGTERM, %s", lavoro.id, lavoro.azione, motivo)
            try:
                lavoro.processo.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            attesa = TABELLA[lavoro.azione].sigkill_dopo_s
            if attesa is not None:
                compito = asyncio.ensure_future(self._sigkill_dopo(lavoro.processo, attesa, lavoro.id))
                self._compiti.add(compito)
                compito.add_done_callback(self._compiti.discard)

    async def _sigkill_dopo(self, processo: asyncio.subprocess.Process, attesa: float, id_lavoro: str) -> None:
        """Solo per le azioni con `sigkill_dopo_s` (stesura, revisione, proposte: niente
        clonazione ne' sintesi): se dopo il SIGTERM non escono, SIGKILL al gruppo."""
        try:
            await asyncio.wait_for(processo.wait(), attesa)
        except asyncio.TimeoutError:
            log.error("lavoro %s: ancora vivo %ss dopo il SIGTERM, SIGKILL al gruppo", id_lavoro, attesa)
            try:
                os.killpg(processo.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _registra_esito_controller(self, lavoro: Lavoro, motivo: str) -> None:
        esito = (lavoro.esito or {}).get("error_type", "ErroreInterno")
        self._ctx.registro.scrivi(utente=lavoro.utente, azione=lavoro.azione, run=lavoro.run, esito=esito,
                                  http=lavoro.http, lavoro=lavoro.id, dettagli={"motivo": motivo})

    def _chiudi_annullato(self, lavoro: Lavoro, motivo: str, da: Utente | None = None) -> None:
        lavoro.stato = "annullato"
        lavoro.finito_il = adesso_iso()
        lavoro.esito = ErroreController("LavoroAnnullato", motivo).corpo()
        self._storico.append(lavoro)
        self._ctx.registro.scrivi(utente=da or lavoro.utente, azione=lavoro.azione, run=lavoro.run, esito="annullato",
                                  lavoro=lavoro.id, dettagli={"motivo": motivo})

    # --- worker -------------------------------------------------------------

    async def _ciclo(self) -> None:
        while not self._chiusura:
            if not self._attesa:
                self._evento.clear()
                await self._evento.wait()
                continue
            lavoro = self._attesa.popleft()
            self._in_corso = lavoro
            try:
                await self._esegui(lavoro)
            except asyncio.CancelledError:
                # Solo allo spegnimento, dopo il SIGTERM e l'attesa: l'esito vero
                # non si conosce, il registro lo dice (A2 BASSA-6).
                if lavoro.esito is None:
                    lavoro.http = 409
                    lavoro.esito = ErroreController(
                        "LavoroAnnullato", "Servizio spento prima della fine del lavoro: controlla l'episodio nell'Archivio.").corpo()
                    self._registra_esito_controller(lavoro, "spegnimento: esito non noto")
                raise
            except Exception:
                log.exception("lavoro %s: errore del controller", lavoro.id)
                lavoro.http, lavoro.esito = 500, ErroreController("ErroreInterno").corpo()
                self._registra_esito_controller(lavoro, "errore del controller")
            finally:
                lavoro.stato = "finito" if lavoro.stato != "annullato" else lavoro.stato
                lavoro.finito_il = lavoro.finito_il or adesso_iso()
                lavoro.processo = None
                self._storico.append(lavoro)
                self._in_corso = None
                lavoro.fatto.set()
                await self._notifica()

    async def _esegui(self, lavoro: Lavoro) -> None:
        from app.servizi.azioni import chiudi_esito

        ctx = self._ctx
        cmd = TABELLA[lavoro.azione]
        argv = esecutore.riga_di_comando(
            ctx.config, lavoro.azione, lavoro.parametri, by=lavoro.utente.firma if cmd.by else None,
            flag_conferme=lavoro.flag_conferme,
        )
        if lavoro.interruzione_richiesta:
            # Interrotto prima di partire: il comando non si lancia nemmeno.
            lavoro.stato = "annullato"
            lavoro.esito = ErroreController("LavoroAnnullato", "Interrotto prima di cominciare.").corpo()
            lavoro.http = 409
            ctx.registro.scrivi(utente=lavoro.utente, azione=lavoro.azione, run=lavoro.run, esito="annullato",
                                lavoro=lavoro.id, dettagli={"motivo": "interrotto prima di cominciare"})
            return
        lavoro.stato = "in_corso"
        lavoro.iniziato_il = adesso_iso()
        inizio = time.monotonic()
        processo = await esecutore.avvia_processo(ctx.config, argv)
        lavoro.processo = processo
        if lavoro.interruzione_richiesta:
            # Interruzione (o spegnimento) arrivata mentre il processo partiva.
            self._sigterm(lavoro, motivo="richiesta durante l'avvio")
        await self._notifica()
        timeout = False
        comunicazione = asyncio.ensure_future(processo.communicate())
        try:
            if cmd.timeout is None:
                stdout, stderr = await comunicazione
            else:
                done, _ = await asyncio.wait({comunicazione}, timeout=cmd.timeout)
                if not done:
                    # Oltre il timeout del contratto (stesura, revisione): un SIGTERM,
                    # poi si aspetta che il comando chiuda da se'.
                    timeout = True
                    log.error("lavoro %s: oltre il timeout di %ss", lavoro.id, cmd.timeout)
                    self._sigterm(lavoro, motivo="timeout")
                stdout, stderr = await comunicazione
        except asyncio.CancelledError:
            # Il worker viene cancellato solo allo spegnimento, dopo il SIGTERM.
            comunicazione.cancel()
            raise
        risultato = esecutore.interpreta(lavoro.azione, processo.returncode, stdout, stderr,
                                         time.monotonic() - inizio, False)
        if timeout and risultato.json is None:
            risultato.timeout = True
        elif risultato.json is None and lavoro.sigterm_inviato and processo.returncode == -signal.SIGTERM:
            # SIGTERM arrivato prima che il comando installasse i suoi gestori (appena
            # partito): non ha fatto niente. E' l'esito `Interrupted` del contratto.
            risultato.exit_code = 1
            risultato.json = {"ok": False, "error_type": "Interrupted", "exit_code": 1,
                              "error": "Interrotto appena partito, prima di fare qualunque cosa: si puo' rilanciare."}
        stato, corpo = chiudi_esito(ctx, lavoro.utente, lavoro.azione, lavoro.parametri, risultato,
                                    flag_conferme=lavoro.flag_conferme, risposta_a=lavoro.risposta_a,
                                    lavoro=lavoro.id, extra_gettone={"impronta": lavoro.impronta} if lavoro.impronta else None)
        lavoro.http, lavoro.esito = stato, corpo


def _numero(valore: Any) -> float | None:
    if isinstance(valore, (int, float)) and not isinstance(valore, bool):
        return float(valore)
    return None
