/**
 * Cerveau — logique PURE du contexte « conversation active » (aucun chrome.*).
 *
 * Chargé par le service worker (importScripts) et par les tests node (vm),
 * après detect.js. Décide, à partir d'événements d'onglets, QUEL onglet est le
 * propriétaire du contexte à publier, et si une action (enregistrer / effacer)
 * doit partir.
 *
 * Invariants (corrigent la cause racine du bug « contexte effacé en boucle ») :
 *  - le contexte appartient à la DERNIÈRE conversation réellement activée ;
 *  - une page ChatGPT SANS conversation (accueil, /share/, …) ne peut JAMAIS
 *    effacer le contexte d'une conversation ouverte dans un autre onglet ;
 *  - le heartbeat (type "coeur") ne rafraîchit QUE le propriétaire courant,
 *    même si son onglet est en arrière-plan (fenêtre réduite) : tant que
 *    l'onglet reste sur /c/<id>, le contexte ne doit pas expirer ;
 *  - la fermeture ou la navigation hors /c/<id> de l'onglet propriétaire
 *    efface le contexte (plus de conversation active → pas de mauvais lien).
 *
 * Événements d'entrée (év) :
 *  {type:"activation", tabId, url, titre}    onglet activé / fenêtre focalisée
 *  {type:"url_onglet", tabId, url, titre, actif}
 *      navigation (SPA via content script, ou chargement complet onUpdated)
 *  {type:"fermeture", tabId}
 *  {type:"coeur", maintenant}                tick heartbeat (alarme)
 *
 * État : { proprietaire: null | {tabId, url, convId, titre} }
 * Sortie : { etat, action } où action ∈ {type:"enregistrer", ongletId, url,
 * convId, titre} | {type:"effacer", ongletId} | null.
 */
(function (racine) {
  "use strict";

  var Detect = null;

  function conversationDe(url) {
    if (!url || !Detect) return null;
    return Detect.extraireConversation(url);
  }

  function titrePropre(titre) {
    return Detect && Detect.titrePage ? Detect.titrePage(titre) : null;
  }

  function etatInitial() {
    return { proprietaire: null };
  }

  function _adopter(apres, tabId, conv, titre) {
    apres.proprietaire = {
      tabId: tabId,
      url: conv.url,
      convId: conv.conversation_id,
      titre: titre || null,
    };
  }

  function _enregistrer(ongletId, conv, titre) {
    return {
      type: "enregistrer",
      ongletId: ongletId,
      url: conv.url,
      convId: conv.conversation_id,
      titre: titre || null,
    };
  }

  /**
   * @returns {etat, action}
   */
  function surEvenement(etat, ev) {
    var apres = {
      proprietaire: etat.proprietaire ? Object.assign({}, etat.proprietaire) : null,
    };
    var conv = conversationDe(ev.url);

    switch (ev.type) {
      // ---------------------------------------------------------------- activation
      case "activation": {
        // L'utilisateur a ramené cet onglet au premier plan. Une conversation
        // devient le nouveau propriétaire (contexte = dernière conversation
        // réellement active). Une page sans conversation ne change RIEN : le
        // propriétaire actuel (conversation en arrière-plan) continue de vivre.
        if (!conv) return { etat: apres, action: null };
        _adopter(apres, ev.tabId, conv, titrePropre(ev.titre));
        return { etat: apres, action: _enregistrer(ev.tabId, conv, titrePropre(ev.titre)) };
      }

      // ---------------------------------------------------------------- navigation
      case "url_onglet": {
        if (conv) {
          var proprietaire = apres.proprietaire;
          if (proprietaire && proprietaire.tabId === ev.tabId) {
            // L'onglet propriétaire a changé de conversation (SPA A→B) : on
            // suit, et on rafraîchit immédiatement.
            proprietaire.url = conv.url;
            proprietaire.convId = conv.conversation_id;
            proprietaire.titre = titrePropre(ev.titre) || proprietaire.titre;
            return {
              etat: apres,
              action: _enregistrer(ev.tabId, conv, proprietaire.titre),
            };
          }
          if (ev.actif) {
            // Onglet (non propriétaire) chargé/ouvert en conversation ET actif :
            // c'est l'utilisateur qui vient de l'activer (ou chargement initial
            // d'un onglet au premier plan) → nouveau propriétaire.
            _adopter(apres, ev.tabId, conv, titrePropre(ev.titre));
            return { etat: apres, action: _enregistrer(ev.tabId, conv, titrePropre(ev.titre)) };
          }
          // Onglet en arrière-plan : il ne vole PAS la propriété.
          return { etat: apres, action: null };
        }
        // URL sans conversation (accueil, /share/, page externe…) : seul le
        // départ de l'onglet PROPRIÉTAIRE efface ; les autres onglets
        // n'ont aucun effet (anti-effacement croisé).
        var p = apres.proprietaire;
        if (p && p.tabId === ev.tabId) {
          apres.proprietaire = null;
          return { etat: apres, action: { type: "effacer", ongletId: ev.tabId } };
        }
        return { etat: apres, action: null };
      }

      // ---------------------------------------------------------------- fermeture
      case "fermeture": {
        var pf = apres.proprietaire;
        if (pf && pf.tabId === ev.tabId) {
          apres.proprietaire = null;
          return { etat: apres, action: { type: "effacer", ongletId: ev.tabId } };
        }
        return { etat: apres, action: null };
      }

      // ---------------------------------------------------------------- heartbeat
      case "coeur": {
        var pc = apres.proprietaire;
        if (!pc) return { etat: apres, action: null };
        // Rafraîchit le TTL serveur tant que l'onglet propriétaire est ouvert
        // sur sa conversation (l'appelant a déjà vérifié existence + URL).
        return {
          etat: apres,
          action: _enregistrer(pc.tabId, { url: pc.url, conversation_id: pc.convId }, pc.titre),
        };
      }

      default:
        return { etat: apres, action: null };
    }
  }

  function _lierDetect() {
    // detect.js doit être chargé avant cerveau.js (importScripts/vm).
    Detect = racine.DetectChatGPT || null;
  }
  _lierDetect();

  racine.Cerveau = {
    etatInitial: etatInitial,
    surEvenement: surEvenement,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = racine.Cerveau;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
