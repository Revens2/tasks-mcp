/**
 * Content script (https://chatgpt.com/*) — observateur de conversation.
 *
 * ChatGPT est une SPA : le changement de conversation modifie l'URL sans
 * recharger la page. On surveille donc :
 *  - un tick périodique léger (URL + titre + visibilité) ;
 *  - les transitions pushState/replaceState/popstate (navigation SPA) ;
 *  - le passage de l'onglet à l'état visible (changement d'onglet actif).
 *
 * On n'envoie quelque chose que lorsque l'onglet est RÉELLEMENT visible, et
 * on ne lit que location.href et document.title — jamais le contenu.
 *
 * Les envois passent par le service worker (background.js), qui possède le
 * jeton et l'endpoint : le content script n'a accès à aucun secret.
 */
(function () {
  "use strict";

  var Detect = globalThis.DetectChatGPT;
  if (!Detect) {
    console.error("[contexte-chatgpt] detect.js absent");
    return;
  }

  // Fenêtre de silence après un échec réseau (évite de marteler l'endpoint).
  var APRES_ECHEC_MS = 15000;
  var TICK_MS = 2000;

  var etat = { dernierType: null, dernierUrl: null, dernierEnvoiA: 0 };
  var derniereTentativeA = 0;
  var enVol = false;

  function envoyer(action) {
    if (enVol) return;
    enVol = true;
    var message =
      action.type === "contexte"
        ? { type: "contexte", payload: action.payload }
        : { type: "efface" };
    chrome.runtime
      .sendMessage(message)
      .then(function (reponse) {
        if (reponse && reponse.ok) {
          // L'envoi a réussi : on peut mémoriser l'état (pas avant, sinon on
          // ne retenterait jamais après un échec réseau transitoire).
          if (action.type === "contexte") {
            etat.dernierType = "contexte";
            etat.dernierUrl = action.payload.url;
          } else {
            etat.dernierType = "efface";
            etat.dernierUrl = null;
          }
          etat.dernierEnvoiA = Date.now();
        } else {
          derniereTentativeA = Date.now();
        }
      })
      .catch(function () {
        derniereTentativeA = Date.now();
      })
      .finally(function () {
        enVol = false;
      });
  }

  function verifier() {
    var maintenant = Date.now();
    if (maintenant - derniereTentativeA < APRES_ECHEC_MS) {
      return; // échec récent : on laisse passer un peu de temps
    }
    var action = Detect.prochaineAction(etat, {
      href: location.href,
      titre: document.title,
      visible: document.visibilityState === "visible",
      maintenant: maintenant,
    });
    if (action) {
      derniereTentativeA = maintenant;
      envoyer(action);
    }
  }

  // Navigation SPA (history) : vérification immédiate.
  ["pushState", "replaceState"].forEach(function (nom) {
    var origine = history[nom];
    history[nom] = function () {
      var resultat = origine.apply(this, arguments);
      setTimeout(verifier, 0);
      return resultat;
    };
  });
  window.addEventListener("popstate", verifier);

  // Changement d'onglet actif / fenêtre.
  document.addEventListener("visibilitychange", verifier);

  // Détection initiale + filet de sécurité SPA (léger).
  setTimeout(verifier, 0);
  setInterval(verifier, TICK_MS);
})();
