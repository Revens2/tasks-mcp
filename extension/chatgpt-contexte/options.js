/**
 * Page d'options : endpoint + jeton « browser context writer ».
 *
 * Le jeton est stocké dans chrome.storage.local (jamais de chrome.storage.sync,
 * jamais dans le code). Au premier enregistrement (ou si l'endpoint change),
 * l'extension demande la permission d'hôte nécessaire pour l'endpoint choisi.
 */
"use strict";

var CLE = { endpoint: "endpoint", jeton: "jeton", clientId: "clientId", statut: "dernierStatut" };

function $(id) {
  return document.getElementById(id);
}

function afficherStatut(message, couleur) {
  var zone = $("statut");
  zone.style.color = couleur || "#444";
  zone.textContent = message;
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
      afficherStatut(ligne, statut.ok ? "#1a7f37" : "#b42318");
    } else {
      afficherStatut("Aucun envoi pour l'instant.");
    }
  });
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
    afficherStatut("Endpoint et jeton sont requis.", "#b42318");
    return;
  }
  if (!/^https?:\/\//.test(endpoint)) {
    afficherStatut("L'endpoint doit être une URL http(s).", "#b42318");
    return;
  }
  garantirPermission(endpoint).then(function (accordee) {
    if (!accordee) {
      afficherStatut("Permission pour cet endpoint refusée : envois impossibles.", "#b42318");
      return;
    }
    chrome.storage.local.set({ [CLE.endpoint]: endpoint, [CLE.jeton]: jeton }).then(function () {
      afficherStatut("Configuration enregistrée.", "#1a7f37");
    });
  });
}

function tester() {
  chrome.storage.local.get([CLE.endpoint, CLE.jeton]).then(async function (valeurs) {
    var endpoint = (valeurs[CLE.endpoint] || "").trim();
    var jeton = (valeurs[CLE.jeton] || "").trim();
    if (!endpoint || !jeton) {
      afficherStatut("Enregistrez d'abord endpoint et jeton.", "#b42318");
      return;
    }
    afficherStatut("Test en cours…", "#444");
    try {
      var reponse = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + jeton },
        // Corps inoffensif : efface le contexte d'un client jetable.
        body: JSON.stringify({ actif: false, client_id: "options-test-" + Date.now() }),
      });
      afficherStatut(
        reponse.ok
          ? "Connexion OK (HTTP " + reponse.status + ")."
          : "Réponse HTTP " + reponse.status + " : vérifiez le jeton et l'endpoint.",
        reponse.ok ? "#1a7f37" : "#b42318"
      );
    } catch (e) {
      afficherStatut("Échec réseau : " + e.message, "#b42318");
    }
  });
}

document.addEventListener("DOMContentLoaded", charger);
$("enregistrer").addEventListener("click", enregistrer);
$("tester").addEventListener("click", tester);
