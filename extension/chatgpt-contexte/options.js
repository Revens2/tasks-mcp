/**
 * Page d'options : endpoint + jeton « browser context writer » et deux tests
 * BIEN DISTINCTS :
 *
 *  1. « Tester le serveur » — transport + authentification uniquement
 *     (probe `{"actif": false}` : un 200 prouve que l'endpoint répond et que
 *     le jeton est accepté, RIEN de plus).
 *  2. « Tester la conversation courante » — pipeline complet : lecture de
 *     l'onglet actif, détection d'une URL `/c/<id>`, envoi de la VRAIE URL,
 *     puis confirmation du stockage côté serveur (GET de diagnostic).
 *
 * Un « serveur accessible » n'implique jamais qu'une conversation a été
 * détectée : les états 🟢/🟠/🔴 le distinguent explicitement.
 *
 * Le jeton n'est jamais dans le code ; aucune permission Chrome supplémentaire
 * (l'URL des onglets chatgpt.com est lisible grâce à l'hôte déjà accordé).
 */
"use strict";

var CLE = { endpoint: "endpoint", jeton: "jeton", clientId: "clientId", statut: "dernierStatut" };
var COULEURS = { vert: "#1a7f37", orange: "#b45309", rouge: "#b42318", neutre: "#444" };
var Detect = (typeof globalThis !== "undefined" && globalThis.DetectChatGPT) || null;

function $(id) {
  return document.getElementById(id);
}

function afficherStatut(message, couleur) {
  var zone = $("statut");
  zone.style.color = couleur || COULEURS.neutre;
  zone.textContent = message;
}

function lireConfig() {
  return chrome.storage.local.get([CLE.endpoint, CLE.jeton, CLE.clientId]);
}

function clientIdPersistant(config) {
  if (config[CLE.clientId]) return config[CLE.clientId];
  var id = crypto.randomUUID();
  chrome.storage.local.set({ [CLE.clientId]: id });
  return id;
}

function charger() {
  chrome.storage.local.get([CLE.endpoint, CLE.jeton, CLE.statut]).then(function (valeurs) {
    $("endpoint").value = valeurs[CLE.endpoint] || "";
    $("jeton").value = valeurs[CLE.jeton] || "";
    var statut = valeurs[CLE.statut];
    if (statut) {
      var ligne = "Dernier envoi : " + (statut.ok ? "réussi" : "échec");
      if (statut.code) ligne += " (HTTP " + statut.code + ")";
      if (statut.raison) ligne += " — " + statut.raison;
      ligne += " à " + new Date(statut.a).toLocaleTimeString();
      afficherStatut(ligne, statut.ok ? COULEURS.vert : COULEURS.rouge);
    } else {
      afficherStatut("Aucun envoi pour l'instant. Utilise les boutons de test ci-dessus.");
    }
    vueEnsemble();
  });
}

/**
 * Vue d'ensemble temps réel (mission : diagnostic sans lire de logs) :
 * Serveur / Conversation active / Contexte / dernier envoi. Ne lit que le
 * diagnostic serveur (GET, jamais d'URL/ID) et l'état local de propriété.
 */
async function vueEnsemble() {
  var zone = $("vue");
  if (!zone) return;
  var config = await lireConfig();
  var endpoint = (config[CLE.endpoint] || "").trim();
  var jeton = (config[CLE.jeton] || "").trim();
  if (!endpoint || !jeton) {
    zone.textContent = "Enregistre d'abord endpoint et jeton pour voir l'état du pipeline.";
    return;
  }
  var lignes = [];
  var statut = (await chrome.storage.local.get(CLE.statut))[CLE.statut];
  var g = await requeteJson("GET", endpoint, jeton);
  if (g.erreur === "reseau") {
    zone.textContent = "Serveur : 🔴 injoignable (réseau)\nLe pipeline ne peut pas être vérifié.";
    return;
  }
  if (g.statut === 401) {
    zone.textContent = "Serveur : 🔴 authentification refusée (401)\nJeton invalide ou tourné — relis-le dans ton terminal.";
    return;
  }
  if (!g.ok) {
    lignes.push("Serveur : 🟠 HTTP " + g.statut);
  } else {
    lignes.push("Serveur : 🟢 joignable");
    var sess = await chrome.storage.session.get({ etatCerveau: null });
    var proprietaire = sess.etatCerveau && sess.etatCerveau.proprietaire;
    lignes.push(
      proprietaire ? "Conversation : 🟢 active (/c/…) — heartbeat automatique" : "Conversation : 🟠 aucune conversation active"
    );
    var corps = g.corps || {};
    if (corps.contexte_present) {
      lignes.push("Contexte : 🟢 actif (âge " + Math.round(corps.age_s || 0) + " s)");
    } else if (corps.raison === "contexte_expire") {
      lignes.push(
        "Contexte : 🟠 expiré (dernier dépôt il y a " +
          Math.round(corps.dernier_depot_s || 0) +
          " s)"
      );
    } else {
      lignes.push("Contexte : ⚪ aucun contexte déposé");
    }
  }
  if (statut && statut.a) {
    var s = Math.max(0, Math.round((Date.now() - statut.a) / 1000));
    lignes.push(
      "Dernier envoi : il y a " + s + " s (" + (statut.ok ? "réussi" : "échec") +
      (statut.raison ? " — " + statut.raison : "") + ")"
    );
  }
  zone.textContent = lignes.join("\n");
}

function origineDe(url) {
  try {
    return new URL(url).origin;
  } catch (e) {
    return null;
  }
}

async function garantirPermission(endpoint) {
  var origine = origineDe(endpoint);
  if (!origine) return true;
  var motif = origine + "/*";
  var deja = await chrome.permissions.contains({ origins: [motif] });
  if (deja) return true;
  try {
    return await chrome.permissions.request({ origins: [motif] });
  } catch (e) {
    return false;
  }
}

function enregistrer() {
  var endpoint = $("endpoint").value.trim();
  var jeton = $("jeton").value.trim();
  if (!endpoint || !jeton) {
    afficherStatut("Endpoint et jeton sont requis.", COULEURS.rouge);
    return;
  }
  if (!/^https?:\/\//.test(endpoint)) {
    afficherStatut("L'endpoint doit être une URL http(s).", COULEURS.rouge);
    return;
  }
  garantirPermission(endpoint).then(function (accordee) {
    if (!accordee) {
      afficherStatut("Permission pour cet endpoint refusée : envois impossibles.", COULEURS.rouge);
      return;
    }
    chrome.storage.local.set({ [CLE.endpoint]: endpoint, [CLE.jeton]: jeton }).then(function () {
      afficherStatut("Configuration enregistrée.", COULEURS.vert);
    });
  });
}

/** POST/GET JSON générique. Résout toujours : {erreur:"reseau"} en cas de réseau. */
async function requeteJson(methode, endpoint, jeton, corps) {
  var options = {
    method: methode,
    headers: { Authorization: "Bearer " + jeton },
  };
  if (corps !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(corps);
  }
  try {
    var reponse = await fetch(endpoint, options);
  } catch (e) {
    return { erreur: "reseau", detail: e && e.message };
  }
  var texte = "";
  try {
    texte = await reponse.text();
  } catch (e) {
    texte = "";
  }
  var parsed = null;
  if (texte) {
    try {
      parsed = JSON.parse(texte);
    } catch (e) {
      parsed = null;
    }
  }
  return { ok: reponse.ok, statut: reponse.status, corps: parsed, texte: texte };
}

async function lireConfigOuAfficher() {
  var config = await lireConfig();
  var endpoint = (config[CLE.endpoint] || "").trim();
  var jeton = (config[CLE.jeton] || "").trim();
  if (!endpoint || !jeton) {
    afficherStatut("Enregistre d'abord endpoint et jeton.", COULEURS.rouge);
    return null;
  }
  return { endpoint: endpoint, jeton: jeton, config: config };
}

/**
 * Probe transport/auth : POST `{"actif": false}`. Retourne {ok:true} ou
 * affiche l'état 🔴 correspondant et retourne null.
 */
async function testerServeurInterne() {
  var conf = await lireConfigOuAfficher();
  if (!conf) return null;
  afficherStatut("Test du serveur en cours…", COULEURS.neutre);
  var r = await requeteJson("POST", conf.endpoint, conf.jeton, {
    actif: false,
    client_id: "test-serveur-" + Date.now(),
  });
  if (r.erreur === "reseau") {
    afficherStatut("🔴 Impossible de joindre Tasks MCP (réseau).", COULEURS.rouge);
    return null;
  }
  if (r.statut === 401) {
    afficherStatut(
      "🔴 Serveur accessible — authentification refusée (HTTP 401) : jeton invalide ou tourné.",
      COULEURS.rouge
    );
    return null;
  }
  if (!r.ok) {
    afficherStatut(
      "🔴 Serveur accessible — réponse HTTP " + r.statut + " inattendue. Vérifie l'endpoint.",
      COULEURS.rouge
    );
    return null;
  }
  return { endpoint: conf.endpoint, jeton: conf.jeton, config: conf.config };
}

async function testerServeur() {
  var resultat = await testerServeurInterne();
  if (resultat) {
    afficherStatut(
      "🟢 Serveur accessible — authentification valide.\n(La détection d'une conversation se vérifie avec « Tester la conversation courante ».)",
      COULEURS.vert
    );
  }
}

async function ongletActif() {
  try {
    var onglets = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (onglets && onglets[0] && onglets[0].url) return onglets[0];
  } catch (e) {
    /* pas de "tabs" : on retombe sur currentWindow */
  }
  try {
    var autres = await chrome.tabs.query({ active: true, currentWindow: true });
    if (autres && autres[0]) return autres[0];
  } catch (e) {
    return null;
  }
  return null;
}

function idMasque(id) {
  return id.slice(0, 8) + "…";
}

async function testerConversation() {
  var conf = await testerServeurInterne(); // étape 1 : transport + auth
  if (!conf) return;

  afficherStatut("Recherche de la conversation dans l'onglet actif…", COULEURS.neutre);
  var onglet = await ongletActif();
  if (!onglet || !onglet.url) {
    afficherStatut(
      "🟠 Serveur accessible — aucun onglet actif exploitable. Ouvre ChatGPT et réessaie.",
      COULEURS.orange
    );
    return;
  }
  if (!Detect) {
    afficherStatut("🔴 Détecteur absent (detect.js non chargé).", COULEURS.rouge);
    return;
  }

  var conv = Detect.extraireConversation(onglet.url);
  if (!Detect.estSurChatGPT(onglet.url)) {
    afficherStatut(
      "🟠 Serveur accessible — l'onglet actif n'est pas ChatGPT (" + onglet.url.split("/")[2] + ").",
      COULEURS.orange
    );
    return;
  }
  if (!conv) {
    afficherStatut(
      "🟠 Serveur accessible — aucune conversation /c/<id> détectée dans l'onglet actif.\n" +
        "Page ChatGPT détectée (« " +
        onglet.url.replace("https://chatgpt.com", "") +
        " ») mais ce n'est pas une conversation.",
      COULEURS.orange
    );
    return;
  }

  // Conversation réelle détectée : on envoie SA VRAIE URL au backend.
  afficherStatut("Conversation détectée — envoi de l'URL réelle…", COULEURS.neutre);
  var titre = Detect.titrePage(onglet.title);
  var r = await requeteJson("POST", conf.endpoint, conf.jeton, {
    url: conv.url,
    title: titre || undefined,
    client_id: clientIdPersistant(conf.config),
    onglet_id: "options",
  });
  if (r.erreur === "reseau") {
    afficherStatut("🔴 Impossible de joindre Tasks MCP (réseau) pendant l'envoi.", COULEURS.rouge);
    return;
  }
  if (r.statut === 401) {
    afficherStatut(
      "🔴 Serveur accessible — authentification refusée (HTTP 401) pendant l'envoi.",
      COULEURS.rouge
    );
    return;
  }
  if (r.statut === 400) {
    afficherStatut(
      "🔴 URL ChatGPT détectée mais format de conversation invalide (HTTP 400).\n" +
        "Rapport serveur : " +
        ((r.corps && r.corps.message) || "url_invalide"),
      COULEURS.rouge
    );
    return;
  }
  if (!r.ok || !r.corps || r.corps.statut !== "ok" || r.corps.conversation_detectee !== true) {
    afficherStatut(
      "🔴 Réponse inattendue du serveur (HTTP " + r.statut + ") : " + (r.texte || "").slice(0, 200),
      COULEURS.rouge
    );
    return;
  }

  // Dépôt accepté : confirmation du stockage via le diagnostic interne.
  var g = await requeteJson("GET", conf.endpoint, conf.jeton);
  var enregistre = g.ok && g.corps && g.corps.contexte_present === true;
  if (enregistre) {
    afficherStatut(
      "🟢 Serveur accessible — conversation détectée — ID présent — contexte enregistré.\n" +
        "Conversation : " +
        idMasque(conv.conversation_id) +
        " (âge " +
        g.corps.age_s +
        " s). Le prochain tasks_create ajoutera le lien dans les notes.",
      COULEURS.vert
    );
  } else {
    afficherStatut(
      "🟢 Serveur accessible — conversation détectée et ID présent, mais le diagnostic de\n" +
        "stockage n'a pas confirmé le contexte (HTTP " +
        (g.statut || "?") +
        ").",
      COULEURS.orange
    );
  }
}

function rafraichirVue() {
  vueEnsemble().catch(function () {
    /* silencieux : l'état s'affichera au prochain tick */
  });
}

document.addEventListener("DOMContentLoaded", function () {
  charger();
  setInterval(rafraichirVue, 5000); // popup/options ouverte : indicateurs vivants
});
$("enregistrer").addEventListener("click", function () {
  enregistrer();
  setTimeout(rafraichirVue, 300);
});
$("tester-serveur").addEventListener("click", async function () {
  await testerServeur();
  rafraichirVue();
});
$("tester-conversation").addEventListener("click", async function () {
  await testerConversation();
  rafraichirVue();
});
