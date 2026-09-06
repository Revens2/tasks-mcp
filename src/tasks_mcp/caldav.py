"""Client CalDAV minimal et robuste (cible : Radicale 3.x).

Toutes les écritures conditionnelles passent par If-Match (jamais de last-write-wins) :
en cas de 412, l'appelant doit re-lire l'item et décider (merge sûr ou erreur claire).
Les chemins (href) retournés par le serveur sont utilisés tels quels ; pour construire
un href à partir d'un nom de liste on encode chaque segment (quote, safe="").
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, unquote

import httpx

DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"

_ENTETES_XML = {"Content-Type": "application/xml; charset=utf-8"}


class ErreurCalDAV(RuntimeError):
    """Erreur CalDAV avec statut HTTP quand disponible."""

    def __init__(self, message: str, statut: int | None = None, href: str | None = None):
        super().__init__(message)
        self.statut = statut
        self.href = href


@dataclass(slots=True)
class Collection:
    href: str  # chemin absolu, se termine par '/'
    nom: str  # nom décodé (segment final)

    @property
    def url(self) -> str:
        return self.href


def encoder_segment(nom: str) -> str:
    return quote(nom, safe="")


def decoder_segment(href: str) -> str:
    return unquote(href.rstrip("/").rsplit("/", 1)[-1])


def _href_local(element: ET.Element, defaut: str) -> str:
    for enfant in element:
        if enfant.tag.split("}", 1)[-1].lower() == "href":
            return (enfant.text or "").strip()
    return defaut


def _texte_local(element: ET.Element) -> str | None:
    texte = element.text
    if texte is None:
        return None
    return " ".join(texte.split()) or None


def _valeur_propriete(element: ET.Element) -> str:
    """Valeur d'une propriété DAV : texte direct, href imbriqué, ou noms des enfants.

    current-user-principal / calendar-home-set contiennent un <href> enfant ;
    resourcetype contient des enfants (collection, calendar) sans texte direct.
    """
    direct = _texte_local(element)
    if direct:
        return direct
    for enfant in element.iter():
        if enfant.tag.split("}", 1)[-1].lower() == "href" and (enfant.text or "").strip():
            return (enfant.text or "").strip()
    noms = [c.tag.split("}", 1)[-1].lower() for c in element if isinstance(c.tag, str)]
    return " ".join(n for n in noms if n)



class CalDAV:
    def __init__(self, url: str, utilisateur: str, mot_de_passe: str, timeout: float = 30.0):
        self._base = url.rstrip("/")
        self._http = httpx.Client(
            base_url=self._base,
            auth=(utilisateur, mot_de_passe),
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=True,
        )

    def fermer(self) -> None:
        self._http.close()

    # --- helpers bas niveau -----------------------------------------------------------

    def _requete(self, methode: str, chemin: str, **kwargs) -> httpx.Response:
        try:
            reponse = self._http.request(methode, chemin, **kwargs)
        except httpx.HTTPError as exc:
            raise ErreurCalDAV(f"échec réseau CalDAV : {exc}") from exc
        if reponse.status_code >= 400 and methode not in ("PROPFIND", "REPORT", "MKCOL", "MOVE"):
            # 412 attendu (conflit) propagé tel quel pour If-Match ; les autres erreurs
            # deviennent des exceptions.
            if reponse.status_code not in (412, 404):
                raise ErreurCalDAV(
                    f"CalDAV {methode} {chemin} -> {reponse.status_code}",
                    statut=reponse.status_code,
                    href=chemin,
                )
        return reponse

    def _multistatus(self, reponse: httpx.Response) -> list[tuple[str, int, dict[str, str]]]:
        """[(href, status, {getetag, resourcetype...})] depuis un 207."""
        if reponse.status_code not in (207, 200):
            return []
        try:
            racine = ET.fromstring(reponse.content)
        except ET.ParseError:
            return []
        resultat: list[tuple[str, int, dict[str, str]]] = []
        for reponse_element in racine:
            nom = reponse_element.tag.split("}", 1)[-1].lower()
            if nom != "response":
                continue
            href = _href_local(reponse_element, "")
            statut = 200
            proprietes: dict[str, str] = {}
            for bloc in reponse_element:
                bloc_nom = bloc.tag.split("}", 1)[-1].lower()
                if bloc_nom == "status":
                    statut = int((bloc.text or "200").split()[1])
                elif bloc_nom == "propstat":
                    for propbloc in bloc:
                        propnom = propbloc.tag.split("}", 1)[-1].lower()
                        if propnom == "status":
                            continue
                        if propnom == "prop":
                            for prop in propbloc:
                                nomp = prop.tag.split("}", 1)[-1].lower()
                                proprietes[nomp] = _valeur_propriete(prop)
                        elif propnom == "propstat":
                            continue
            resultat.append((href, statut, proprietes))
        return resultat

    def _corps_propfind(self, *proprietes: tuple[str, str]) -> bytes:
        """Corps PROPFIND demandant des propriétés (namespace_local, nom).

        Les deux namespaces (DAV: et CalDAV:) sont TOUJOURS déclarés, même si la
        requête n'utilise que l'un des deux : un préfixe non déclaré rend le corps
        XML invalide et Radicale répond 400.
        """
        demandes = []
        for ns, nom in proprietes:
            prefixe = "D" if ns == DAV else "C"
            demandes.append(f"<{prefixe}:{nom}/>")
        return (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            f'<D:propfind xmlns:D="{DAV}" xmlns:C="{CAL}">'
            f"<D:prop>{''.join(demandes)}</D:prop></D:propfind>"
        ).encode("utf-8")

    # --- découverte ---------------------------------------------------------------------

    def principal(self) -> str:
        """Chemin du principal courant (ex. /juliann/)."""
        corps = self._corps_propfind((DAV, "current-user-principal"))
        reponse = self._requete("PROPFIND", "/", headers=_ENTETES_XML, content=corps)
        for href, statut, proprietes in self._multistatus(reponse):
            if statut == 200 and proprietes.get("current-user-principal"):
                return proprietes["current-user-principal"]
        raise ErreurCalDAV("découverte du principal impossible", statut=reponse.status_code)

    def calendrier_maison(self, principal: str | None = None) -> str:
        principal = principal or self.principal()
        corps = self._corps_propfind((CAL, "calendar-home-set"))
        reponse = self._requete("PROPFIND", principal, headers=_ENTETES_XML, content=corps)
        for href, statut, proprietes in self._multistatus(reponse):
            if statut == 200 and proprietes.get("calendar-home-set"):
                return proprietes["calendar-home-set"]
        raise ErreurCalDAV("découverte du calendar-home-set impossible", statut=reponse.status_code)

    def collections(self) -> list[Collection]:
        """Liste des collections (listes Rappels) de l'utilisateur."""
        maison = self.calendrier_maison()
        corps = self._corps_propfind((DAV, "resourcetype"), (DAV, "displayname"))
        reponse = self._requete("PROPFIND", maison, headers={**_ENTETES_XML, "Depth": "1"}, content=corps)
        resultat: list[Collection] = []
        for href, statut, proprietes in self._multistatus(reponse):
            if statut != 200 or not href:
                continue
            if href == maison:
                continue
            if "calendar" not in proprietes.get("resourcetype", ""):
                continue
            # Radicale renvoie le chemin complet ("juliann/Inbox") comme displayname
            # quand aucune valeur n'est stockée : dans ce cas on retombe sur le segment
            # final du href. Un displayname propre (sans '/') reste prioritaire.
            affiche = (proprietes.get("displayname") or "").strip()
            if not affiche or "/" in affiche:
                nom = decoder_segment(href)
            else:
                nom = affiche
            resultat.append(Collection(href=href if href.endswith("/") else href + "/", nom=nom))
        return resultat

    def creer_collection(self, nom: str) -> str:
        """MKCOL d'une collection calendrier sous la maison. Retourne son href.

        Radicale exige un corps MKCOL déclarant le resourcetype calendar (sans corps,
        type UNKNOWN -> refus "missing rights W"). 405 = déjà existant (idempotent).
        """
        maison = self.calendrier_maison()
        href = maison + encoder_segment(nom) + "/"
        nom_xml = (
            nom.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        corps = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<C:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            "<D:set><D:prop><D:resourcetype>"
            "<D:collection/><C:calendar/>"
            "</D:resourcetype>"
            f"<D:displayname>{nom_xml}</D:displayname>"
            "</D:prop></D:set></C:mkcol>"
        )
        reponse = self._requete("MKCOL", href, headers=_ENTETES_XML, content=corps.encode())
        if reponse.status_code in (201, 405):
            return href
        if reponse.status_code in (403, 409):
            # La collection maison (/user/) peut ne pas exister encore (Radicale la
            # crée au premier accès authentifié) : un PROPFIND la matérialise, puis on
            # réessaie. Les deux requêtes sont sans effet de bord destructif.
            self._requete("PROPFIND", maison, headers={**_ENTETES_XML, "Depth": "0"})
            reponse = self._requete("MKCOL", href, headers=_ENTETES_XML, content=corps.encode())
            if reponse.status_code in (201, 405):
                return href
        raise ErreurCalDAV(
            f"MKCOL {nom} -> {reponse.status_code}", statut=reponse.status_code, href=href
        )

    # --- items --------------------------------------------------------------------------

    def etiquettes(self, collection: Collection) -> dict[str, str]:
        """{href_item: etag} de la collection."""
        corps = self._corps_propfind((DAV, "getetag"))
        reponse = self._requete(
            "PROPFIND", collection.url, headers={**_ENTETES_XML, "Depth": "1"}, content=corps
        )
        etiquettes: dict[str, str] = {}
        for href, statut, proprietes in self._multistatus(reponse):
            if not href or href == collection.url:
                continue
            if statut == 200 and proprietes.get("getetag"):
                etiquettes[href] = proprietes["getetag"]
        return etiquettes

    def lire(self, href: str) -> tuple[bytes, str]:
        """(contenu brut, etag)."""
        reponse = self._requete("GET", href)
        if reponse.status_code == 404:
            raise ErreurCalDAV("élément introuvable", statut=404, href=href)
        if reponse.status_code >= 400:
            raise ErreurCalDAV(
                f"GET {href} -> {reponse.status_code}", statut=reponse.status_code, href=href
            )
        return reponse.content, reponse.headers.get("etag", "")

    def ecrire(self, href: str, contenu: bytes, etag_attendu: str | None = None) -> str:
        """PUT conditionnel (If-Match). Retourne le nouvel etag. 412 -> ErreurCalDAV."""
        entetes = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag_attendu:
            entetes["If-Match"] = etag_attendu
        reponse = self._requete("PUT", href, headers=entetes, content=contenu)
        if reponse.status_code == 412:
            raise ErreurCalDAV("conflit : l'élément a changé depuis la lecture", statut=412, href=href)
        if reponse.status_code == 404 and etag_attendu is None:
            raise ErreurCalDAV("collection parente introuvable", statut=404, href=href)
        if reponse.status_code >= 400:
            raise ErreurCalDAV(
                f"PUT {href} -> {reponse.status_code}", statut=reponse.status_code, href=href
            )
        return reponse.headers.get("etag", "")

    def supprimer(self, href: str, etag_attendu: str | None = None) -> None:
        entetes = {"If-Match": etag_attendu} if etag_attendu else {}
        reponse = self._requete("DELETE", href, headers=entetes)
        if reponse.status_code == 412:
            raise ErreurCalDAV("conflit : l'élément a changé depuis la lecture", statut=412, href=href)
        if reponse.status_code not in (200, 204, 404):
            raise ErreurCalDAV(
                f"DELETE {href} -> {reponse.status_code}", statut=reponse.status_code, href=href
            )

    def deplacer(self, href: str, href_destination: str, etag_attendu: str | None = None) -> str:
        """MOVE conditionnel. Retourne le nouvel etag (vide si non fourni)."""
        entetes = {"Destination": href_destination}
        if etag_attendu:
            entetes["If-Match"] = etag_attendu
        reponse = self._requete("MOVE", href, headers=entetes)
        if reponse.status_code == 412:
            raise ErreurCalDAV("conflit : l'élément a changé depuis la lecture", statut=412, href=href)
        if reponse.status_code not in (200, 201, 204):
            raise ErreurCalDAV(
                f"MOVE {href} -> {reponse.status_code}", statut=reponse.status_code, href=href
            )
        return reponse.headers.get("etag", "")

    def synchroniser(
        self, collection: Collection, jeton: str = ""
    ) -> tuple[str, list[tuple[str, int, str]]]:
        """REPORT sync-collection : (nouveau_jeton, [(href, status, etag)]) avec etag '' si supprimé."""
        corps = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            f'<C:sync-collection xmlns:C="{CAL}" xmlns:D="{DAV}">'
            f"<C:sync-token>{_xml_echapper(jeton)}</C:sync-token>"
            "<C:sync-level>1</C:sync-level>"
            "<C:prop><D:getetag/></C:prop>"
            "</C:sync-collection>"
        ).encode("utf-8")
        reponse = self._requete(
            "REPORT", collection.url, headers={**_ENTETES_XML, "Depth": "1"}, content=corps
        )
        if reponse.status_code not in (200, 207):
            raise ErreurCalDAV(
                f"sync-collection {collection.url} -> {reponse.status_code}",
                statut=reponse.status_code,
                href=collection.url,
            )
        changements: list[tuple[str, int, str]] = []
        nouveau_jeton = ""
        try:
            racine = ET.fromstring(reponse.content)
        except ET.ParseError:
            return "", changements
        for element in racine.iter():
            nom = element.tag.split("}", 1)[-1].lower()
            if nom == "sync-token":
                nouveau_jeton = (element.text or "").strip()
        for href, statut, proprietes in self._multistatus(reponse):
            if not href:
                continue
            changements.append((href, statut, proprietes.get("getetag", "")))
        return nouveau_jeton, changements


def _xml_echapper(valeur: str) -> str:
    return (
        valeur.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
