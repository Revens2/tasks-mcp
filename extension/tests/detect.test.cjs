/**
 * Tests de la logique pure de détection (extension) — node --test.
 *
 * Couvre : détection initiale, navigation SPA (changement d'URL sans
 * rechargement), changement d'onglet actif, URL /c/… détectée, autres pages
 * ChatGPT ignorées (et « efface » hors conversation), heartbeat périodique.
 *
 * Exécution : node --test extension/tests/detect.test.js
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// detect.js est un script classique (content script Chrome) sans export : on
// l'exécute dans un contexte vm pour en récupérer l'API publique.
const code = fs.readFileSync(
  path.join(__dirname, "..", "chatgpt-contexte", "detect.js"),
  "utf8"
);
// Le contexte vm n'a pas les globals hôtes (URL…) par défaut : on les fournit.
const boite = { URL, console };
vm.createContext(boite);
vm.runInContext(code, boite);
const Detect = boite.DetectChatGPT;
assert.ok(Detect, "detect.js doit exposer DetectChatGPT");

const UUID = "67d5b8f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f";
const CONV = `https://chatgpt.com/c/${UUID}`;
const AUTRE_CONV = `https://chatgpt.com/c/aaaaaaaa-1111-2222-3333-444444444444`;

function etatVierge() {
  return { dernierType: null, dernierUrl: null, dernierEnvoiA: 0 };
}

// --- URL /c/… ---------------------------------------------------------------

test("extrait une URL de conversation valide", () => {
  const r = Detect.extraireConversation(CONV);
  // L'objet vient du contexte vm : comparaison après passage JSON.
  assert.deepEqual(JSON.parse(JSON.stringify(r)), { url: CONV, conversation_id: UUID });
});

test("extrait en ignorant le slash final", () => {
  assert.equal(Detect.extraireConversation(CONV + "/").url, CONV);
});

test("rejette les URL hors conversation", () => {
  for (const mauvaise of [
    "https://chatgpt.com/",
    "https://chatgpt.com/share/abc",
    "https://chatgpt.com/g/g-123",
    "https://chatgpt.com/c/",
    "https://chatgpt.com/c/a/b",
    "https://evil.com/c/abc",
    "http://chatgpt.com/c/abc",
    "https://www.chatgpt.com/c/abc",
    "javascript:alert(1)",
    "pas une url",
    null,
    undefined,
    42,
  ]) {
    assert.equal(Detect.extraireConversation(mauvaise), null, `devrait rejeter: ${mauvaise}`);
  }
});

test("estSurChatGPT ne retient que chatgpt.com en https", () => {
  assert.equal(Detect.estSurChatGPT(CONV), true);
  assert.equal(Detect.estSurChatGPT("https://chatgpt.com/"), true);
  assert.equal(Detect.estSurChatGPT("https://evil.com/"), false);
  assert.equal(Detect.estSurChatGPT("http://chatgpt.com/"), false);
});

// --- détection initiale -----------------------------------------------------

test("détection initiale : page de conversation visible → contexte", () => {
  const action = Detect.prochaineAction(etatVierge(), {
    href: CONV,
    titre: "Ma conversation - ChatGPT",
    visible: true,
    maintenant: 1000,
  });
  assert.equal(action.type, "contexte");
  assert.equal(action.payload.url, CONV);
  assert.equal(action.payload.conversation_id, UUID);
  assert.equal(action.payload.title, "Ma conversation");
});

// --- navigation SPA ---------------------------------------------------------

test("changement d'URL de conversation (SPA) → nouveau contexte immédiat", () => {
  const etat = { dernierType: "contexte", dernierUrl: CONV, dernierEnvoiA: 500 };
  const action = Detect.prochaineAction(etat, {
    href: AUTRE_CONV,
    titre: "Nouvelle conversation",
    visible: true,
    maintenant: 1000,
  });
  assert.equal(action.type, "contexte");
  assert.equal(action.payload.url, AUTRE_CONV);
});

test("même conversation et heartbeat pas dû → aucune action", () => {
  const etat = { dernierType: "contexte", dernierUrl: CONV, dernierEnvoiA: 500 };
  const action = Detect.prochaineAction(etat, {
    href: CONV,
    titre: "Ma conversation",
    visible: true,
    maintenant: 6000, // < 120 s après l'envoi
  });
  assert.equal(action, null);
});

test("heartbeat dû (> 120 s) → contexte renvoyé (TTL serveur rafraîchi)", () => {
  const etat = { dernierType: "contexte", dernierUrl: CONV, dernierEnvoiA: 1000 };
  const action = Detect.prochaineAction(etat, {
    href: CONV,
    titre: "Ma conversation",
    visible: true,
    maintenant: 1000 + 121000,
  });
  assert.equal(action.type, "contexte");
  assert.equal(action.payload.url, CONV);
});

// --- changement d'onglet ----------------------------------------------------

test("onglet non visible → aucune action (pas de faux contexte)", () => {
  const action = Detect.prochaineAction(etatVierge(), {
    href: CONV,
    titre: "Ma conversation",
    visible: false,
    maintenant: 1000,
  });
  assert.equal(action, null);
});

test("retour sur l'onglet visible avec URL différente → contexte immédiat", () => {
  // L'onglet était caché sur CONV (rien n'a été envoyé) ; au retour il affiche
  // AUTRE_CONV : l'état n'a pas changé côté extension → contexte envoyé.
  const action = Detect.prochaineAction(etatVierge(), {
    href: AUTRE_CONV,
    titre: "Nouvelle conversation",
    visible: true,
    maintenant: 2000,
  });
  assert.equal(action.type, "contexte");
  assert.equal(action.payload.url, AUTRE_CONV);
});

test("retour visible sur la même conversation déjà envoyée → heartbeat seulement", () => {
  const etat = { dernierType: "contexte", dernierUrl: CONV, dernierEnvoiA: 500 };
  const action = Detect.prochaineAction(etat, {
    href: CONV,
    titre: "Ma conversation",
    visible: true,
    maintenant: 900, // heartbeat pas dû
  });
  assert.equal(action, null);
});

// --- autres pages ChatGPT : effacement du contexte ---------------------------

test("page ChatGPT hors conversation (accueil) → efface", () => {
  const action = Detect.prochaineAction(etatVierge(), {
    href: "https://chatgpt.com/",
    titre: "ChatGPT",
    visible: true,
    maintenant: 1000,
  });
  assert.equal(action.type, "efface");
});

test("page share → efface (jamais de lien public)", () => {
  const action = Detect.prochaineAction(etatVierge(), {
    href: "https://chatgpt.com/share/abc",
    titre: "ChatGPT",
    visible: true,
    maintenant: 1000,
  });
  assert.equal(action.type, "efface");
});

test("efface déjà envoyé récemment → aucune action", () => {
  const etat = { dernierType: "efface", dernierUrl: null, dernierEnvoiA: 1000 };
  const action = Detect.prochaineAction(etat, {
    href: "https://chatgpt.com/",
    titre: "ChatGPT",
    visible: true,
    maintenant: 60_000,
  });
  assert.equal(action, null);
});

test("après efface, retour sur /c/… → nouveau contexte", () => {
  const etat = { dernierType: "efface", dernierUrl: null, dernierEnvoiA: 500 };
  const action = Detect.prochaineAction(etat, {
    href: CONV,
    titre: "Ma conversation",
    visible: true,
    maintenant: 1000,
  });
  assert.equal(action.type, "contexte");
});

// --- titre ------------------------------------------------------------------

test("titre : suffixe ChatGPT retiré, espaces aplatis, borné", () => {
  assert.equal(Detect.titrePage("Planifier  le  week-end   - ChatGPT"), "Planifier le week-end");
  assert.equal(Detect.titrePage("ChatGPT"), null);
  assert.equal(Detect.titrePage(""), null);
  assert.equal(Detect.titrePage("x".repeat(500)).length, 200);
});
