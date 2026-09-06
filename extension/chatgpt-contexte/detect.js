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
  var MOTIF_ID = /^[A-Za-z0-9_-]{1,100}$/;
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

  /**
   * Prochaine action à envoyer, ou null (rien à faire).
   *
   * @param etat  {dernierType:"contexte"|"efface"|null, dernierUrl:string|null,
   *              dernierEnvoiA:number} — état précédent (muté par l'appelant
   *              seulement quand l'envoi a RÉUSSI).
   * @param entrees {href, titre, visible:boolean, maintenant:number(ms),
   *              rafraichirApresMs?:number}
   * @returns null | {type:"contexte", payload:{url, conversation_id, title}}
   *                | {type:"efface"}
   *
   * Règles :
   *  - onglet non visible → aucune action (pas de faux contexte) ;
   *  - visible sur /c/<id> → contexte, immédiatement si l'URL change, puis
   *    heartbeat périodique (rafraichirApresMs, défaut 120 s) ;
   *  - visible sur une AUTRE page ChatGPT → « efface » (l'onglet a quitté une
   *    conversation : ne pas associer une création de tâche à l'ancienne URL) ;
   *  - visible sur autre chose (hors chatgpt.com, impossible ici) → rien.
   */
  function prochaineAction(etat, entrees) {
    if (!entrees.visible) return null;
    var maintenant = entrees.maintenant;
    var conv = extraireConversation(entrees.href);
    if (conv) {
      if (
        etat.dernierType === "contexte" &&
        etat.dernierUrl === conv.url &&
        maintenant - etat.dernierEnvoiA < (entrees.rafraichirApresMs || 120000)
      ) {
        return null; // déjà à jour, heartbeat pas encore dû
      }
      return {
        type: "contexte",
        payload: {
          url: conv.url,
          conversation_id: conv.conversation_id,
          title: titrePage(entrees.titre),
        },
      };
    }
    if (estSurChatGPT(entrees.href)) {
      if (
        etat.dernierType === "efface" &&
        maintenant - etat.dernierEnvoiA < (entrees.rafraichirApresMs || 120000)
      ) {
        return null;
      }
      return { type: "efface" };
    }
    return null;
  }

  var api = {
    extraireConversation: extraireConversation,
    estSurChatGPT: estSurChatGPT,
    titrePage: titrePage,
    prochaineAction: prochaineAction,
    HOTE: HOTE,
  };
  racine.DetectChatGPT = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
