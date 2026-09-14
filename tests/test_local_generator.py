import json
import subprocess

import pytest

from src.ai.draft_generator import DraftGenerationError
from src.ai import local_generator

VALIDE = {"news_html": "<p>actu</p>", "stages_html": "<p>stage</p>"}


class TestParseCliOutput:
    def test_plain_json(self):
        assert local_generator.parse_cli_output(json.dumps(VALIDE)) == VALIDE

    def test_json_in_a_markdown_fence(self):
        raw = f"Voici le brouillon :\n```json\n{json.dumps(VALIDE)}\n```"
        assert local_generator.parse_cli_output(raw) == VALIDE

    def test_json_surrounded_by_prose(self):
        assert local_generator.parse_cli_output(f"Bien sûr !\n{json.dumps(VALIDE)}\nVoilà.") == VALIDE

    def test_html_is_sanitised(self):
        raw = json.dumps({
            "news_html": '<p onclick="x">a</p><script>alert(1)</script>',
            "stages_html": "<p>b</p>",
        })
        result = local_generator.parse_cli_output(raw)
        assert "script" not in result["news_html"]
        assert "onclick" not in result["news_html"]

    @pytest.mark.parametrize("raw", ["", "Je ne peux pas répondre.", "   "])
    def test_rejects_a_response_without_json(self, raw):
        with pytest.raises(DraftGenerationError, match="Aucun objet JSON"):
            local_generator.parse_cli_output(raw)

    def test_rejects_malformed_json(self):
        with pytest.raises(DraftGenerationError, match="JSON invalide"):
            local_generator.parse_cli_output('{"news_html": "a", "stages_html":}')

    @pytest.mark.parametrize("manquante", ["news_html", "stages_html"])
    def test_rejects_a_partial_response(self, manquante):
        payload = {k: v for k, v in VALIDE.items() if k != manquante}
        with pytest.raises(DraftGenerationError, match=manquante):
            local_generator.parse_cli_output(json.dumps(payload))


class TestFindCli:
    def test_prefers_the_binary_on_the_path(self, monkeypatch):
        monkeypatch.setattr(local_generator.shutil, "which", lambda name: "/usr/bin/claude")
        assert local_generator.find_cli() == "/usr/bin/claude"

    def test_falls_back_to_the_newest_bundled_extension(self, monkeypatch):
        monkeypatch.setattr(local_generator.shutil, "which", lambda name: None)
        monkeypatch.setattr(local_generator.glob, "glob", lambda pattern: [
            "/e/anthropic.claude-code-2.1.9-darwin/resources/native-binary/claude",
            "/e/anthropic.claude-code-2.1.10-darwin/resources/native-binary/claude",
            "/e/anthropic.claude-code-2.0.99-darwin/resources/native-binary/claude",
        ])
        # 2.1.10 > 2.1.9 : un tri alphabétique choisirait le mauvais.
        assert "2.1.10" in local_generator.find_cli()

    def test_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(local_generator.shutil, "which", lambda name: None)
        monkeypatch.setattr(local_generator.glob, "glob", lambda pattern: [])
        assert local_generator.find_cli() is None


class TestGenerateDraftLocally:
    def test_explains_how_to_proceed_when_the_cli_is_missing(self, monkeypatch):
        monkeypatch.setattr(local_generator, "find_cli", lambda: None)
        with pytest.raises(DraftGenerationError, match="introuvable"):
            local_generator.generate_draft_locally([], [])

    def test_passes_the_prompt_on_stdin_and_returns_the_draft(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["input"] = kwargs["input"]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(VALIDE), "")

        monkeypatch.setattr(local_generator, "find_cli", lambda: "/bin/claude")
        monkeypatch.setattr(local_generator.subprocess, "run", fake_run)

        assert local_generator.generate_draft_locally(
            [{"title": "BCE", "url": "https://x", "full_text": "texte"}], []
        ) == VALIDE
        assert seen["cmd"][:2] == ["/bin/claude", "-p"]
        assert "DÉVELOPPABLES" in seen["input"]

    def test_runs_the_cli_as_a_generator_not_an_agent(self, monkeypatch):
        # Laissé agentique, le CLI écrit un fichier au lieu de répondre.
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, json.dumps(VALIDE), "")

        monkeypatch.setattr(local_generator, "find_cli", lambda: "/bin/claude")
        monkeypatch.setattr(local_generator.subprocess, "run", fake_run)
        local_generator.generate_draft_locally([], [])

        assert "--system-prompt" in seen["cmd"]
        charte = seen["cmd"][seen["cmd"].index("--system-prompt") + 1]
        assert "N'écris aucun fichier" in charte

        assert "--disallowed-tools" in seen["cmd"]
        interdits = seen["cmd"][seen["cmd"].index("--disallowed-tools") + 1]
        for outil in ("Write", "Bash", "Edit"):
            assert outil in interdits

    def test_reports_a_failing_cli(self, monkeypatch):
        monkeypatch.setattr(local_generator, "find_cli", lambda: "/bin/claude")
        monkeypatch.setattr(
            local_generator.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "non authentifié"),
        )
        with pytest.raises(DraftGenerationError, match="non authentifié"):
            local_generator.generate_draft_locally([], [])

    def test_reports_a_timeout(self, monkeypatch):
        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, local_generator.TIMEOUT_SECONDS)

        monkeypatch.setattr(local_generator, "find_cli", lambda: "/bin/claude")
        monkeypatch.setattr(local_generator.subprocess, "run", timeout)
        with pytest.raises(DraftGenerationError, match="n'a pas répondu"):
            local_generator.generate_draft_locally([], [])
