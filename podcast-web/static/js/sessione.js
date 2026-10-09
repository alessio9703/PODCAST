// Utente e permessi (da GET /api/io). Il controllo vero e' sempre sul server:
// qui servono solo per nascondere o disattivare i pulsanti.
export const sessione = { utente: null, nome: "", permessi: new Set() };
export const puo = (permesso) => sessione.permessi.has(permesso);
