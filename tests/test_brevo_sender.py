from datetime import date

import pytest
import requests

from src.email import brevo_sender


class FakeResponse:
    def __init__(self, status_code=201, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"x" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("pas de JSON")
        return self._payload


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(brevo_sender, "BREVO_API_KEY", "cle-test")
    monkeypatch.setattr(brevo_sender, "BREVO_LIST_ID", "7")
    monkeypatch.setattr(brevo_sender, "BREVO_SENDER_EMAIL", "news@spf.fr")
    monkeypatch.setattr(brevo_sender, "BREVO_SENDER_NAME", "Sciences Po Finance")


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_post(url, json=None, headers=None, timeout=None):
        recorded.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if url.endswith("/emailCampaigns"):
            return FakeResponse(201, {"id": 4242})
        return FakeResponse(204)

    monkeypatch.setattr(brevo_sender.requests, "post", fake_post)
    return recorded


class TestSendCampaign:
    def test_creates_then_triggers_the_campaign(self, configured, calls):
        assert brevo_sender.send_campaign("Sujet", "<p>hello</p>") == "4242"
        assert calls[0]["url"].endswith("/emailCampaigns")
        assert calls[1]["url"].endswith("/emailCampaigns/4242/sendNow")

    def test_targets_the_configured_list(self, configured, calls):
        brevo_sender.send_campaign("Sujet", "<p>hello</p>")
        assert calls[0]["json"]["recipients"] == {"listIds": [7]}

    def test_explicit_list_id_wins(self, configured, calls):
        brevo_sender.send_campaign("Sujet", "<p>x</p>", list_id=99)
        assert calls[0]["json"]["recipients"] == {"listIds": [99]}

    def test_every_request_has_a_timeout(self, configured, calls):
        brevo_sender.send_campaign("Sujet", "<p>x</p>")
        assert all(c["timeout"] == brevo_sender.REQUEST_TIMEOUT_SECONDS for c in calls)

    def test_campaign_name_is_unique_per_send(self, configured, calls):
        brevo_sender.send_campaign("Sujet", "<p>x</p>")
        # Le sujet reste propre ; l'horodatage ne va que dans le nom interne.
        assert calls[0]["json"]["subject"] == "Sujet"
        assert calls[0]["json"]["name"].startswith("Sujet (")

    def test_uses_the_configured_sender(self, configured, calls):
        brevo_sender.send_campaign("Sujet", "<p>x</p>")
        assert calls[0]["json"]["sender"] == {
            "name": "Sciences Po Finance", "email": "news@spf.fr"
        }


class TestConfigurationErrors:
    def test_missing_api_key(self, configured, monkeypatch):
        monkeypatch.setattr(brevo_sender, "BREVO_API_KEY", "")
        with pytest.raises(brevo_sender.BrevoError, match="BREVO_API_KEY"):
            brevo_sender.send_campaign("s", "<p>x</p>")

    def test_missing_sender_email(self, configured, monkeypatch):
        monkeypatch.setattr(brevo_sender, "BREVO_SENDER_EMAIL", "")
        with pytest.raises(brevo_sender.BrevoError, match="BREVO_SENDER_EMAIL"):
            brevo_sender.send_campaign("s", "<p>x</p>")

    def test_missing_list_id(self, configured, monkeypatch):
        monkeypatch.setattr(brevo_sender, "BREVO_LIST_ID", None)
        with pytest.raises(brevo_sender.BrevoError, match="BREVO_LIST_ID"):
            brevo_sender.send_campaign("s", "<p>x</p>")

    def test_non_numeric_list_id(self, configured, monkeypatch):
        monkeypatch.setattr(brevo_sender, "BREVO_LIST_ID", "ma-liste")
        with pytest.raises(brevo_sender.BrevoError, match="invalide"):
            brevo_sender.send_campaign("s", "<p>x</p>")


class TestApiErrors:
    def test_http_error_surfaces_brevo_message(self, configured, monkeypatch):
        monkeypatch.setattr(
            brevo_sender.requests, "post",
            lambda *a, **k: FakeResponse(400, text='{"message":"sender unknown"}'),
        )
        with pytest.raises(brevo_sender.BrevoError, match="sender unknown"):
            brevo_sender.send_campaign("s", "<p>x</p>")

    def test_network_failure_is_wrapped(self, configured, monkeypatch):
        def boom(*a, **k):
            raise requests.ConnectionError("réseau coupé")

        monkeypatch.setattr(brevo_sender.requests, "post", boom)
        with pytest.raises(brevo_sender.BrevoError, match="injoignable"):
            brevo_sender.send_campaign("s", "<p>x</p>")

    def test_response_without_campaign_id(self, configured, monkeypatch):
        monkeypatch.setattr(
            brevo_sender.requests, "post", lambda *a, **k: FakeResponse(201, {})
        )
        with pytest.raises(brevo_sender.BrevoError, match="identifiant"):
            brevo_sender.send_campaign("s", "<p>x</p>")


class TestRenderNewsletter:
    def test_produces_a_full_document(self):
        out = brevo_sender.render_newsletter("<p>A</p>", "<p>S</p>", date(2026, 9, 7))
        assert out.startswith("<!DOCTYPE html>")
        # Le contenu porte désormais ses styles en ligne : on vérifie le
        # texte, pas le balisage exact.
        assert ">A</p>" in out
        assert ">S</p>" in out

    def test_shows_the_week(self):
        out = brevo_sender.render_newsletter("", "", date(2026, 9, 7))
        assert "2026-09-07" in out

    def test_styles_are_inline_for_mail_clients(self):
        out = brevo_sender.render_newsletter("", "", date(2026, 9, 7))
        assert "<link" not in out
        assert "style=" in out


class TestNewsletterBranding:
    def rendu(self):
        return brevo_sender.render_newsletter("<p>A</p>", "<p>S</p>", date(2026, 9, 14))

    def test_carries_the_association_logo(self):
        # Un mail ne peut pas pointer vers un fichier local.
        out = self.rendu()
        assert brevo_sender.LOGO_URL in out
        assert out.count("<img") == 1
        assert 'alt="Sciences Po Finance"' in out

    def test_uses_the_association_navy(self):
        assert brevo_sender.MARINE in self.rendu()

    def test_week_is_written_out_in_french(self):
        # "2026-09-14" dans un en-tête de mail fait brut.
        assert "Semaine du 14 septembre 2026" in self.rendu()

    def test_week_falls_back_when_not_a_date(self):
        out = brevo_sender.render_newsletter("", "", "2026-W38")
        assert "2026-W38" in out

    def test_has_a_preheader(self):
        out = self.rendu()
        assert "preheader" in out
        assert "stages de la semaine" in out

    def test_is_responsive_on_phones(self):
        out = self.rendu()
        assert "max-width:620px" in out
        assert 'name="viewport"' in out

    def test_width_is_capped_for_mail_clients(self):
        assert "max-width:600px" in self.rendu()

    def test_no_external_stylesheet(self):
        # Les clients mail ignorent les feuilles externes.
        out = self.rendu()
        assert "<link" not in out
        assert "@import" not in out

    def test_mentions_the_unsubscribe_link(self):
        assert "désabonnement" in self.rendu().lower()

    def test_escapes_the_title(self):
        out = brevo_sender.render_newsletter("", "", "<script>x</script>")
        assert "<script>x</script>" not in out.split("<body")[0]


class TestBrandColour:
    def test_uses_the_navy_sampled_from_the_crest(self):
        # #183050 est la teinte dominante de logo.jpeg, pas une valeur choisie.
        assert brevo_sender.MARINE == "#183050"

    def test_the_navy_appears_in_the_rendered_email(self):
        out = brevo_sender.render_newsletter("", "", date(2026, 9, 14))
        assert "#183050" in out


class TestWeekBandOnPhones:
    def test_the_band_shrinks_on_small_screens(self):
        # Une règle la portait de 14 à 20px, ce qui la coupait en deux lignes.
        out = brevo_sender.render_newsletter("", "", date(2026, 9, 14))
        bloc = out[out.index("max-width:620px"):out.index("</style>")]
        assert ".titre" in bloc
        assert "font-size:13px" in bloc
        assert "font-size:20px" not in bloc

    def test_the_week_is_written_on_one_line(self):
        out = brevo_sender.render_newsletter("", "", date(2026, 9, 14))
        assert "Semaine du 14 septembre 2026" in out


class TestSectionHierarchy:
    def rendu(self):
        return brevo_sender.render_newsletter(
            "<h3>MARCHÉS</h3><h4>Un titre</h4><p>Texte</p>", "<p>S</p>",
            date(2026, 9, 14),
        )

    def test_the_rubric_is_a_label_not_a_title(self):
        # h3 nomme la rubrique, h4 l'article : sans cette hiérarchie, tous les
        # titres avaient le même poids et l'on ne savait plus où commençait quoi.
        bloc = self.rendu()
        regle = bloc[bloc.index(".contenu h3"):bloc.index(".contenu h4")]
        assert "text-transform:uppercase" in regle
        assert "border-top" in regle

    def test_the_article_title_keeps_the_serif(self):
        bloc = self.rendu()
        regle = bloc[bloc.index(".contenu h4"):bloc.index("@media")]
        assert "Georgia" in regle

    def test_the_first_rubric_has_no_rule_above(self):
        assert ".contenu h3:first-child" in self.rendu()

    def test_the_label_colour_reads_on_white(self):
        # L'or clair de l'en-tête ne passe pas le contraste sur fond blanc.
        assert brevo_sender.OR_FONCE == "#8A6B22"


class TestInlineStyles:
    def test_every_content_tag_carries_its_own_style(self):
        # Gmail sur mobile supprime le bloc <style> : sans styles en ligne,
        # l'étiquette dorée redevenait un titre noir ordinaire.
        out = brevo_sender.inline_styles(
            '<h3>MARCHÉS</h3><h4>Titre</h4><p>Texte</p><ul><li>x</li></ul>'
        )
        for balise in ("h3", "h4", "p", "ul", "li"):
            assert f"<{balise} style=" in out or f'<{balise} style=' in out

    def test_existing_attributes_survive(self):
        out = brevo_sender.inline_styles('<h4 id="a1">Titre</h4>')
        assert 'id="a1"' in out and "style=" in out

    def test_the_rubric_is_gold_and_uppercase(self):
        out = brevo_sender.inline_styles("<h3>MARCHÉS</h3>")
        assert brevo_sender.OR_FONCE in out
        assert "text-transform:uppercase" in out

    def test_untouched_tags_are_left_alone(self):
        assert brevo_sender.inline_styles("<strong>x</strong>") == "<strong>x</strong>"

    def test_empty_fragment(self):
        assert brevo_sender.inline_styles("") == ""
        assert brevo_sender.inline_styles(None) == ""

    def test_the_first_rubric_has_no_rule_above_it(self):
        # Rien ne la précède : un filet y serait une barre flottante.
        out = brevo_sender.render_newsletter(
            "<h3>MARCHÉS</h3><p>a</p><h3>MACRO</h3><p>b</p>", "", date(2026, 9, 14)
        )
        premier = out[out.index("<h3"):out.index("</h3>")]
        assert "border-top" not in premier
        assert out.count(f"border-top:1px solid {brevo_sender.FILET}") >= 1

    def test_styles_come_from_us_not_from_the_model(self):
        # Le nettoyage retire tout style reçu ; ceux-ci sont ajoutés ensuite.
        from src.sanitize import sanitize_html
        propre = sanitize_html('<h3 style="color:red">MARCHÉS</h3>')
        assert "red" not in propre
        assert brevo_sender.OR_FONCE in brevo_sender.inline_styles(propre)


class TestPreheaderStaysHidden:
    def test_it_is_hidden_without_the_style_block(self):
        # Un client qui supprime <style> affichait le pré-en-tête en clair,
        # juste au-dessus de l'en-tête.
        import re
        out = brevo_sender.render_newsletter("<p>A</p>", "", date(2026, 9, 14))
        sans_style = re.sub(r"<style>.*?</style>", "", out, flags=re.S)
        bloc = sans_style[sans_style.index('class="preheader"'):]
        bloc = bloc[:bloc.index("</div>")]
        assert "display:none" in bloc
        assert "max-height:0" in bloc
