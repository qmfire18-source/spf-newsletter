import pytest
import requests

from src.scraper import article_fetcher


def page(paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<html><body><article>{body}</article></body></html>"


LONG = (
    "L'Insee a revu sa prévision de croissance à 0,4 % pour 2026, contre 0,7 % "
    "attendus jusque-là, soit trois fois moins que le Royaume-Uni ou l'Espagne."
)
# Un article plausible : au-delà du seuil qui écarte les pages rendues en JS.
ARTICLE = " ".join([LONG] * 5)


class TestExtractText:
    def test_keeps_substantial_paragraphs(self):
        assert LONG in article_fetcher.extract_text(page([LONG]))

    def test_drops_short_paragraphs(self):
        text = article_fetcher.extract_text(page(["Publicité", "Menu", LONG]))
        assert text == LONG

    @pytest.mark.parametrize("noise", [
        "Nous utilisons des cookies pour mesurer l'audience de notre site web.",
        "Vous pouvez changer d'avis à tout moment dans les réglages de votre navigateur.",
        "Cet article est réservé aux abonnés, connectez-vous pour lire la suite.",
        "Temps de lecture : 3 min. Article rédigé par la rédaction du service.",
    ])
    def test_drops_boilerplate(self, noise):
        assert noise not in article_fetcher.extract_text(page([noise, LONG]))

    def test_ignores_scripts_and_navigation(self):
        html = f"<script><p>{LONG}</p></script><nav><p>{LONG}</p></nav>"
        assert article_fetcher.extract_text(html) == ""

    def test_empty_page(self):
        assert article_fetcher.extract_text("") == ""


class TestAggregatorDetection:
    @pytest.mark.parametrize("url", [
        "https://news.google.com/rss/articles/CBMiabcdef?oc=5",
        "https://consent.google.com/m?continue=x",
    ])
    def test_aggregators_are_refused_without_a_request(self, url, monkeypatch):
        # Aucune requête ne doit partir : ces URL ne mènent pas à un article.
        def explode(*a, **k):
            raise AssertionError("aucune requête ne devrait être émise")
        monkeypatch.setattr(article_fetcher.requests, "get", explode)
        assert article_fetcher.fetch_article_text(url) is None

    def test_a_publisher_url_is_not_an_aggregator(self):
        assert not article_fetcher._is_aggregator("https://www.challenges.fr/a.html")


class FakeResponse:
    def __init__(self, text="", status_code=200, url="https://presse.fr/a"):
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Sert un robots.txt puis une page, en enregistrant les URL demandées."""

    def __init__(self, robots="", page_html="", page_url="https://presse.fr/a"):
        self.robots = robots
        self.page_html = page_html
        self.page_url = page_url
        self.seen = []

    def get(self, url, **kwargs):
        self.seen.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(self.robots)
        return FakeResponse(self.page_html, url=self.page_url)


@pytest.fixture(autouse=True)
def clear_robots_cache():
    article_fetcher._robots_cache.clear()
    yield
    article_fetcher._robots_cache.clear()


class TestRobotsCompliance:
    def test_refuses_a_path_disallowed_by_robots(self):
        session = FakeSession(robots="User-agent: *\nDisallow: /economie/")
        assert article_fetcher.fetch_article_text(
            "https://presse.fr/economie/a.html", session
        ) is None

    def test_reads_an_allowed_path(self):
        session = FakeSession(
            robots="User-agent: *\nDisallow: /prive/", page_html=page([ARTICLE])
        )
        assert article_fetcher.fetch_article_text("https://presse.fr/a", session)

    def test_abstains_when_robots_is_unreachable(self):
        class Broken(FakeSession):
            def get(self, url, **kwargs):
                raise requests.ConnectionError("injoignable")

        assert article_fetcher.fetch_article_text("https://presse.fr/a", Broken()) is None

    def test_missing_robots_means_everything_is_allowed(self):
        class NoRobots(FakeSession):
            def get(self, url, **kwargs):
                if url.endswith("/robots.txt"):
                    return FakeResponse("", status_code=404)
                return FakeResponse(page([ARTICLE]), url=url)

        assert article_fetcher.fetch_article_text("https://presse.fr/a", NoRobots())

    def test_robots_is_fetched_once_per_domain(self):
        session = FakeSession(robots="User-agent: *\n", page_html=page([ARTICLE]))
        article_fetcher.fetch_article_text("https://presse.fr/a", session)
        article_fetcher.fetch_article_text("https://presse.fr/b", session)
        assert session.seen.count("https://presse.fr/robots.txt") == 1


class TestJavascriptWalls:
    def test_a_page_too_thin_is_refused(self):
        session = FakeSession(robots="", page_html="<p>Please enable JavaScript.</p>")
        assert article_fetcher.fetch_article_text("https://presse.fr/a", session) is None

    def test_a_consent_redirect_is_refused(self):
        session = FakeSession(
            robots="", page_html=page([ARTICLE]),
            page_url="https://consent.google.com/m?continue=x",
        )
        assert article_fetcher.fetch_article_text("https://presse.fr/a", session) is None

    def test_text_is_truncated(self, monkeypatch):
        monkeypatch.setattr(article_fetcher, "MAX_TEXT_CHARS", 100)
        session = FakeSession(robots="", page_html=page([LONG * 20]))
        assert len(article_fetcher.fetch_article_text("https://presse.fr/a", session)) == 100


class TestEnrich:
    def test_stops_once_the_limit_is_reached(self, monkeypatch):
        monkeypatch.setattr(article_fetcher, "REQUEST_DELAY_SECONDS", 0)
        monkeypatch.setattr(article_fetcher, "fetch_article_text",
                            lambda url, session=None: "texte")
        items = [{"url": f"https://presse.fr/{i}"} for i in range(5)]
        article_fetcher.enrich_with_article_text(items, limit=2)
        assert sum(1 for i in items if "full_text" in i) == 2

    def test_aggregators_do_not_consume_an_attempt(self, monkeypatch):
        monkeypatch.setattr(article_fetcher, "REQUEST_DELAY_SECONDS", 0)
        tried = []

        def fake(url, session=None):
            tried.append(url)
            return "texte"

        monkeypatch.setattr(article_fetcher, "fetch_article_text", fake)
        items = [{"url": "https://news.google.com/rss/articles/x"}] * 4
        items.append({"url": "https://presse.fr/vrai"})
        article_fetcher.enrich_with_article_text(items, limit=1, max_attempts=2)
        assert tried == ["https://presse.fr/vrai"]

    def test_unreadable_items_keep_no_full_text(self, monkeypatch):
        monkeypatch.setattr(article_fetcher, "REQUEST_DELAY_SECONDS", 0)
        monkeypatch.setattr(article_fetcher, "fetch_article_text",
                            lambda url, session=None: None)
        items = [{"url": "https://presse.fr/a"}]
        article_fetcher.enrich_with_article_text(items, limit=3)
        assert "full_text" not in items[0]

    def test_respects_the_attempts_ceiling(self, monkeypatch):
        monkeypatch.setattr(article_fetcher, "REQUEST_DELAY_SECONDS", 0)
        tried = []
        monkeypatch.setattr(article_fetcher, "fetch_article_text",
                            lambda url, session=None: tried.append(url))
        items = [{"url": f"https://presse.fr/{i}"} for i in range(20)]
        article_fetcher.enrich_with_article_text(items, limit=10, max_attempts=3)
        assert len(tried) == 3
