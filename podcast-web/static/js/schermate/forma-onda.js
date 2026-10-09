// Passo 7 · forma d'onda dell'episodio (waveform.py): picchi 0-1 sull'intero file finale,
// alla risoluzione ammessa piu' vicina alla larghezza, ridimensionati in pagina. Un clic
// (o le frecce) sposta l'ascolto e sceglie la battuta in quel punto. Colori dal tema.

import { h, sostituisci } from "../dom.js";
import { leggi, seg } from "../api.js";

const RISOLUZIONI = [50, 100, 200, 400, 800, 1600];

function colore(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

function orario(secondi) {
  const s = Math.max(0, Math.round(Number(secondi) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function formaOnda(run, audio, trascrizione, suBattuta) {
  const canvas = h("canvas", { class: "forma-onda", height: "96", tabindex: "0", role: "slider",
    "aria-label": "Posizione nell'episodio", "aria-valuemin": "0" });
  const tempo = h("span", { class: "nota", "aria-live": "off" }, "0:00");
  const battutaEl = h("span", { class: "forte" });
  const contenitore = h("div", { class: "contenitore-onda" },
    h("div", { class: "riga-azioni" }, battutaEl,
      h("button", { type: "button", class: "pulsante pulsante-piccolo", "aria-label": "Indietro di 10 secondi", onclick: () => salta(-10) }, "−10"),
      h("button", { type: "button", class: "pulsante pulsante-piccolo", "aria-label": "Avanti di 10 secondi", onclick: () => salta(10) }, "+10"),
      tempo),
    canvas);
  let picchi = [];
  let totale = 0;

  const battutaA = (sec) => trascrizione.find((t) => typeof t.inizio_finale === "number" && sec >= t.inizio_finale && sec < t.fine_finale);

  function disegna() {
    const larghezza = canvas.clientWidth || 600;
    const scala = window.devicePixelRatio || 1;
    canvas.width = Math.round(larghezza * scala);
    canvas.height = Math.round(96 * scala);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(scala, 0, 0, scala, 0, 0);
    ctx.clearRect(0, 0, larghezza, 96);
    if (!picchi.length) return;
    const ora = audio.currentTime || 0;
    const t = battutaA(ora);
    if (t && totale) {
      ctx.fillStyle = colore("--onda-battuta");
      ctx.fillRect((t.inizio_finale / totale) * larghezza, 0, ((t.fine_finale - t.inizio_finale) / totale) * larghezza, 96);
    }
    const passo = larghezza / picchi.length;
    const fatto = totale ? (ora / totale) * larghezza : 0;
    picchi.forEach((p, i) => {
      const x = i * passo;
      const altezza = Math.max(1, p * 88);
      ctx.fillStyle = x < fatto ? colore("--onda-ascoltata") : colore("--onda");
      ctx.fillRect(x, 48 - altezza / 2, Math.max(1, passo - 1), altezza);
    });
    ctx.fillStyle = colore("--focus");
    ctx.fillRect(fatto, 0, 2, 96);
  }

  function aggiorna() {
    const ora = audio.currentTime || 0;
    sostituisci(tempo, `${orario(ora)} / ${orario(totale)}`);
    canvas.setAttribute("aria-valuenow", String(Math.round(ora)));
    canvas.setAttribute("aria-valuetext", orario(ora));
    const t = battutaA(ora);
    sostituisci(battutaEl, t ? `Battuta ${t.n}` : "");
    disegna();
  }

  function vaiA(sec, scegli = true) {
    audio.currentTime = Math.min(Math.max(0, sec), totale || sec);
    aggiorna();
    const t = battutaA(audio.currentTime);
    if (scegli && t) suBattuta(t);
  }

  function salta(delta) { vaiA((audio.currentTime || 0) + delta, false); }

  canvas.addEventListener("click", (e) => {
    const r = canvas.getBoundingClientRect();
    vaiA(((e.clientX - r.left) / r.width) * totale);
  });
  canvas.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); salta(-5); }
    else if (e.key === "ArrowRight") { e.preventDefault(); salta(5); }
    else if (e.key === "Enter") { const t = battutaA(audio.currentTime || 0); if (t) suBattuta(t); }
  });
  audio.addEventListener("timeupdate", aggiorna);
  audio.addEventListener("seeked", aggiorna);
  const osservatore = new ResizeObserver(() => disegna());
  osservatore.observe(canvas);

  (async () => {
    const voluti = Math.min(1600, (canvas.clientWidth || 800) / 2);
    const punti = RISOLUZIONI.reduce((a, b) => (Math.abs(b - voluti) < Math.abs(a - voluti) ? b : a));
    const { corpo } = await leggi(`/api/episodi/${seg(run)}/forma-onda?punti=${punti}`);
    if (!corpo.ok) { canvas.remove(); contenitore.append(h("p", { class: "nota" }, "Forma d'onda non disponibile.")); return; }
    picchi = corpo.dati.peaks || [];
    totale = corpo.dati.duration_seconds || audio.duration || 0;
    canvas.setAttribute("aria-valuemax", String(Math.round(totale)));
    aggiorna();
  })();

  return { elemento: contenitore, chiudi: () => osservatore.disconnect() };
}
