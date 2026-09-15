import pytest

from src.sanitize import remove_dashes, sanitize_html


class TestRemoveDashes:
    def test_a_spaced_dash_becomes_a_comma(self):
        assert remove_dashes("Le taux à 10 ans — la référence — a bondi.") == (
            "Le taux à 10 ans, la référence, a bondi."
        )

    def test_an_en_dash_is_treated_the_same(self):
        assert remove_dashes("Stage M&A – Lazard") == "Stage M&A, Lazard"

    def test_after_a_comma_the_dash_simply_disappears(self):
        # « , — » donnerait « , , » : le tiret est redondant.
        assert remove_dashes("sa valeur baisse, — c'est le modèle DCF.") == (
            "sa valeur baisse, c'est le modèle DCF."
        )

    @pytest.mark.parametrize("ponctuation", [".", ":", ";", "!", "?"])
    def test_after_any_punctuation(self, ponctuation):
        assert "—" not in remove_dashes(f"Fin{ponctuation} — Suite")

    def test_a_glued_dash_becomes_a_hyphen(self):
        # Un trajet, pas une énumération.
        assert remove_dashes("Paris—Londres en deux heures") == (
            "Paris-Londres en deux heures"
        )

    def test_ordinary_hyphens_are_untouched(self):
        texte = "Le contrôle-de-gestion et le porte-monnaie"
        assert remove_dashes(texte) == texte

    def test_html_tags_survive(self):
        out = remove_dashes("<p>Stage M&A — Lazard</p>")
        assert out == "<p>Stage M&A, Lazard</p>"

    def test_a_dash_between_a_tag_and_a_word_is_not_glued(self):
        # Pas de trait d'union collé à une balise.
        assert "—" not in remove_dashes("<li>Titre</li>—<li>Autre</li>")

    @pytest.mark.parametrize("vide", ["", None])
    def test_empty_input(self, vide):
        assert remove_dashes(vide) == ""

    def test_text_without_dashes_is_unchanged(self):
        texte = "Une phrase parfaitement ordinaire, sans tiret."
        assert remove_dashes(texte) == texte

    def test_combined_with_sanitising(self):
        out = sanitize_html(remove_dashes('<p>A — B</p><script>x</script>'))
        assert out == "<p>A, B</p>"
