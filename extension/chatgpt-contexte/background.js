/**
 * Service worker — orchestration du contexte « conversation ChatGPT active ».
 *
 * V2 : c'est ici (et non plus dans chaque content script) que l'on décide de
 * la conversation à publier et que l'on maintient le heartbeat.
 *
 * Architecture :
 *  - content.js signale les navigations SPA (pushState/replaceState/popstate),
 *    que le worker ne voit pas autrement ;
 *  - cerveau.js (logique pure) choisit l'ONGlet propriétaire = la dernière
 *    conversation réellement activée (tabs.onActivated / windows.onFocusChanged) ;
 *  - une alarme « coeur » (~45 s) rafraîchit le TTL serveur tant que l'onglet
 *    propriétaire est ouvert sur /c/<id> — même en arrière-plan (fenêtre
 *    réduite) : le contexte d'une conversation active n'expire plus ;
 *  - une page ChatGPT SANS conversation ne peut jamais effacer le contexte
 *    d'une autre conversation (les messages « efface » de l'ancienne version
 *    sont ignorés : type_inconnu) ;
 *  - l'état ({proprietaire}) est conservé dans chrome.storage.session :
 *    il survit aux redémarrages du worker (pas à ceux du navigateur, et c'est
 *    voulu — aucune URL persistée sur disque).
 *
 * Le contenu n'est jamais lu ; seuls URL + titre des onglets chatgpt.com
 * (permission d'hôte) transitent ; le jeton et l'endpoint restent ici.
 */
"use strict";

importScripts("detect.js", "cerveau.js");

var Detect = globalThis.DetectChatGPT;
var Cerveau = globalThis.Cerveau;

var COEUR_NOM = "coeur-contexte";
var COEUR_PERIODE_MIN = 0.75; // 45 s → ~6 rafraîchissements par TTL de 300 s
var COOLDOWN_MS = 15000; // un seul enregistrement par (onglet, url) sur cette fenêtre

var CLE_STOCKAGE = {
  endpoint: "endpoint",
  jeton: "jeton",
  clientId: "clientId",
  compte: "compteLabel", // « Nom de ce compte » (ex. ChatGPT principal) — non secret
  statut: "dernierStatut", // {ok, code?, raison?, a} — sans URL
};

// ------------------------------------------------------------------ utilitaires

var dernierPost = { cle: null, a: 0 };

function lireConfig() {
  return chrome.storage.local.get([
    CLE_STOCKAGE.endpoint,
    CLE_STOCKAGE.jeton,
    CLE_STOCKAGE.clientId,
    CLE_STOCKAGE.compte,
  ]);
}

function lireEtat() {
  return chrome.storage.session
    .get({ etatCerveau: Cerveau.etatInitial() })
    .then(function (v) {
      return v.etatCerveau;
    });
}

function ecrireEtat(etat) {
  return chrome.storage.session.set({ etatCerveau: etat });
}

function memoriserStatut(statut) {
  var a = Date.now();
  chrome.storage.local.set({ [CLE_STOCKAGE.statut]: Object.assign({ a: a }, statut) });
}

function idClientPersistant(config) {
  if (config[CLE_STOCKAGE.clientId]) return config[CLE_STOCKAGE.clientId];
  var id = crypto.randomUUID();
  chrome.storage.local.set({ [CLE_STOCKAGE.clientId]: id });
  return id;
}

async function poster(endpoint, jeton, corps) {
  let reponse;
  try {
    reponse = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + jeton,
      },
      body: JSON.stringify(corps),
    });
  } catch (e) {
    memoriserStatut({ ok: false, raison: "reseau" });
    return { ok: false, raison: "reseau" };
  }
  if (reponse.ok) {
    memoriserStatut({ ok: true, code: reponse.status });
    return { ok: true };
  }
  var raison = reponse.status === 401 ? "jeton" : reponse.status === 429 ? "limite" : "http";
  memoriserStatut({ ok: false, code: reponse.status, raison: raison });
  return { ok: false, code: reponse.status, raison: raison };
}

async function lireOnglet(tabId) {
  try {
    var t = await chrome.tabs.get(tabId);
    if (!t || !t.url) return null; // URL illisible (hôte sans permission) ou onglet parti
    return { url: t.url, titre: t.title || null, actif: !!t.active };
  } catch (e) {
    return null;
  }
}

// ------------------------------------------------------- actions vers le serveur

async function envoyerContexte(action) {
  var config = await lireConfig();
  var endpoint = (config[CLE_STOCKAGE.endpoint] || "").trim();
  var jeton = (config[CLE_STOCKAGE.jeton] || "").trim();
  if (!endpoint || !jeton) {
    memoriserStatut({ ok: false, raison: "config" });
    return;
  }
  var r = await poster(endpoint, jeton, {
    url: action.url,
    title: action.titre || undefined,
    account_label: (config[CLE_STOCKAGE.compte] || "").trim() || undefined,
    client_id: idClientPersistant(config),
    onglet_id: String(action.ongletId),
    timestamp: new Date().toISOString(),
  });
  if (r.ok) {
    dernierPost = { cle: action.ongletId + "|" + action.url, a: Date.now() };
  }
}

async function envoyerEfface(ongletId) {
  var config = await lireConfig();
  var endpoint = (config[CLE_STOCKAGE.endpoint] || "").trim();
  var jeton = (config[CLE_STOCKAGE.jeton] || "").trim();
  if (!endpoint || !jeton) {
    memoriserStatut({ ok: false, raison: "config" });
    return;
  }
  var r = await poster(endpoint, jeton, {
    actif: false,
    client_id: idClientPersistant(config),
    onglet_id: String(ongletId),
  });
  if (r.ok) {
    dernierPost = { cle: "efface|" + ongletId, a: Date.now() };
  }
}

// ------------------------------------------------------------------ traitement

// File d'attente : l'état (chrome.storage.session) est lu puis réécrit de façon
// séquentielle, jamais en parallèle (pas de course entre événements).
var file = Promise.resolve();

function enQueue(tache) {
  file = file
    .then(tache)
    .catch(function (e) {
      console.warn("[contexte-chatgpt]", e && e.message ? e.message : e);
    });
  return file;
}

async function traiterEv(ev) {
  var etat = await lireEtat();
  var res = Cerveau.surEvenement(etat, ev);
  await ecrireEtat(res.etat);
  if (!res.action) return;
  if (res.action.type === "effacer") {
    await envoyerEfface(res.action.ongletId);
    return;
  }
  // enregistrer — sauf heartbeat redondant (même onglet/url très récemment).
  var cle = res.action.ongletId + "|" + res.action.url;
  if (ev.type === "coeur" && dernierPost.cle === cle && Date.now() - dernierPost.a < COOLDOWN_MS) {
    return;
  }
  await envoyerContexte(res.action);
}

// ---------------------------------------------------------------- événements

chrome.tabs.onActivated.addListener(function (info) {
  return enQueue(async function () {
    var onglet = await lireOnglet(info.tabId);
    await traiterEv({
      type: "activation",
      tabId: info.tabId,
      url: onglet ? onglet.url : null,
      titre: onglet ? onglet.titre : null,
    });
  });
});

chrome.windows.onFocusChanged.addListener(function (windowId) {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return undefined;
  return enQueue(async function () {
    var onglets;
    try {
      onglets = await chrome.tabs.query({ active: true, windowId: windowId });
    } catch (e) {
      return;
    }
    var actif = onglets && onglets[0];
    if (!actif || !actif.url) return;
    await traiterEv({
      type: "activation",
      tabId: actif.id,
      url: actif.url,
      titre: actif.title || null,
    });
  });
});

chrome.tabs.onUpdated.addListener(function (tabId, info, onglet) {
  if (!info.url) return undefined; // changement de titre seul : sans objet
  return enQueue(async function () {
    var url = (onglet && onglet.url) || info.url || "";
    await traiterEv({
      type: "url_onglet",
      tabId: tabId,
      url: url,
      titre: (onglet && onglet.title) || null,
      actif: !!(onglet && onglet.active),
    });
  });
});

chrome.tabs.onRemoved.addListener(function (tabId) {
  return enQueue(function () {
    return traiterEv({ type: "fermeture", tabId: tabId });
  });
});

// Messages des content scripts : navigation SPA. Les anciens types
// (« contexte » / « efface ») sont IGNORÉS — c'est le verrou anti-bug : un
// vieux content script ne peut plus effacer le contexte.
chrome.runtime.onMessage.addListener(function (message, expediteur) {
  if (!message || message.type !== "url_onglet") {
    return false;
  }
  var tab = expediteur && expediteur.tab;
  if (!tab || tab.id == null) return false;
  return enQueue(async function () {
    var actif = false;
    try {
      var act = await chrome.tabs.query({ active: true, windowId: tab.windowId });
      actif = !!(act && act[0] && act[0].id === tab.id);
    } catch (e) {
      /* non bloquant */
    }
    await traiterEv({
      type: "url_onglet",
      tabId: tab.id,
      url: message.url || "",
      titre: message.titre || null,
      actif: actif,
    });
  });
});

// -------------------------------------------------------------------- heartbeat

function armerCoeur() {
  chrome.alarms.get(COEUR_NOM, function (alarme) {
    if (!alarme) {
      chrome.alarms.create(COEUR_NOM, { periodInMinutes: COEUR_PERIODE_MIN });
    }
  });
}

chrome.alarms.onAlarm.addListener(function (alarme) {
  if (alarme.name !== COEUR_NOM) return;
  enQueue(async function () {
    var etat = await lireEtat();
    var p = etat.proprietaire;
    if (!p) return;
    // Le propriétaire doit exister et être toujours sur /c/<id> : sinon on
    // laisse cerveau réagir (fermeture / navigation) au lieu de heartbeater.
    var onglet = await lireOnglet(p.tabId);
    if (!onglet) {
      await traiterEv({ type: "fermeture", tabId: p.tabId });
      return;
    }
    if (!Detect.estSurChatGPT(onglet.url)) {
      await traiterEv({
        type: "url_onglet",
        tabId: p.tabId,
        url: onglet.url,
        titre: onglet.titre,
        actif: onglet.actif,
      });
      return;
    }
    if (!Detect.extraireConversation(onglet.url)) {
      await traiterEv({
        type: "url_onglet",
        tabId: p.tabId,
        url: onglet.url,
        titre: onglet.titre,
        actif: true,
      });
      return;
    }
    if (onglet.url !== p.url) {
      // L'onglet propriétaire a changé de conversation (ou a rechargé une
      // autre) : on met à jour immédiatement.
      await traiterEv({
        type: "url_onglet",
        tabId: p.tabId,
        url: onglet.url,
        titre: onglet.titre,
        actif: onglet.actif,
      });
      return;
    }
    await traiterEv({ type: "coeur" });
  });
});

// ------------------------------------------------------------------- démarrage

chrome.runtime.onInstalled.addListener(function () {
  armerCoeur();
});
chrome.runtime.onStartup.addListener(function () {
  armerCoeur();
});
armerCoeur();

// Reprise rapide : si un propriétaire existait (worker redémarré), on vérifie
// tout de suite qu'il est toujours là et on heartbeate.
enQueue(async function () {
  var etat = await lireEtat();
  if (!etat.proprietaire) return;
  var onglet = await lireOnglet(etat.proprietaire.tabId);
  if (!onglet) {
    await traiterEv({ type: "fermeture", tabId: etat.proprietaire.tabId });
    return;
  }
  if (!onglet.url || !Detect.estSurChatGPT(onglet.url) || !Detect.extraireConversation(onglet.url)) {
    await traiterEv({
      type: "url_onglet",
      tabId: etat.proprietaire.tabId,
      url: onglet.url || "",
      titre: onglet.titre,
      actif: onglet.actif,
    });
    return;
  }
  await traiterEv({ type: "coeur" });
});
