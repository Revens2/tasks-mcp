# tasks-gateway-rs — facade Rust devant l'upstream Python fige

Miroir de `tasks_gateway` Python : validation Bearer + politique explicite
10 lectures / 6 ecritures / 0 admin + proxy vers `tasks_mcp` (`:8791`,
loopback, sans auth — isolation systemd, `x-tasks-mcp-acteur` injecte apres
authentification). Metier conserve : CalDAV + OAuth Python.

## Contrat conserve

`initialize`/sessions passthrough, `tools/list` filtree par portees (10 en
lecture, 16 en lecture+ecriture, jamais l'inconnu), `tools/call` avec refus
local AVANT envoi, allowlist methodes upstream SDK 2.2.0 (-32601), JSON
strict anti-cles-dupliquees (-32600), ere duale (quirk ChatGPT rabaisse vers
2025-11-25, enveloppe 2026-07-28 intacte, desaccord -32020/400, doublons
d'en-tetes -32020/400), `tools/list` illisible jamais relayee (-32603),
`resultType` 2026-07-28 : cache force `private`/`ttlMs 0`.

## Differences assumees (contrat outils intact)

401 au format framework (URL PRM exacte presente), PRM sans
`bearer_methods_supported`, metadata AS format framework (`none` seul) servie
a la racine (suffixe 404 comme le Python), ajout `/ready`, pool HTTP sans
plafond total explicite (reqwest, >= capacite Python 300), semantique
framework ecriture⊇lecture (inatteignable : middleware exige la lecture).

## Environnement (`TASKS_MCP_RS_*`)

| Variable | Defaut | Role |
|---|---|---|
| `TASKS_MCP_RS_ISSUER` | issuer prod | HTTPS requis |
| `TASKS_MCP_RS_UPSTREAM` | `http://127.0.0.1:8791` | loopback requis |
| `TASKS_MCP_RS_PORT` | `18992` (canary) | `8792` a la bascule (GATEE) |
| `TASKS_MCP_RS_TOKEN` / `_TOKEN_FILE` | `/opt/tasks-gateway-rs/.mcp_token` | Bearer dedie >= 32, fail-closed |
| `TASKS_MCP_RS_TOKEN_SCOPES` | lecture+ecriture | quoté dans l'unit (lecon lot 2) |
| `TASKS_MCP_RS_CONSENT_HASH` | vide (= refuse) | PBKDF2 consentement |

## Preuves lot tasks (passerelle)

- `cargo fmt --check` 0, `cargo clippy --all-targets -- -D warnings` 0.
- `cargo test` : politique 10/6, visibles 10/16, ere (matrice quirk/moderne),
  strict anti-doublons, allowlist, 401, health/PRM, acteur+mode injectes,
  rabaissement constate par mock, refus locaux, -32603, cache force prive.
- Canary VPS `:18992` + diff contrat zero vs `:8792` : voir `progress.md`.
- Bascule `:8792` GATEE (OAuth JWT meme famille + rotation eventuelle =
  exploitant) : prod Python intacte. Upstream `tasks_mcp` natif/adapter =
  jalon suivant (metier CalDAV conserve).
