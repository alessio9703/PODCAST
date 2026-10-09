// Costruzione del DOM senza markup in stringa: ogni testo passa da textContent, quindi
// niente di cio' che arriva dall'agente (titoli, battute, nomi) puo' diventare codice.
// Nessuno stile inline: solo classi (la CSP vieta gli attributi style).

export function h(tag, attributi = {}, ...figli) {
  const el = document.createElement(tag);
  for (const [nome, valore] of Object.entries(attributi || {})) {
    if (valore === undefined || valore === null || valore === false) continue;
    if (nome === "class") el.className = Array.isArray(valore) ? valore.filter(Boolean).join(" ") : valore;
    else if (nome.startsWith("on") && typeof valore === "function") el.addEventListener(nome.slice(2), valore);
    else if (nome === "dataset") Object.assign(el.dataset, valore);
    else if (nome === "style") throw new Error("niente stili inline: usa una classe");
    else if (valore === true) el.setAttribute(nome, "");
    else el.setAttribute(nome, String(valore));
  }
  aggiungi(el, figli);
  return el;
}

export function aggiungi(el, figli) {
  for (const figlio of figli.flat(Infinity)) {
    if (figlio === null || figlio === undefined || figlio === false) continue;
    el.append(figlio instanceof Node ? figlio : document.createTextNode(String(figlio)));
  }
  return el;
}

export function svuota(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function sostituisci(el, ...figli) {
  svuota(el);
  return aggiungi(el, figli);
}

let contatore = 0;
export function nuovoId(prefisso = "id") {
  contatore += 1;
  return `${prefisso}-${contatore}`;
}

// Icone semplici in SVG (create con createElementNS, niente markup in stringa).
const PERCORSI = {
  piu: "M12 5v14M5 12h14",
  cerca: "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM20 20l-4-4",
  carica: "M12 16V4M7 9l5-5 5 5M5 20h14",
  scarica: "M12 4v12M7 11l5 5 5-5M5 20h14",
  spunta: "M5 12l5 5 9-10",
  indietro: "M15 6l-6 6 6 6",
  avanti: "M9 6l6 6-6 6",
};

export function icona(nome) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icona");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", PERCORSI[nome] || "");
  svg.append(p);
  return svg;
}

// --- formattazione -------------------------------------------------------

export function giorno(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// Come `giorno`, ma "Ora" per l'ultimo minuto (colonna "Aggiornato" dell'Archivio).
export function data(iso) {
  const d = new Date(iso);
  const ora = new Date();
  if (iso && !Number.isNaN(d.getTime()) && ora - d < 60 * 1000 && ora >= d) return "Ora";
  return giorno(iso);
}

export function dataOra(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${giorno(iso)}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function durata(secondi) {
  if (secondi === null || secondi === undefined || Number.isNaN(secondi)) return "—";
  const s = Math.max(0, Math.round(secondi));
  const ore = Math.floor(s / 3600);
  const min = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = ore ? String(min).padStart(2, "0") : String(min);
  return `${ore ? ore + ":" : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

export function numero(n) {
  return n === null || n === undefined ? "—" : new Intl.NumberFormat("it-IT").format(n);
}

// Costo da una stima dell'agente: euro se c'e' il cambio (D3), altrimenti dollari.
export function costo(dati) {
  if (!dati) return "—";
  if (typeof dati.cost_eur === "number") return new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" }).format(dati.cost_eur);
  if (typeof dati.cost_usd === "number") return new Intl.NumberFormat("it-IT", { style: "currency", currency: "USD" }).format(dati.cost_usd);
  return "costo non disponibile";
}
