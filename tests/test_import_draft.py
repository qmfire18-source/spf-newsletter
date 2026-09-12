import json

import pytest

from scripts.import_draft import extract_draft

VALIDE = {"news_html": "<p>actu</p>", "stages_html": "<p>stage</p>"}


class TestExtractDraft:
    def test_plain_json(self):
        assert extract_draft(json.dumps(VALIDE)) == VALIDE

    def test_json_inside_a_markdown_fence(self):
        raw = f"Voici le brouillon :\n```json\n{json.dumps(VALIDE)}\n```"
        assert extract_draft(raw) == VALIDE

    def test_fence_without_a_language(self):
        raw = f"```\n{json.dumps(VALIDE)}\n```"
        assert extract_draft(raw) == VALIDE

    def test_json_surrounded_by_prose(self):
        raw = f"Bien sûr !\n\n{json.dumps(VALIDE)}\n\nDis-moi si tu veux des ajustements."
        assert extract_draft(raw) == VALIDE

    def test_html_containing_braces_is_preserved(self):
        payload = {"news_html": "<p>Résultat : {x} et }y{</p>", "stages_html": "<p>s</p>"}
        assert extract_draft(json.dumps(payload)) == payload

    def test_rejects_a_response_without_json(self):
        with pytest.raises(ValueError, match="aucun objet JSON"):
            extract_draft("Je ne peux pas répondre à cette demande.")

    @pytest.mark.parametrize("manquante", ["news_html", "stages_html"])
    def test_rejects_a_partial_response(self, manquante):
        payload = {k: v for k, v in VALIDE.items() if k != manquante}
        with pytest.raises(ValueError, match=manquante):
            extract_draft(json.dumps(payload))

    def test_rejects_malformed_json(self):
        with pytest.raises(json.JSONDecodeError):
            extract_draft('{"news_html": "a", "stages_html":}')

    def test_empty_response(self):
        with pytest.raises(ValueError):
            extract_draft("")
