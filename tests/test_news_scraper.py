import random
from datetime import datetime, timedelta, timezone

import pytest

from src.scraper import news_scraper


@pytest.fixture(autouse=True)
def no_politeness_delay(monkeypatch):
    monkeypatch.setattr(news_scraper.time, "sleep", lambda _: None)


def make_entry(title="Titre", link="https://example.com/a", summary="", published=None):
    entry = {"title": title, "link": link, "summary": summary}
    if published:
        entry["published_parsed"] = published.timetuple()
    return entry


class TestNormalizeUrl:
    def test_ignores_tracking_params_www_and_trailing_slash(self):
        a = news_scraper._normalize_url(
            "https://www.lemonde.fr/article/?utm_source=twitter&id=7"
        )
        b = news_scraper._normalize_url("https://lemonde.fr/article?id=7")
        assert a == b

    def test_keeps_meaningful_params(self):
        a = news_scraper._normalize_url("https://example.com/x?id=1")
        b = news_scraper._normalize_url("https://example.com/x?id=2")
        assert a != b


class TestNormalizeTitle:
    def test_strips_accents_case_and_punctuation(self):
        assert news_scraper._normalize_title(
            "La BCE relève ses taux !"
        ) == news_scraper._normalize_title("la bce releve ses taux")


class TestCleanHtml:
    def test_strips_tags_and_entities(self):
        assert news_scraper._clean_html("<p>Fusion &amp; acquisition</p>") == (
            "Fusion & acquisition"
        )

    def test_truncates_long_summaries(self):
        assert len(news_scraper._clean_html("a" * 5000)) == news_scraper.MAX_SUMMARY_CHARS


class TestDeduplicate:
    def test_drops_duplicate_urls(self):
        items = [
            {"title": "A", "url": "https://example.com/x"},
            {"title": "B", "url": "https://www.example.com/x/?utm_medium=rss"},
        ]
        assert len(news_scraper._deduplicate(items)) == 1

    def test_drops_similar_titles_across_sources(self):
        items = [
            {"title": "La BCE relève ses taux directeurs", "url": "https://a.fr/1"},
            {"title": "La BCE relève ses taux directeurs.", "url": "https://b.fr/2"},
        ]
        assert len(news_scraper._deduplicate(items)) == 1

    def test_keeps_distinct_articles(self):
        items = [
            {"title": "La BCE relève ses taux", "url": "https://a.fr/1"},
            {"title": "Fusion surprise dans le luxe", "url": "https://b.fr/2"},
        ]
        assert len(news_scraper._deduplicate(items)) == 2

    def test_keeps_first_occurrence(self):
        items = [
            {"title": "Sujet", "url": "https://a.fr/1"},
            {"title": "Sujet", "url": "https://b.fr/2"},
        ]
        assert news_scraper._deduplicate(items)[0]["url"] == "https://a.fr/1"


class TestEntryToItem:
    def test_drops_entries_older_than_cutoff(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        entry = make_entry(published=datetime.now(timezone.utc) - timedelta(days=30))
        assert news_scraper._entry_to_item(entry, "Source", cutoff) is None

    def test_keeps_recent_entries(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        entry = make_entry(published=datetime.now(timezone.utc) - timedelta(days=1))
        assert news_scraper._entry_to_item(entry, "Source", cutoff) is not None

    def test_keeps_undated_entries(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        assert news_scraper._entry_to_item(make_entry(), "Source", cutoff) is not None

    def test_drops_entries_without_title_or_link(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        assert news_scraper._entry_to_item(make_entry(title=""), "Source", cutoff) is None
        assert news_scraper._entry_to_item(make_entry(link=None), "Source", cutoff) is None


class TestIsFinanceRelated:
    @pytest.mark.parametrize("title", [
        "La BCE relève ses taux directeurs",
        "Le CAC 40 termine en hausse",
        "Financement participatif en plein essor",
        "Deal M&A record dans le luxe",
        "Une levée de fonds pour la fintech",
        "Wall Street ouvre en baisse",
    ])
    def test_keeps_finance_topics(self, title):
        assert news_scraper._is_finance_related({"title": title})

    @pytest.mark.parametrize("title", [
        "Le nouvel iPhone pliable d'Apple",
        "Grève des transports en Île-de-France",
        "Un film américain rafle les récompenses",
    ])
    def test_drops_off_topic(self, title):
        assert not news_scraper._is_finance_related({"title": title})

    def test_matches_on_summary_when_title_is_vague(self):
        item = {"title": "Coup de théâtre", "raw_summary": "Le rachat de la banque"}
        assert news_scraper._is_finance_related(item)


class TestStripSourceSuffix:
    def test_removes_google_news_media_suffix(self):
        assert news_scraper._strip_source_suffix("Titre - Le Monde", "Le Monde") == "Titre"

    def test_leaves_other_titles_intact(self):
        assert news_scraper._strip_source_suffix("Titre - autre", "Le Monde") == (
            "Titre - autre"
        )


class TestFetchNews:
    def test_unreachable_source_does_not_break_the_run(self, monkeypatch):
        def fail(*args, **kwargs):
            raise ConnectionError("boom")

        monkeypatch.setattr(news_scraper, "_fetch_rss", fail)
        assert news_scraper.fetch_news([{"type": "rss", "url": "https://a.fr"}]) == []

    def test_unknown_source_type_is_skipped(self):
        assert news_scraper.fetch_news([{"type": "carrier-pigeon"}]) == []

    def test_aggregates_and_deduplicates_across_sources(self, monkeypatch):
        article = {"title": "La BCE relève ses taux", "url": "https://a.fr/1"}
        monkeypatch.setattr(news_scraper, "_fetch_rss", lambda url, cutoff: [article])
        monkeypatch.setattr(
            news_scraper,
            "_fetch_gnews",
            lambda query, cutoff: [{**article, "url": "https://a.fr/1?utm_x=1"}],
        )
        result = news_scraper.fetch_news([
            {"type": "rss", "url": "https://a.fr"},
            {"type": "gnews", "query": "finance"},
        ])
        assert len(result) == 1

    def test_drops_off_topic_articles(self, monkeypatch):
        monkeypatch.setattr(
            news_scraper,
            "_fetch_rss",
            lambda url, cutoff: [
                {"title": "La BCE relève ses taux", "url": "https://a.fr/1"},
                {"title": "Le nouvel iPhone pliable d'Apple", "url": "https://a.fr/2"},
            ],
        )
        result = news_scraper.fetch_news([{"type": "rss", "url": "https://a.fr"}])
        assert [i["url"] for i in result] == ["https://a.fr/1"]

    def test_caps_and_sorts_by_recency(self, monkeypatch):
        now = datetime.now(timezone.utc)
        # Titres volontairement dissemblables : sinon la dédup par similarité
        # les fusionne avant même le plafonnement.
        rng = random.Random(0)
        vocabulaire = [f"mot{i}" for i in range(300)]
        articles = [
            {
                "title": "taux " + " ".join(rng.sample(vocabulaire, 10)),
                "url": f"https://a.fr/{n}",
                "published": (now - timedelta(hours=n)).isoformat(),
            }
            for n in range(news_scraper.MAX_NEWS_ITEMS + 10)
        ]
        monkeypatch.setattr(news_scraper, "_fetch_rss", lambda url, cutoff: articles)
        result = news_scraper.fetch_news([{"type": "rss", "url": "https://a.fr"}])
        assert len(result) == news_scraper.MAX_NEWS_ITEMS
        assert result[0]["url"] == "https://a.fr/0"

    def test_undated_articles_sort_last(self, monkeypatch):
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(
            news_scraper,
            "_fetch_rss",
            lambda url, cutoff: [
                {"title": "Fusion dans la banque", "url": "https://a.fr/1"},
                {
                    "title": "Le CAC 40 progresse",
                    "url": "https://a.fr/2",
                    "published": now.isoformat(),
                },
            ],
        )
        result = news_scraper.fetch_news([{"type": "rss", "url": "https://a.fr"}])
        assert [i["url"] for i in result] == ["https://a.fr/2", "https://a.fr/1"]


class TestFetchNewsapi:
    def test_returns_empty_without_key(self, monkeypatch):
        monkeypatch.setattr(news_scraper, "NEWSAPI_KEY", None)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        assert news_scraper._fetch_newsapi("finance", cutoff) == []


class TestConsumerNoiseVeto:
    def item(self, title, summary=""):
        return {"title": title, "url": "https://x.fr/1", "raw_summary": summary}

    @pytest.mark.parametrize("title", [
        "Prix des carburants : la crise est loin d'être terminée",
        "Pouvoir d'achat : les tomates à 9,95 euros, c'est du jamais-vu",
        "Le changement climatique pourrait amputer le PIB français de 3,6%",
        "Chômage, inflation, pouvoir d'achat : la douche froide",
    ])
    def test_drops_consumer_and_society_topics(self, title):
        assert not news_scraper._is_finance_related(self.item(title))

    @pytest.mark.parametrize("title", [
        "La BCE relève ses taux d'intérêt à 2,5 % face à l'inflation",
        "Les Etats-Unis se financent au coût le plus élevé en 25 ans",
        "Le spread entre les taux d'emprunt français et allemands se creuse",
        "Aublé Law accueille Patrick Dupuis comme associé en corporate/M&A",
    ])
    def test_keeps_macro_and_market_topics(self, title):
        # Le macro reste dans le périmètre : c'est l'angle conso qui en sort.
        assert news_scraper._is_finance_related(self.item(title))

    def test_veto_applies_even_when_a_finance_word_is_present(self):
        item = self.item(
            "L'écologie est le poste le plus touché pour financer le plan d'aide"
        )
        assert not news_scraper._is_finance_related(item)

    def test_veto_reads_the_summary_too(self):
        item = self.item("Les Experts", summary="Chômage et prix à la pompe")
        assert not news_scraper._is_finance_related(item)


class TestSpreadAcrossSources:
    def item(self, source, published):
        return {"source": source, "published": published, "title": source}

    def test_alternates_between_sources(self):
        # Une source prolixe ne doit pas occuper toute la tête de liste.
        items = [self.item("bfm", f"2026-09-11T1{i}:00:00+00:00") for i in range(5)]
        items += [
            self.item("agefi", "2026-09-10T09:00:00+00:00"),
            self.item("echos", "2026-09-09T09:00:00+00:00"),
        ]
        ordered = news_scraper._spread_across_sources(items)
        assert [i["source"] for i in ordered[:3]] == ["bfm", "agefi", "echos"]

    def test_keeps_every_item(self):
        items = [
            self.item("a", "2026-09-11T10:00:00+00:00"),
            self.item("a", "2026-09-10T10:00:00+00:00"),
            self.item("b", "2026-09-09T10:00:00+00:00"),
        ]
        assert len(news_scraper._spread_across_sources(items)) == 3

    def test_most_recent_source_leads(self):
        items = [
            self.item("vieux", "2026-09-01T10:00:00+00:00"),
            self.item("recent", "2026-09-11T10:00:00+00:00"),
        ]
        ordered = news_scraper._spread_across_sources(items)
        assert ordered[0]["source"] == "recent"

    def test_within_a_source_the_newest_comes_first(self):
        items = [
            self.item("a", "2026-09-09T10:00:00+00:00"),
            self.item("a", "2026-09-11T10:00:00+00:00"),
        ]
        ordered = news_scraper._spread_across_sources(items)
        assert ordered[0]["published"].startswith("2026-09-11")

    def test_empty_input(self):
        assert news_scraper._spread_across_sources([]) == []

    def test_tolerates_a_missing_source(self):
        items = [self.item(None, "2026-09-11T10:00:00+00:00")]
        assert len(news_scraper._spread_across_sources(items)) == 1
