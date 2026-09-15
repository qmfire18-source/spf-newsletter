"""Classement des offres de stage par segment de la finance.

La section stages alignait les offres sans ordre : un audit provincial
voisinait un M&A parisien. Les regrouper par métier permet au lecteur de
sauter directement à ce qui l'intéresse — et rend visible ce que la semaine
offre vraiment dans chaque segment.

Le nom de l'employeur tranche en premier : c'est le signal le plus sûr.
À défaut, l'intitulé du poste est lu. Une offre non classée va dans
« Autres opportunités » plutôt que d'être forcée dans un segment faux.
"""
import unicodedata

BANQUE = "Banque de financement et d'investissement"
CONSEIL_MA = "Banque d'affaires et M&A"
PRIVATE_EQUITY = "Private equity et investissement"
AUDIT = "Audit et transaction services"
GESTION = "Gestion d'actifs"
FINTECH = "Fintech"
ASSURANCE = "Assurance et actuariat"
CORPORATE = "Corporate finance en entreprise"
INSTITUTIONS = "Institutions et régulateurs"
AUTRES = "Autres opportunités"

# Ordre d'apparition dans la newsletter : du plus recherché par l'asso au
# moins. « Autres » ferme la marche.
SECTOR_ORDER = (
    CONSEIL_MA, BANQUE, PRIVATE_EQUITY, GESTION, AUDIT,
    ASSURANCE, CORPORATE, FINTECH, INSTITUTIONS, AUTRES,
)

# Fragments cherchés dans le nom ou l'URL de l'employeur, normalisés.
EMPLOYER_SECTORS = {
    BANQUE: (
        "bnp paribas", "societe generale", "credit agricole", "ca-cib",
        "credit agricole cib", "credit mutuel", "natixis", "bpce",
        "la banque postale", "banque postale", "hsbc", "barclays",
        "deutsche bank", "jp morgan", "goldman sachs", "morgan stanley",
        "citi", "bank of america", "oddo bhf", "degroof", "banque bcp",
    ),
    CONSEIL_MA: (
        "rothschild", "lazard", "clipperton", "messier", "dc advisory",
        "cambon", "evercore", "houlihan lokey", "pjt", "centerview",
        "alantra", "natixis partners", "bryan garnier", "edmond de rothschild",
        "transaction r", "sycomore corporate", "degroof petercam finance",
    ),
    PRIVATE_EQUITY: (
        "ardian", "naxicap", "creadev", "eurazeo", "pai partners", "tikehau",
        "antin", "wendel", "bpifrance", "apax", "astorg", "ekkio", "idinvest",
        "seventure", "partech", "eurazeo", "montefiore", "activa capital",
        "ofi invest", "andera", "latour capital",
    ),
    GESTION: (
        "amundi", "axa im", "axa investment", "carmignac", "comgest",
        "la francaise", "lazard freres gestion", "rothschild asset",
        "sycomore asset", "dnca", "lyxor", "candriam", "groupama am",
    ),
    AUDIT: (
        "deloitte", "ey", "ernst", "kpmg", "pwc", "pricewaterhouse", "bdo",
        "rsm", "in extenso", "pkf", "grant thornton", "mazars",
        "forvis", "eight advisory", "accuracy", "alvarez", "advolis",
        "crowe", "baker tilly", "exco",
    ),
    FINTECH: (
        "ibanfirst", "qonto", "swan", "pennylane", "spendesk", "ledger",
        "lydia", "younited", "alan", "payfit", "doctolib", "shine",
        "memo bank", "revolut", "n26",
    ),
    ASSURANCE: (
        "axa", "allianz", "generali", "coface", "scor", "covea", "groupama",
        "maif", "macif", "cnp assurances", "matmut", "aviva", "swiss life",
    ),
    INSTITUTIONS: (
        "euronext", "amf", "autorite des marches", "banque de france",
        "bce", "european central bank", "acpr", "caisse des depots",
        "tresor",
    ),
}

# À défaut d'employeur reconnu, l'intitulé du poste renseigne. Les entrées
# les plus spécifiques passent en premier : « private equity » avant
# « invest », sinon un stage en private equity tomberait dans le mauvais tas.
TITLE_SECTORS = (
    (CONSEIL_MA, ("m&a", "m a ", "fusion", "acquisition", "investment banking",
                  "banque d affaires", "corporate finance", "ecm", "dcm",
                  "leveraged finance", "lbo")),
    (PRIVATE_EQUITY, ("private equity", "capital investissement", "venture capital",
                      "capital risque", "investissement", "participations")),
    (GESTION, ("gestion d actifs", "asset management", "gerant", "gestion de portefeuille",
               "multi strategie", "allocation")),
    (AUDIT, ("audit", "transaction services", "commissariat", "due diligence",
             "evaluation financiere")),
    (BANQUE, ("banquier", "banque privee", "credit", "financement de projet",
              "salle des marches", "trading", "structuration")),
    (CORPORATE, ("controle de gestion", "controleur", "tresorerie", "consolidation",
                 "analyste financier", "fp&a", "pricing")),
    (ASSURANCE, ("actuariat", "actuarielles", "actuaire", "assurance",
                 "reassurance", "souscription")),
    (INSTITUTIONS, ("regulation", "conformite", "compliance", "risque",
                    "supervision")),
)


def classify(offer: dict) -> str:
    """Segment d'une offre : par employeur d'abord, par intitulé ensuite."""
    employeur = _normalise(f"{offer.get('company') or ''} {offer.get('url') or ''}")
    for secteur, marqueurs in EMPLOYER_SECTORS.items():
        if any(m in employeur for m in marqueurs):
            return secteur

    titre = _normalise(offer.get("title") or "")
    for secteur, marqueurs in TITLE_SECTORS:
        if any(m in titre for m in marqueurs):
            return secteur

    return AUTRES


def group_by_sector(offers: list[dict]) -> list[dict]:
    """Retourne [{"secteur": ..., "offres": [...]}, ...] dans l'ordre éditorial.

    Les segments vides ne figurent pas : une rubrique sans offre n'apprend
    rien au lecteur.
    """
    paniers: dict[str, list[dict]] = {}
    for offer in offers:
        paniers.setdefault(classify(offer), []).append(offer)

    return [
        {"secteur": secteur, "offres": paniers[secteur]}
        for secteur in SECTOR_ORDER
        if paniers.get(secteur)
    ]


def _normalise(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte.lower())
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return "".join(c if c.isalnum() or c == "&" else " " for c in sans_accent)
