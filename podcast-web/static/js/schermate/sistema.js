// Impostazioni (settings_show.py) e Diagnostica (doctor.py --web): sola lettura. Le
// impostazioni si cambiano in config.yaml dell'agente, non dall'interfaccia.

import { h, sostituisci } from "../dom.js";
import { leggi } from "../api.js";
import { boxErrore, sezione } from "../componenti.js";
import { mostraErrore } from "../stati.js";
import { puo } from "../sessione.js";

const TITOLI = { provider: "Voci e sintesi", copione: "Copione", audio: "Audio", musica: "Musica", documenti: "Documenti",
  retake: "Retake", valuta: "Valuta", lezioni: "Lezioni", cartelle: "Cartelle" };

function valore(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map(valore).join(", ");
  if (typeof v === "object") {
    return h("dl", { class: "riepilogo" }, Object.entries(v).flatMap(([k, x]) => [h("dt", {}, k.replaceAll("_", " ")), h("dd", {}, valore(x))]));
  }
  if (typeof v === "boolean") return v ? "sì" : "no";
  return String(v);
}

function intestazione(titolo, testo) {
  return h("header", { class: "intestazione-pagina" },
    h("div", {}, h("h1", { tabindex: "-1" }, titolo), h("p", { class: "nota" }, testo)));
}

export async function mostraImpostazioni(pagina) {
  const errore = boxErrore();
  sostituisci(pagina, intestazione("Impostazioni", "Sola lettura: si cambiano nel file di configurazione dell'agente."), errore);
  if (!puo("diagnostica")) { pagina.append(h("p", { class: "nota" }, "Le impostazioni le vede chi amministra il progetto.")); return; }
  const { corpo } = await leggi("/api/impostazioni");
  if (!corpo.ok) { mostraErrore(errore, corpo); return; }
  const imp = corpo.dati.impostazioni || {};
  pagina.append(...Object.entries(imp).map(([k, v]) => sezione(TITOLI[k] || k,
    v && typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length ? h("p", { class: "nota" }, "Valori predefiniti.") : valore(v))));
}

export async function mostraDiagnostica(pagina) {
  const errore = boxErrore();
  const elenco = h("div", { "aria-live": "polite" });
  async function carica() {
    sostituisci(elenco, h("p", { class: "nota", role: "status" }, "Controllo in corso…"));
    const { corpo } = await leggi("/api/diagnostica");
    if (!corpo.ok) { sostituisci(elenco); mostraErrore(errore, corpo, { riprova: carica }); return; }
    const controlli = corpo.dati.checks || [];
    sostituisci(elenco,
      h("div", { class: ["messaggio", corpo.dati.ok ? "messaggio-ok" : "messaggio-errore"], role: "status" },
        h("p", { class: "messaggio-titolo" }, corpo.dati.ok ? "Tutti i controlli obbligatori sono a posto" : "Un controllo obbligatorio non passa")),
      h("div", { class: "tabella-contenitore" }, h("table", { class: "tabella" },
        h("caption", { class: "solo-lettori" }, "Controlli"),
        h("thead", {}, h("tr", {}, ["Controllo", "Esito", "Dettaglio"].map((t) => h("th", { scope: "col" }, t)))),
        h("tbody", {}, controlli.map((c) => h("tr", {},
          h("td", {}, c.name, c.required ? "" : h("span", { class: "nota" }, " (facoltativo)")),
          h("td", {}, h("span", { class: `badge ${c.ok ? "badge-ok" : c.required ? "badge-errore" : "badge-attenzione"}` }, c.ok ? "ok" : "non passa")),
          h("td", {}, c.detail ?? "")))))));
  }
  sostituisci(pagina, intestazione("Diagnostica", "I controlli che l'agente fa su sé stesso: versioni, dipendenze, ffmpeg, cartelle."), errore,
    puo("diagnostica") ? h("div", { class: "riga-azioni" }, h("button", { type: "button", class: "pulsante", onclick: carica }, "Ripeti i controlli")) : null,
    elenco);
  if (!puo("diagnostica")) { sostituisci(elenco, h("p", { class: "nota" }, "La diagnostica la vede chi amministra il progetto.")); return; }
  await carica();
}
