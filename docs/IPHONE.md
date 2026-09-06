# iPhone — Apple Rappels ↔ CalDAV (procédure exacte)

Objectif : un compte CalDAV privé (NetBird) dans l'application Rappels, avec la liste
**Inbox** comme boîte de capture, synchronisée avec tasks-mcp.

## 0. Prérequis iPhone

1. L'iPhone est un pair NetBird (app NetBird installée et connectée — déjà le cas chez toi).
2. **Installer la CA privée** (une seule fois) pour que TLS soit validé sans message :
   - Récupère le fichier `tasks-ca.pem` (CA de la stack tasks) et envoie-le à l'iPhone
     (AirDrop depuis l'ordi, ou Fichiers → sur mon Mac/PC).
   - Ouvre le fichier → **Profil téléchargé**.
   - Réglages → **Général → Gestion des appareils/VPN** → installe le profil.
   - Réglages → **Général → Informations → Réglages de confiance des certificats** →
     active la **confiance totale** pour « Tasks CalDAV Private CA ».

## 1. Ajout du compte dans Réglages

1. **Réglages → Rappels → Comptes → Ajouter un compte → Autre → Compte CalDAV**.
2. Renseigner exactement :
   - **Serveur** : `netbird.internal.example`
   - **Nom d'utilisateur** : `juliann`
   - **Mot de passe** : (le mot de passe CalDAV — récupérable par
     `sudo bash /srv/tasks/scripts/afficher-secret.sh`, ou celui que tu auras choisi
     via `creer-compte-caldav.sh`)
   - **Description** : `Tasks VPS` (libre)
3. **Suivant** : iOS découvre `Inbox` et `Corbeille`.
4. Cocher uniquement **Rappels** (décocher Calendriers si proposé).
5. **Enregistrer**.

> SSL est actif par défaut sur le port 5232 (le serveur est en HTTPS NetBird). Si iOS
> demande un port : laisser vide (443) n'est pas valable ici — le port est **5232** ;
> iOS l'utilise automatiquement pour les comptes CalDAV « Autre » après la découverte.
> En cas de doute, utilise l'URL complète `https://netbird.internal.example:5232`.

## 2. Liste par défaut / Inbox

- Dans **Rappels**, toutes les listes du compte CalDAV apparaissent (Inbox, Corbeille).
- Siri et le bouton « + » ajoutent dans la liste par défaut :
  **Réglages → Rappels → Liste par défaut → choisir Inbox (Tasks VPS)**.
- C'est là que tu captures ; le MCP lit surtout `Inbox`.

## 3. Vérification rapide

1. Crée dans Rappels (liste Inbox) : `TEST MCP IPHONE`.
2. Côté MCP (voir TESTS.md / un client MCP) : `tasks_list` ou `tasks_search(title="TEST")`
   doit la retrouver.
3. Modifie-la depuis le MCP ; elle doit se mettre à jour sur l'iPhone en quelques secondes.

## 4. Tests bout-en-bout A→F (à faire avec l'assistant)

- **A** : tu crées `TEST MCP IPHONE` sur l'iPhone → le MCP la retrouve.
- **B** : le MCP la renomme `TEST MCP MODIFIÉ` → visible sur l'iPhone.
- **C** : le MCP lui pose une échéance → affichée correctement (Europe/Paris).
- **D** : tu la coches sur l'iPhone → le MCP la voit `completed`.
- **E** : le MCP crée une tâche → elle apparaît dans Rappels.
- **F** : conflit : l'iPhone modifie pendant que le MCP écrit sur un etag périmé →
  pas d'écrasement silencieux (`conflit:true` + tâche actuelle).

## Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| « Impossible de vérifier l'identité du serveur » | CA non installée/confiance partielle | refaire l'étape 0 |
| Le compte ne se synchronise pas | NetBird coupé sur l'iPhone | vérifier l'app NetBird (IP 10.200.x) |
| Liste vide côté iPhone | mauvaise liste par défaut | Réglages → Rappels → Liste par défaut |
| 401 | mauvais mot de passe | `creer-compte-caldav.sh` puis re-saisir |
| Le serveur ne répond pas | service arrêté | `sudo systemctl status tasks-mcp` ; `docker ps` |
