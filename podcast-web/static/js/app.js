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
import { mostraConfronto } from "./schermate/confronto.js";
import { mostraDocumenti } from "./schermate/documenti.js";
import { mostraVoci } from "./schermate/voci.js";
import { mostraBrani } from "./schermate/brani.js";
import { mostraLezioni } from "./schermate/lezioni.js";
import { mostraImpostazioni, mostraDiagnostica } from "./schermate/sistema.js";


const PASSI = { 1: mostraDocumento, 2: mostraCast, 3: mostraMusiche, 4: mostraCopione, 5: mostraStima, 6: mostraGenerazione, 7: mostraEpisodio };

const PAGINE = { documenti: mostraDocumenti, voci: mostraVoci, musiche: mostraBrani, lezioni: mostraLezioni, impostazioni: mostraImpostazioni, diagnostica: mostraDiagnostica };

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
    h("p", { class: "nota" }, "Questa pagina non esiste. Torna all'Archivio o crea un nuovo episodio."));
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
      chiudiSchermata = await mostraDocumento(pagina, { run: null, documento: parti[1] || null });
    } else if (PAGINE[parti[0]]) {
      chiudiSchermata = await PAGINE[parti[0]](pagina);
    } else if (parti[0] === "episodi" && parti[1] && parti[2] === "confronto") {
      menu = "archivio";
      chiudiSchermata = await mostraConfronto(pagina, { run: parti[1], da: parti[3] });
    } else if (parti[0] === "episodi" && parti[1] && PASSI[parti[2]]) {
      menu = "archivio";
      chiudiSchermata = await PASSI[parti[2]](pagina, { run: parti[1] });
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
    if (corpo.dati.formati) sessione.formati = corpo.dati.formati;
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
