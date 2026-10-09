// Componenti condivisi dalle schermate dei passi.

import { h, icona } from "./dom.js";
import { leggi, seg } from "./api.js";

export const PASSI = ["Documento", "Cast e formato", "Musiche", "Copione", "Stima e consenso", "Generazione", "Episodio"];

// Barra dei passi (Stepper.dc.html): `raggiunto` viene da run_status.passo. I passi
// precedenti si possono riaprire, quelli successivi no finche' lo stato non ci arriva.
export function stepper(run, mostrato, raggiunto) {
  return h("nav", { class: "stepper", "aria-label": "Passi dell'episodio" },
    h("ol", {}, PASSI.map((nome, i) => {
      const n = i + 1;
      const stato = n === mostrato ? "corrente" : n < raggiunto || (n === raggiunto && n !== mostrato) ? "fatto" : "futuro";
      const contenuto = [h("span", { class: "stepper-punto", "aria-hidden": "true" }, String(n)), h("span", { class: "stepper-nome" }, nome)];
      const raggiungibile = run && n <= Math.max(raggiunto, 1) && n !== mostrato;
      return h("li", { class: `stepper-passo stepper-${stato}` },
        raggiungibile
          ? h("a", { href: `#/episodi/${seg(run)}/${n}` }, contenuto)
          : h("span", { "aria-current": n === mostrato ? "step" : null, "aria-disabled": n !== mostrato ? "true" : null }, contenuto));
    })));
}

// Intestazione comune: "N · Nome", titolo dell'episodio, stato, stepper.
export function intestazionePasso({ run, passo, titolo, stato, raggiunto, sottotitolo }) {
  return h("header", { class: "intestazione-passo" },
    h("p", { class: "occhiello" }, `${passo} · ${PASSI[passo - 1]}`),
    h("h1", { tabindex: "-1" }, titolo || "Nuovo episodio"),
    h("p", { class: "nota" }, [stato, sottotitolo || (!run ? "La bozza si crea con «Avanti»" : passo <= 5 ? "Bozza salvata automaticamente in Archivio" : null)].filter(Boolean).join(" · ")),
    stepper(run, passo, raggiunto || passo),
  );
}

// Stato della run per l'intestazione (run_status).
export async function statoRun(run) {
  const { stato, corpo } = await leggi(`/api/episodi/${seg(run)}/stato`);
  return stato === 200 && corpo.ok ? corpo.dati : null;
}

export function badgeStato(dati) {
  const classe = {
    pronto: "badge-ok", da_aggiornare: "badge-attenzione", in_corso: "badge-incorso", interrotta: "badge-errore",
    copione_da_approvare: "badge-attenzione", stima_e_consenso: "badge-attenzione", illeggibile: "badge-errore",
  }[dati.stato] || "badge-neutro";
  let testo = dati.etichetta || dati.stato;
  if (dati.stato === "in_corso" && dati.operazione === "produzione" && dati.battute_totali) {
    testo = `In generazione · ${dati.battuta ?? 0}/${dati.battute_totali}`;
  } else if (dati.stato === "interrotta" && typeof dati.battute_mancanti === "number") {
    testo = `Interrotta · ${dati.battute_mancanti} battute mancanti`;
  }
  return h("span", { class: `badge ${classe}` }, testo);
}

export function pulsanteAvanti(testo, href, attivo = true) {
  return attivo
    ? h("a", { class: "pulsante pulsante-primario", href }, h("span", {}, testo), icona("avanti"))
    : h("button", { type: "button", class: "pulsante pulsante-primario", disabled: true }, testo);
}

export function pulsanteIndietro(href, testo = "Indietro") {
  return h("a", { class: "pulsante", href }, icona("indietro"), h("span", {}, testo));
}

export function piede(...figli) {
  return h("div", { class: "piede-passo" }, figli);
}

// Contenitore per i messaggi d'errore di una sezione (nascosto finche' serve).
export function boxErrore() {
  return h("div", { class: "box-errore", hidden: true });
}

export function sezione(titolo, ...figli) {
  return h("section", { class: "scheda" }, titolo ? h("h2", {}, titolo) : null, figli);
}
