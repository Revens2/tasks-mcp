#!/usr/bin/env bash
# PKI privée de la stack tasks (root). Créée UNE FOIS ; idempotente (ne régénère pas
# une CA existante, remplace seulement le certificat feuille si demandé).
#
# V1 "NetBird uniquement" : certificat délivré par une CA privée locale. L'iPhone doit
# installer la CA (/srv/tasks/pki/ca/tasks-ca.pem, voir docs/IPHONE.md) UNE fois ; le
# certificat feuille est alors validé sans autre manipulation. Phase 2 publique :
# remplacer feuille + CA par Let's Encrypt (duckdns) — l'architecture nginx ne change pas.
#
# SAN couverts : noms DNS NetBird (netbird.internal.example, caldav.internal.example)
# + adresse NetBird 198.51.100.10. Valdité feuille 800 jours (limite Apple 825 j).
#
# Usage : sudo bash /srv/tasks/scripts/init-pki.sh [--force-leaf]
set -euo pipefail
RACINE="/srv/tasks"
CA_DIR="$RACINE/pki/ca"
FEUILLE_DIR="$RACINE/pki/caldav"
ADRESSE_NETBIRD="198.51.100.10"
NOMS="DNS:netbird.internal.example,DNS:caldav.internal.example,IP:$ADRESSE_NETBIRD"
FORCE_LEAF="${1:-}"

umask 077
mkdir -p "$CA_DIR" "$FEUILLE_DIR"

if [ ! -f "$CA_DIR/tasks-ca.key" ] || [ ! -f "$CA_DIR/tasks-ca.pem" ]; then
    echo "== création CA privée tasks =="
    openssl genrsa -out "$CA_DIR/tasks-ca.key" 4096
    openssl req -x509 -new -key "$CA_DIR/tasks-ca.key" \
        -sha256 -days 3650 \
        -subj "/CN=Tasks CalDAV Private CA/O=Selfhosted VPS/OU=tasks-mcp" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$CA_DIR/tasks-ca.pem"
else
    echo "== CA existante conservée =="
fi

if [ ! -f "$FEUILLE_DIR/privkey.pem" ] || [ "$FORCE_LEAF" = "--force-leaf" ]; then
    echo "== certificat feuille CalDAV =="
    openssl genrsa -out "$FEUILLE_DIR/privkey.pem" 2048
    openssl req -new -key "$FEUILLE_DIR/privkey.pem" \
        -subj "/CN=netbird.internal.example" -out /tmp/tasks-caldav.csr
    openssl x509 -req -in /tmp/tasks-caldav.csr \
        -CA "$CA_DIR/tasks-ca.pem" -CAkey "$CA_DIR/tasks-ca.key" -CAcreateserial \
        -sha256 -days 800 \
        -extfile <(printf "subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth" "$NOMS") \
        -out "$FEUILLE_DIR/fullchain.pem"
    rm -f /tmp/tasks-caldav.csr
else
    echo "== feuille existante conservée (--force-leaf pour régénérer) =="
fi

# Permissions : CA en lecture seule root ; feuille lisible par nginx (master root).
chown -R root:root "$CA_DIR" "$FEUILLE_DIR"
chmod 700 "$CA_DIR"
chmod 600 "$CA_DIR"/*
chmod 700 "$FEUILLE_DIR"
chmod 600 "$FEUILLE_DIR/privkey.pem"
chmod 644 "$FEUILLE_DIR/fullchain.pem"

echo "== vérification =="
openssl verify -CAfile "$CA_DIR/tasks-ca.pem" "$FEUILLE_DIR/fullchain.pem"
openssl x509 -in "$FEUILLE_DIR/fullchain.pem" -noout -subject -enddate
echo "OK : CA -> $CA_DIR/tasks-ca.pem (à installer sur l'iPhone, voir docs/IPHONE.md)"
