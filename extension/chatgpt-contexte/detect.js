/**
 * Logique pure de détection de la conversation ChatGPT active.
 *
 * Partagée entre le content script (chrome-extension) et les tests node
 * (extension/tests/detect.test.js) : AUCUN accès au DOM ni à l'API Chrome ici,
 * seulement des fonctions pures — décision = f(état, entrées horodatées).
 *
 * L'extension ne lit que l'URL de l'onglet et le titre du document : jamais le
 * contenu des conversations.
 */
(function (racine) {
  "use strict";

  var HOTE = "chatgpt.com";
  var PREFIXE_CONVERSATION = "/c/";
  // Longueur 8..100 (UUID réels = 36) : pas de regex UUID stricte (risque de
  // faux négatifs), mais un segment trop court après /c/ est refusé.
  var MOTIF_ID = /^[A-Za-z0-9_-]{8,100}$/;
  var TAILLE_MAX_TITRE = 200;

  /** URL https://chatgpt.com/c/<id> → {url, conversation_id}, sinon null. */
  function extraireConversation(valeur) {
    if (typeof valeur !== "string") return null;
    var u;
    try {
      u = new URL(valeur);
    } catch (e) {
      return null;
    }
    if (u.protocol !== "https:") return null;
    if (u.hostname !== HOTE) return null;
    if (!u.pathname.startsWith(PREFIXE_CONVERSATION)) return null;
    var id = u.pathname.slice(PREFIXE_CONVERSATION.length);
    if (id.slice(-1) === "/") id = id.slice(0, -1);
    if (!id || id.indexOf("/") !== -1 || !MOTIF_ID.test(id)) return null;
    var canonique = "https://" + HOTE + PREFIXE_CONVERSATION + id;
    return { url: canonique, conversation_id: id };
  }

  /** L'URL est-elle sur chatgpt.com (n'importe quelle page) ? */
  function estSurChatGPT(valeur) {
    if (typeof valeur !== "string") return false;
    var u;
    try {
      u = new URL(valeur);
    } catch (e) {
      return false;
    }
    return u.protocol === "https:" && u.hostname === HOTE;
  }

  /** Titre épuré pour les notes : suffixe « - ChatGPT » retiré, borné. */
  function titrePage(titreBrut) {
    if (typeof titreBrut !== "string") return null;
    var titre = titreBrut.replace(/\s*-\s*ChatGPT\s*$/i, "").replace(/\s+/g, " ").trim();
    if (!titre) return null;
    if (/^chatgpt$/i.test(titre)) return null; // titre générique, pas un nom de conversation
    return titre.slice(0, TAILLE_MAX_TITRE);
  }

  // NB : la décision « quel onglet publie / efface » vit désormais dans
  // cerveau.js (état propriétaire + heartbeat), PAS ici : ce fichier ne
  // contient que le parsing/assainissement pur, partagé entre le worker et
  // la page d'options.

  var api = {
    extraireConversation: extraireConversation,
    estSurChatGPT: estSurChatGPT,
    titrePage: titrePage,
    HOTE: HOTE,
  };
  racine.DetectChatGPT = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
