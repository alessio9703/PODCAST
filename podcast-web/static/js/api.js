// Chiamate al controller. Ogni risposta ha il formato unico:
//   {ok: true, dati} | {ok: false, esito, error_type, messaggio, details, riprovabile, gettone}
// `chiama` non solleva mai per un esito dell'agente: restituisce {stato, corpo}.

const HEADER_RICHIESTA = { "X-LIL-Richiesta": "1" };

function corpoDiRipiego(stato) {
  return {
    ok: false, esito: "controller", error_type: stato === 401 ? "NonAutenticato" : "ErroreRete",
    messaggio: stato === 401 ? "Sessione scaduta: ricarica la pagina." : "Il servizio non risponde. Controlla la connessione e riprova.",
    riprovabile: stato !== 401,
  };
}

export async function chiama(metodo, url, corpo) {
  const opzioni = { method: metodo, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (metodo !== "GET") Object.assign(opzioni.headers, HEADER_RICHIESTA);
  if (corpo !== undefined) {
    opzioni.headers["Content-Type"] = "application/json";
    opzioni.body = JSON.stringify(corpo);
  }
  let risposta;
  try {
    risposta = await fetch(url, opzioni);
  } catch {
    return { stato: 0, corpo: corpoDiRipiego(0) };
  }
  let dati;
  try {
    dati = await risposta.json();
  } catch {
    dati = corpoDiRipiego(risposta.status);
  }
  return { stato: risposta.status, corpo: dati };
}

export const leggi = (url) => chiama("GET", url);
export const invia = (url, corpo) => chiama("POST", url, corpo ?? {});
export const modifica = (url, corpo) => chiama("PATCH", url, corpo);
export const sostituisci = (url, corpo) => chiama("PUT", url, corpo);
export const togli = (url) => chiama("DELETE", url);

// Caricamento di un file: corpo grezzo, nome in X-LIL-Nome-File (codificato come X-LIL-Nome).
export async function caricaFile(url, file, gettone) {
  const headers = {
    ...HEADER_RICHIESTA,
    "Content-Type": "application/octet-stream",
    "X-LIL-Nome-File": encodeURIComponent(file.name),
  };
  if (gettone) headers["X-LIL-Gettone"] = gettone;
  try {
    const r = await fetch(url, { method: "POST", headers, body: file, credentials: "same-origin" });
    let dati;
    try { dati = await r.json(); } catch { dati = corpoDiRipiego(r.status); }
    return { stato: r.status, corpo: dati };
  } catch {
    return { stato: 0, corpo: corpoDiRipiego(0) };
  }
}

export function seg(testo) {
  return encodeURIComponent(testo);
}

// Avanzamento di una run: SSE, con il polling come ripiego (429, proxy che trattiene
// i dati, errori). `gestori`: {agente(evento, id), lavoro(vista), azzerato(), fine(dati)}.
// Restituisce una funzione per chiudere.
export function segui(run, gestori) {
  let chiuso = false;
  let ultimoId = 0;
  let timer = null;
  let sorgente = null;

  const fine = (dati) => {
    if (chiuso) return;
    chiudi();
    gestori.fine?.(dati);
  };

  async function polling() {
    if (chiuso) return;
    const { stato, corpo } = await leggi(`/api/episodi/${seg(run)}/eventi/elenco?dopo=${ultimoId}`);
    if (chiuso) return;
    if (stato === 200 && corpo.ok) {
      const d = corpo.dati;
      if (d.ripartito) { ultimoId = 0; gestori.azzerato?.(); }
      for (const e of d.eventi) { ultimoId = e.id; gestori.agente?.(e, e.id); }
      gestori.lavoro?.(d.lavoro);
      const attivo = d.lavoro && (d.lavoro.stato === "in_coda" || d.lavoro.stato === "in_corso");
      if (!attivo && (d.finito || !d.lavoro || ["finito", "annullato"].includes(d.lavoro.stato))) {
        fine({ lavoro: d.lavoro });
        return;
      }
    }
    timer = setTimeout(polling, 1500);
  }

  function passaAlPolling() {
    if (sorgente) { sorgente.close(); sorgente = null; }
    if (!chiuso && !timer) polling();
  }

  function chiudi() {
    chiuso = true;
    if (sorgente) sorgente.close();
    if (timer) clearTimeout(timer);
  }

  if ("EventSource" in window) {
    sorgente = new EventSource(`/api/episodi/${seg(run)}/eventi`);
    let ricevuto = false;
    // Se in 8 secondi non arriva niente (proxy che trattiene i dati), si passa al polling.
    const controllo = setTimeout(() => { if (!ricevuto) passaAlPolling(); }, 8000);
    const segna = () => { ricevuto = true; };
    sorgente.addEventListener("agente", (m) => {
      segna();
      ultimoId = Number(m.lastEventId) || ultimoId;
      gestori.agente?.(JSON.parse(m.data), ultimoId);
    });
    sorgente.addEventListener("lavoro", (m) => { segna(); gestori.lavoro?.(JSON.parse(m.data)); });
    sorgente.addEventListener("azzerato", () => { segna(); ultimoId = 0; gestori.azzerato?.(); });
    sorgente.addEventListener("fine", (m) => { segna(); clearTimeout(controllo); fine(JSON.parse(m.data)); });
    sorgente.onerror = () => {
      // Una risposta non 200 (es. 429 TroppeConnessioni) chiude la sorgente: ripiego.
      if (sorgente && sorgente.readyState === EventSource.CLOSED) { clearTimeout(controllo); passaAlPolling(); }
    };
  } else {
    polling();
  }
  return chiudi;
}

// Attende la fine di un lavoro (stesura, revisione, ritenta clonazione) leggendo lo stato.
export function aspettaLavoro(id, suCambio, intervallo = 1500) {
  let chiuso = false;
  let timer = null;
  async function giro() {
    if (chiuso) return;
    const { stato, corpo } = await leggi(`/api/lavori/${seg(id)}`);
    if (chiuso) return;
    if (stato === 200 && corpo.ok) {
      suCambio(corpo.dati);
      if (["finito", "annullato"].includes(corpo.dati.stato)) return;
    } else if (stato === 404) {
      suCambio(null); // servizio riavviato: lo stato vero lo dice l'agente
      return;
    }
    timer = setTimeout(giro, intervallo);
  }
  giro();
  return () => { chiuso = true; if (timer) clearTimeout(timer); };
}
