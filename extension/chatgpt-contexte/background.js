/**
 * Service worker — pont sécurisé vers `POST /context/chatgpt`.
 *
 * Le content script ne manipule AUCUN secret : il envoie {url, titre} ; c'est
 * ici que l'on charge l'endpoint et le jeton (chrome.storage.local, réglés
 * dans les options) et que l'on fait l'appel HTTP authentifié. Aucune URL de
 * conversation n'est conservée : seulement un statut diagnostique (pour la
 * page d'options), jamais l'URL elle-même.
 */
"use strict";

var CLE_STOCKAGE = {
  endpoint: "endpoint",
  jeton: "jeton",
  clientId: "clientId",
  statut: "dernierStatut", // {ok:bool, code?:number, a:number} — sans URL
};

function lireConfig() {
  return chrome.storage.local.get([
    CLE_STOCKAGE.endpoint,
    CLE_STOCKAGE.jeton,
    CLE_STOCKAGE.clientId,
  ]);
}

function clientId() {
  return crypto.randomUUID();
}

function memoriserStatut(statut) {
  var a = Date.now();
  chrome.storage.local.set({ [CLE_STOCKAGE.statut]: Object.assign({ a: a }, statut) });
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

chrome.runtime.onMessage.addListener(function (message, _expediteur, repondre) {
  (async function () {
    if (!message || (message.type !== "contexte" && message.type !== "efface")) {
      repondre({ ok: false, raison: "type" });
      return;
    }
    var config = await lireConfig();
    var endpoint = (config[CLE_STOCKAGE.endpoint] || "").trim();
    var jeton = (config[CLE_STOCKAGE.jeton] || "").trim();
    if (!endpoint || !jeton) {
      memoriserStatut({ ok: false, raison: "config" });
      repondre({ ok: false, raison: "config" });
      return;
    }
    var idClient =
      config[CLE_STOCKAGE.clientId] ||
      (function () {
        var id = clientId();
        chrome.storage.local.set({ [CLE_STOCKAGE.clientId]: id });
        return id;
      })();

    if (message.type === "contexte") {
      var p = message.payload || {};
      repondre(
        await poster(endpoint, jeton, {
          url: p.url,
          title: p.title || undefined,
          client_id: idClient,
          timestamp: new Date().toISOString(),
        })
      );
    } else {
      repondre(
        await poster(endpoint, jeton, { actif: false, client_id: idClient })
      );
    }
  })();
  return true; // réponse asynchrone
});
