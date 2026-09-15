"""Nettoyage du HTML de la newsletter.

Le contenu vient de sources tierces (titres et résumés scrapés) qui traversent
l'IA, puis est réaffiché dans l'aperçu de l'interface de validation et envoyé
par email. On le passe par une liste blanche à chaque frontière : la consigne
du system prompt oriente le modèle, elle ne garantit rien, et le relecteur
peut lui aussi coller du HTML arbitraire.
"""
import re

import nh3

ALLOWED_TAGS = {"h3", "h4", "p", "ul", "ol", "li", "a", "strong", "em", "br"}
# `id` sur les titres d'article : c'est la cible des liens du sommaire. Un
# identifiant ne peut rien exécuter, le risque est nul. Les clients mail qui
# ignorent les ancres affichent simplement un lien sans effet, jamais une
# erreur ; Gmail, lui, réécrit identifiants et liens de concert et les fait
# fonctionner.
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "h4": {"id"}, "h3": {"id"}}


def sanitize_html(html: str) -> str:
    return nh3.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)


# Le tiret cadratin est la ponctuation signature des textes générés : le
# modèle en plaçait 114 dans une seule édition. En français, la virgule ou
# les deux-points font le même travail sans le tic.
_ENTRE_BALISES = re.compile(r"(?<=>)\s*[—–]\s*(?=<)")
_APRES_PONCTUATION = re.compile(r"(?<=[,;:.!?])\s*[—–]\s*")
_COLLE = re.compile(r"(?<=[^\s>])[—–](?=[^\s<])")
_RESTANT = re.compile(r"\s*[—–]\s*")


def remove_dashes(texte: str) -> str:
    """Remplace les tirets cadratins par une ponctuation ordinaire.

    Quatre cas, dans cet ordre, du plus spécifique au plus général :

    - entre deux balises, le tiret n'apporte rien et disparaît ;
    - après une ponctuation il est redondant et disparaît aussi ;
    - collé entre deux mots il devient un trait d'union, car il y marque un
      lien et non une énumération (« Paris—Londres » est un trajet) ;
    - partout ailleurs il devient une virgule, ce qui reste grammatical.

    L'ordre compte : sans la règle du tiret collé avant la règle générale,
    « Paris—Londres » deviendrait une liste de deux villes.
    """
    texte = _ENTRE_BALISES.sub("", texte or "")
    texte = _APRES_PONCTUATION.sub(" ", texte)
    texte = _COLLE.sub("-", texte)
    return _RESTANT.sub(", ", texte)
