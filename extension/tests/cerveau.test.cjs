/**
 * Tests de la logique PURE « cerveau » (propriété du contexte) — node --test.
 *
 * Couvre les invariants qui corrigent la cause racine du bug live :
 *  - le contexte appartient à la dernière conversation réellement activée ;
 *  - une page ChatGPT sans conversation ne peut jamais effacer le contexte
 *    d'une conversation ouverte dans un autre onglet (plus d'effacement en
 *    boucle toutes les ~2 min) ;
 *  - le heartbeat rafraîchit le propriétaire même en arrière-plan ;
 *  - bascule A → B → A sans inversion ; fermeture/navigation du propriétaire.
 *
 * Exécution : node --test extension/tests/cerveau.test.cjs
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function charger() {
  const boite = { URL, console };
  vm.createContext(boite);
  for (const fichier of ["detect.js", "cerveau.js"]) {
    const code = fs.readFileSync(
      path.join(__dirname, "..", "chatgpt-contexte", fichier),
      "utf8"
    );
    vm.runInContext(code, boite);
  }
  assert.ok(boite.DetectChatGPT, "detect.js doit exposer DetectChatGPT");
  assert.ok(boite.Cerveau, "cerveau.js doit exposer Cerveau");
  return boite;
}

const boite = charger();
const Cerveau = boite.Cerveau;

const UUID_A = "aaaaaaaa-1111-2222-3333-444444444444";
const UUID_B = "bbbbbbbb-1111-2222-3333-444444444444";
const CONV_A = `https://chatgpt.com/c/${UUID_A}`;
const CONV_B = `https://chatgpt.com/c/${UUID_B}`;
const ACCUEIL = "https://chatgpt.com/";

// -- helpers ---------------------------------------------------------------

function etat(proprietaire) {
  return { proprietaire: proprietaire || null };
}

function prop(tabId, url, convId, titre) {
  return { tabId, url, convId, titre: titre || null };
}

function evActivation(tabId, url, titre) {
  return { type: "activation", tabId, url, titre: titre || null };
}
function evUrl(tabId, url, opts) {
  const o = opts || {};
  return { type: "url_onglet", tabId, url, titre: o.titre || null, actif: !!o.actif };
}
function evCoeur() {
  return { type: "coeur" };
}
function evFermeture(tabId) {
  return { type: "fermeture", tabId };
}

// -- activation --------------------------------------------------------------

test("activation sur une conversation → devient propriétaire + enregistre", () => {
  const r = Cerveau.surEvenement(etat(), evActivation(7, CONV_A, "Ma conv - ChatGPT"));
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.action.ongletId, 7);
  assert.equal(r.action.url, CONV_A);
  assert.equal(r.action.convId, UUID_A);
  assert.equal(r.action.titre, "Ma conv");
  assert.equal(r.etat.proprietaire.tabId, 7);
});

test("activation sur page sans conversation (accueil) → aucune action", () => {
  const r = Cerveau.surEvenement(etat(), evActivation(7, ACCUEIL, "ChatGPT"));
  assert.equal(r.action, null);
  assert.equal(r.etat.proprietaire, null);
});

test("RÉGRESSION : activation accueil ne dépossède pas une conversation en arrière-plan", () => {
  // Onglet 7 = conversation A (en arrière-plan, fenêtre réduite) ; l'utilisateur
  // active ensuite un onglet accueil (onglet 9) : le contexte A DOIT survivre.
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evActivation(9, ACCUEIL, "ChatGPT"));
  assert.equal(r.action, null);
  assert.ok(r.etat.proprietaire, "le propriétaire doit rester");
  assert.equal(r.etat.proprietaire.tabId, 7);
});

test("bascule A → B : la conversation B activée devient propriétaire", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evActivation(8, CONV_B, "Conv B"));
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.action.url, CONV_B);
  assert.equal(r.etat.proprietaire.tabId, 8);
});

test("retour B → A : aucune inversion (le contexte redevient A)", () => {
  let etatCourant = etat(prop(8, CONV_B, UUID_B, null));
  const r = Cerveau.surEvenement(etatCourant, evActivation(7, CONV_A, "Conv A"));
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.action.url, CONV_A);
  assert.equal(r.etat.proprietaire.tabId, 7);
});

test("activation sur URL illisible (hors permission) → aucune action, propriétaire intact", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evActivation(10, null, null));
  assert.equal(r.action, null);
  assert.equal(r.etat.proprietaire.tabId, 7);
});

// -- navigation SPA / URL ----------------------------------------------------

test("SPA : l'onglet propriétaire change /c/A → /c/B → enregistre B immédiatement", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(7, CONV_B, { titre: "Conv B" }));
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.action.url, CONV_B);
  assert.equal(r.etat.proprietaire.url, CONV_B);
  assert.equal(r.etat.proprietaire.convId, UUID_B);
});

test("SPA : retour /c/B → /c/A dans le même onglet → re-enregistre A", () => {
  let etatCourant = etat(prop(7, CONV_B, UUID_B, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(7, CONV_A, { titre: "Conv A" }));
  assert.equal(r.action.url, CONV_A);
  assert.equal(r.etat.proprietaire.convId, UUID_A);
});

test("navigation hors conversation de l'onglet PROPRIÉTAIRE → efface", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(7, ACCUEIL, { titre: "ChatGPT" }));
  assert.equal(r.action.type, "effacer");
  assert.equal(r.action.ongletId, 7);
  assert.equal(r.etat.proprietaire, null);
});

test("RÉGRESSION : onglet NON propriétaire quittant sa conversation n'efface RIEN", () => {
  // Onglet 9 (accueil ou autre page sans conversation) ne peut pas effacer le
  // contexte de l'onglet 7 — c'était le bug live (effacement en boucle).
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(9, ACCUEIL, { titre: "ChatGPT", actif: true }));
  assert.equal(r.action, null);
  assert.ok(r.etat.proprietaire, "le contexte du propriétaire doit survivre");
  assert.equal(r.etat.proprietaire.tabId, 7);
});

test("conversation chargée en onglet INACTIF (non propriétaire) → ne vole pas la propriété", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(8, CONV_B, { actif: false }));
  assert.equal(r.action, null);
  assert.equal(r.etat.proprietaire.tabId, 7);
});

test("conversation chargée en onglet ACTIF (non propriétaire) → devient propriétaire", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(8, CONV_B, { actif: true }));
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.etat.proprietaire.tabId, 8);
});

test("rechargement de l'onglet propriétaire (URL identique) → mise à jour + enregistre", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evUrl(7, CONV_A, { actif: false }));
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.action.url, CONV_A);
  assert.equal(r.etat.proprietaire.tabId, 7);
});

test("aucune propriété + onglet inactif sur conversation → rien (pas de contexte fantôme)", () => {
  const r = Cerveau.surEvenement(etat(), evUrl(8, CONV_B, { actif: false }));
  assert.equal(r.action, null);
  assert.equal(r.etat.proprietaire, null);
});

// -- heartbeat ----------------------------------------------------------------

test("heartbeat avec propriétaire → enregistre (TTL serveur rafraîchi)", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, "Ma conv"));
  const r = Cerveau.surEvenement(etatCourant, evCoeur());
  assert.equal(r.action.type, "enregistrer");
  assert.equal(r.action.ongletId, 7);
  assert.equal(r.action.url, CONV_A);
  assert.equal(r.action.titre, "Ma conv");
});

test("heartbeat sans propriétaire → aucune action", () => {
  const r = Cerveau.surEvenement(etat(), evCoeur());
  assert.equal(r.action, null);
});

// -- fermeture ----------------------------------------------------------------

test("fermeture de l'onglet propriétaire → efface + plus de propriétaire", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evFermeture(7));
  assert.equal(r.action.type, "effacer");
  assert.equal(r.action.ongletId, 7);
  assert.equal(r.etat.proprietaire, null);
});

test("fermeture d'un onglet non propriétaire → aucune action", () => {
  let etatCourant = etat(prop(7, CONV_A, UUID_A, null));
  const r = Cerveau.surEvenement(etatCourant, evFermeture(8));
  assert.equal(r.action, null);
  assert.equal(r.etat.proprietaire.tabId, 7);
});

// -- scénario long (mission : A actif → B → retour A, sans jamais d'efface croisé) --

test("scénario multi-conversation complet A → B → A", () => {
  let e = etat();
  let r = Cerveau.surEvenement(e, evActivation(7, CONV_A, "Conv A"));
  assert.equal(r.action.url, CONV_A);
  e = r.etat;

  // Onglet accueil visible (autre onglet) : ne doit RIEN casser.
  r = Cerveau.surEvenement(e, evActivation(9, ACCUEIL, "ChatGPT"));
  assert.equal(r.action, null);
  e = r.etat;

  // Passage à la conversation B.
  r = Cerveau.surEvenement(e, evActivation(8, CONV_B, "Conv B"));
  assert.equal(r.action.url, CONV_B);
  e = r.etat;

  // Retour à A.
  r = Cerveau.surEvenement(e, evActivation(7, CONV_A, "Conv A"));
  assert.equal(r.action.url, CONV_A);
  e = r.etat;

  // Heartbeats successifs : toujours A (l'onglet B, pourtant ouvert, est
  // silencieux — ce n'est plus lui le propriétaire).
  r = Cerveau.surEvenement(e, evCoeur());
  assert.equal(r.action.url, CONV_A);
});
