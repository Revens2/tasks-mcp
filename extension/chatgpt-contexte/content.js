/**
 * Content script (https://chatgpt.com/*) — observateur de navigation SPA.
 *
 * V2 : ce script ne décide PLUS rien et n'effectue AUCUN appel réseau. Il ne
 * fait que signaler au service worker les changements d'URL/titre que
 * ChatGPT produit SANS recharger la page (pushState / replaceState /
 * popstate), car le worker ne voit pas ces transitions.
 *
 * C'est le worker (background.js + cerveau.js) qui :
 *  - choisit l'onglet propriétaire (dernière conversation réellement active) ;
 *  - envoie les enregistrements + heartbeat périodique (alarme ~45 s) ;
 *  - décide de l'effacement — jamais une page sans conversation ne peut
 *    effacer le contexte d'une autre conversation (bug corrigé).
 *
 * On ne lit que location.href et document.title — jamais le contenu. Les
 * envois passent par chrome.runtime.sendMessage ; le content script ne voit
 * aucun secret (jeton/endpoint restent dans le worker).
 */
(function () {
  "use strict";

  var dernierUrl = null;

  function signaler() {
    var url = location.href;
    if (url === dernierUrl) return; // aucun changement réel : rien à dire
    dernierUrl = url;
    chrome.runtime.sendMessage({
      type: "url_onglet",
      url: url,
      titre: document.title || null,
    }).catch(function () {
      /* worker indisponible (ex. extension rechargée) : l'événement suivant
         ou l'alarme reprendra la main ; rien à corriger ici. */
    });
  }

  // Navigation SPA (history) : signalement immédiat après la transition.
  ["pushState", "replaceState"].forEach(function (nom) {
    var origine = history[nom];
    history[nom] = function () {
      var resultat = origine.apply(this, arguments);
      setTimeout(signaler, 0);
      return resultat;
    };
  });
  window.addEventListener("popstate", signaler);

  // Signalement initial (titre réel disponible après le premier rendu).
  function initial() {
    var pret = document.readyState === "complete" || document.readyState === "interactive";
    if (pret) {
      signaler();
    } else {
      document.addEventListener("DOMContentLoaded", signaler, { once: true });
    }
  }
  // document_start : on retente un peu plus tard si le titre n'était pas prêt,
  // puis régulièrement — un simple garde par URL évite tout spam.
  setTimeout(initial, 0);
  setInterval(function () {
    if (location.href !== dernierUrl) signaler();
  }, 4000);
})();
