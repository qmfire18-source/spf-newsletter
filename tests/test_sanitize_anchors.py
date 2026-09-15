"""Ancres du sommaire — voir ALLOWED_ATTRIBUTES."""
from src.sanitize import sanitize_html


class TestAnchors:
    def test_an_id_on_an_article_title_survives(self):
        # C'est la cible des liens du sommaire.
        assert 'id="a1"' in sanitize_html('<h4 id="a1">Titre</h4>')

    def test_an_id_on_a_rubric_survives(self):
        assert 'id="marches"' in sanitize_html('<h3 id="marches">MARCHÉS</h3>')

    def test_an_internal_link_survives(self):
        assert 'href="#a1"' in sanitize_html('<a href="#a1">Titre</a>')

    def test_dangerous_attributes_are_still_stripped(self):
        sale = '<h4 id="a1" onclick="voler()" style="x">Titre</h4>'
        propre = sanitize_html(sale)
        assert 'id="a1"' in propre
        assert "onclick" not in propre
        assert "style" not in propre

    def test_scripts_are_still_removed(self):
        assert "script" not in sanitize_html('<h4 id="a">t</h4><script>x</script>')

    def test_an_id_elsewhere_is_dropped(self):
        # Seuls les titres en ont besoin : ailleurs, c'est du bruit.
        assert "id=" not in sanitize_html('<p id="x">Texte</p>')

    def test_a_javascript_link_is_refused(self):
        assert "javascript" not in sanitize_html('<a href="javascript:x()">t</a>')
