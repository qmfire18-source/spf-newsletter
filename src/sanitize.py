"""Nettoyage du HTML de la newsletter.

Le contenu vient de sources tierces (titres et résumés scrapés) qui traversent
l'IA, puis est réaffiché dans l'aperçu de l'interface de validation et envoyé
par email. On le passe par une liste blanche à chaque frontière : la consigne
du system prompt oriente le modèle, elle ne garantit rien, et le relecteur
peut lui aussi coller du HTML arbitraire.
"""
import nh3

ALLOWED_TAGS = {"h3", "h4", "p", "ul", "ol", "li", "a", "strong", "em", "br"}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}


def sanitize_html(html: str) -> str:
    return nh3.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
