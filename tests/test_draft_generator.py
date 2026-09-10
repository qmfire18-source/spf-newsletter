import json
from types import SimpleNamespace

import pytest

from src.ai import draft_generator


def fake_response(text=None, stop_reason="end_turn", stop_details=None):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, stop_details=stop_details
    )


class FakeClient:
    """Renvoie les réponses fournies dans l'ordre et enregistre les appels."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


@pytest.fixture
def use_client(monkeypatch):
    def _install(*responses):
        client = FakeClient(*responses)
        monkeypatch.setattr(draft_generator, "_get_client", lambda: client)
        return client

    return _install


VALID_JSON = json.dumps({"news_html": "<p>Actu</p>", "stages_html": "<p>Stage</p>"})


class TestGenerateDraft:
    def test_returns_parsed_sections(self, use_client):
        use_client(fake_response(VALID_JSON))
        assert draft_generator.generate_draft([], []) == {
            "news_html": "<p>Actu</p>",
            "stages_html": "<p>Stage</p>",
        }

    def test_retries_once_on_unparsable_response(self, use_client):
        client = use_client(fake_response("pas du json"), fake_response(VALID_JSON))
        result = draft_generator.generate_draft([], [])
        assert len(client.calls) == 2
        assert result["news_html"] == "<p>Actu</p>"

    def test_raises_after_retry_exhausted(self, use_client):
        client = use_client(fake_response("nope"), fake_response("toujours pas"))
        with pytest.raises(draft_generator.DraftGenerationError, match="non parsable"):
            draft_generator.generate_draft([], [])
        assert len(client.calls) == draft_generator.MAX_ATTEMPTS

    def test_raises_when_a_required_key_is_missing(self, use_client):
        partial = json.dumps({"news_html": "<p>Actu</p>"})
        use_client(fake_response(partial), fake_response(partial))
        with pytest.raises(draft_generator.DraftGenerationError):
            draft_generator.generate_draft([], [])

    def test_refusal_is_not_retried(self, use_client):
        client = use_client(fake_response(stop_reason="refusal", stop_details="cyber"))
        with pytest.raises(draft_generator.DraftGenerationError, match="refusée"):
            draft_generator.generate_draft([], [])
        assert len(client.calls) == 1

    def test_truncated_response_is_not_retried(self, use_client):
        client = use_client(fake_response('{"news_html"', stop_reason="max_tokens"))
        with pytest.raises(draft_generator.DraftGenerationError, match="tronquée"):
            draft_generator.generate_draft([], [])
        assert len(client.calls) == 1


class TestRequestShape:
    def test_requests_a_schema_constrained_json_object(self, use_client):
        client = use_client(fake_response(VALID_JSON))
        draft_generator.generate_draft([], [])
        output_format = client.calls[0]["output_config"]["format"]
        assert output_format["type"] == "json_schema"
        assert output_format["schema"]["required"] == ["news_html", "stages_html"]

    def test_sends_the_editorial_charter_as_system_prompt(self, use_client):
        client = use_client(fake_response(VALID_JSON))
        draft_generator.generate_draft([], [])
        assert "Sciences Po Finance" in client.calls[0]["system"]

    def test_passes_news_and_stages_into_the_prompt(self, use_client):
        client = use_client(fake_response(VALID_JSON))
        draft_generator.generate_draft(
            [{"title": "La BCE relève ses taux"}],
            [{"title": "Stage M&A", "company": "Lazard"}],
        )
        prompt = client.calls[0]["messages"][0]["content"]
        assert "La BCE relève ses taux" in prompt
        assert "Lazard" in prompt


class TestBuildUserPrompt:
    def test_keeps_accents_readable(self):
        prompt = draft_generator._build_user_prompt([{"t": "marché"}], [])
        assert "marché" in prompt
