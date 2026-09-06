/**
 * Tests de la logique PURE de parsing URL/titre (extension) — node --test.
 *
 * detect.js est un script classique (content script Chrome) sans export : on
 * l'exécute dans un contexte vm pour en récupérer l'API publique.
 *
 * Exécution : node --test extension/tests/detect.test.cjs
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const code = fs.readFileSync(
  path.join(__dirname, "..", "chatgpt-contexte", "detect.js"),
  "utf8"
);
const boite = { URL, console };
vm.createContext(boite);
vm.runInContext(code, boite);
const Detect = boite.DetectChatGPT;
assert.ok(Detect, "detect.js doit exposer DetectChatGPT");

const UUID = "67d5b8f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f";
const CONV = `https://chatgpt.com/c/${UUID}`;
const AUTRE_CONV = `https://chatgpt.com/c/aaaaaaaa-1111-2222-3333-444444444444`;

test("extrait une URL de conversation valide", () => {
  const r = Detect.extraireConversation(CONV);
  assert.deepEqual(JSON.parse(JSON.stringify(r)), { url: CONV, conversation_id: UUID });
});

test("extrait en ignorant le slash final", () => {
  assert.equal(Detect.extraireConversation(CONV + "/").url, CONV);
});

test("ID de longueur minimale accepté (8), plus court refusé", () => {
  const court = "a".repeat(8);
  assert.equal(Detect.extraireConversation("https://chatgpt.com/c/" + court).conversation_id, court);
  for (const tropCourt of ["abc", "a".repeat(7)]) {
    assert.equal(Detect.extraireConversation("https://chatgpt.com/c/" + tropCourt), null);
  }
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

test("titre : suffixe ChatGPT retiré, espaces aplatis, borné", () => {
  assert.equal(Detect.titrePage("Planifier  le  week-end   - ChatGPT"), "Planifier le week-end");
  assert.equal(Detect.titrePage("ChatGPT"), null);
  assert.equal(Detect.titrePage(""), null);
  assert.equal(Detect.titrePage("x".repeat(500)).length, 200);
});
