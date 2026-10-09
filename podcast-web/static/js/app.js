// Avvio dell'interfaccia: utente e permessi, menu, instradamento con l'hash.

import { h, sostituisci } from "./dom.js";
import { leggi } from "./api.js";
import { sessione } from "./sessione.js";
import { preparaAvvisi, erroreCorreggibile } from "./stati.js";
import { mostraArchivio } from "./schermate/archivio.js";
import { mostraDocumento } from "./schermate/documento.js";
import { mostraCast } from "./schermate/cast.js";
import { mostraMusiche } from "./schermate/musiche.js";
import { mostraCopione } from "./schermate/copione.js";
import { mostraStima } from "./schermate/stima.js";
import { mostraGenerazione } from "./schermate/generazione.js";
import { mostraEpisodio } from "./schermate/episodio.js";


const PASSI = { 1: mostraDocumento, 2: mostraCast, 3: mostraMusiche, 4: mostraCopione, 5: mostraStima, 6: mostraGenerazione, 7: mostraEpisodio };

const contenuto = document.getElementById("contenuto");
let chiudiSchermata = null;

function voceMenuAttiva(nome) {
  for (const a of document.querySelectorAll("[data-menu]")) {
    if (a.dataset.menu === nome) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

function nonDisponibile(titolo) {
  return h("div", { class: "pagina" },
    h("h1", { tabindex: "-1" }, titolo),
    h("p", { class: "nota" }, "Questa sezione arriva in una fase successiva. Il flusso principale è disponibile da «Nuovo episodio» e dall'Archivio."));
}

async function instrada() {
  if (chiudiSchermata) { try { chiudiSchermata(); } catch { /* niente */ } chiudiSchermata = null; }
  const percorso = decodeURIComponent(location.hash.replace(/^#/, "")) || "/archivio";
  const parti = percorso.split("/").filter(Boolean);
  const pagina = h("div", { class: "pagina" }, h("p", { class: "nota", role: "status" }, "Caricamento…"));
  sostituisci(contenuto, pagina);
  let menu = parti[0];
  voceMenuAttiva(parti[0] === "episodi" || !parti[0] ? "archivio" : parti[0]);
  try {
    if (parti[0] === "archivio" || parti.length === 0) {
      menu = "archivio";
      chiudiSchermata = await mostraArchivio(pagina);
    } else if (parti[0] === "nuovo") {
      menu = "nuovo";
      chiudiSchermata = await mostraDocumento(pagina, { run: null });
    } else if (parti[0] === "episodi" && parti[1] && PASSI[parti[2]]) {
      menu = "archivio";
      chiudiSchermata = await PASSI[parti[2]](pagina, { run: parti[1] });
    } else if (["documenti", "voci", "musiche", "lezioni", "impostazioni", "diagnostica"].includes(parti[0])) {
      sostituisci(contenuto, nonDisponibile({ documenti: "Documenti", voci: "Voci", musiche: "Musiche", lezioni: "Lezioni", impostazioni: "Impostazioni", diagnostica: "Diagnostica" }[parti[0]]));
    } else {
      sostituisci(contenuto, nonDisponibile("Pagina non trovata"));
    }
  } catch (errore) {
    console.error(errore);
    const box = h("div", {});
    sostituisci(contenuto, h("div", { class: "pagina" }, box));
    erroreCorreggibile(box, { error_type: "ErroreInterno", messaggio: "" });
  }
  voceMenuAttiva(menu);
  // Il focus va al titolo della nuova schermata (lettori di schermo, tastiera).
  const titolo = contenuto.querySelector("h1");
  if (titolo) titolo.focus({ preventScroll: false });
}

async function avvia() {
  preparaAvvisi(document.getElementById("avvisi"));
  const { stato, corpo } = await leggi("/api/io");
  if (stato === 200 && corpo.ok) {
    sessione.utente = corpo.dati.utente;
    sessione.nome = corpo.dati.nome;
    sessione.permessi = new Set(corpo.dati.permessi);
    document.getElementById("utente").textContent = corpo.dati.nome;
  } else {
    const box = h("div", {});
    sostituisci(contenuto, h("div", { class: "pagina" }, h("h1", { tabindex: "-1" }, "LIL Podcast"), box));
    erroreCorreggibile(box, corpo);
    return;
  }
  window.addEventListener("hashchange", instrada);
  instrada();
}

avvia();
