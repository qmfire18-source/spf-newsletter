import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_weekly  # noqa: E402

from src.db.models import Base, Draft, NewsItem, StageOffer  # noqa: E402

NEWS = [{
    "title": "La BCE relève ses taux",
    "source": "Le Monde",
    "url": "https://lemonde.fr/1",
    "raw_summary": "Hausse à 2,5%.",
}]
STAGES = [{
    "title": "Stage M&A",
    "company": "GreenYellow",
    "location": "Puteaux",
    "deadline": "2026-12-07",
    "url": "https://wttj.fr/1",
}]
GENERATED = {"news_html": "<p>Actu</p>", "stages_html": "<p>Stage</p>"}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(run_weekly, "SessionLocal", Session)
    monkeypatch.setattr(run_weekly, "init_db", lambda: None)
    return Session()


@pytest.fixture
def pipeline(monkeypatch):
    calls = {"news": 0, "stages": 0, "draft": 0, "enriched": 0}

    def enrich(items, limit):
        # Neutralisé : sans ça, la suite irait chercher les articles en ligne.
        calls["enriched"] += 1
        return items

    def fetch_news(sources):
        calls["news"] += 1
        return NEWS

    def pool(db, days=7, limit=40):
        calls["stages"] += 1
        return STAGES

    def generate(news, stages):
        calls["draft"] += 1
        return GENERATED

    monkeypatch.setattr(run_weekly, "fetch_news", fetch_news)
    monkeypatch.setattr(run_weekly.offer_store, "recent_offers", pool)
    monkeypatch.setattr(run_weekly.offer_store, "known_urls", lambda db: set())
    monkeypatch.setattr(run_weekly, "run_fetch_stage_offers", lambda s, known_urls=None: [])
    monkeypatch.setattr(run_weekly, "generate_draft", generate)
    monkeypatch.setattr(run_weekly, "enrich_with_article_text", enrich)
    # Le choix automatique du moteur irait sinon chercher le CLI Claude Code
    # installé sur la machine, et lancerait une vraie génération.
    monkeypatch.setattr(run_weekly, "choose_generator", lambda mode: (generate, "test"))
    return calls


class TestCurrentWeekOf:
    @pytest.mark.parametrize("today,expected", [
        (date(2026, 9, 10), date(2026, 9, 7)),   # jeudi -> lundi
        (date(2026, 9, 7), date(2026, 9, 7)),    # lundi -> lui-même
        (date(2026, 9, 13), date(2026, 9, 7)),   # dimanche -> lundi précédent
    ])
    def test_returns_the_monday_of_the_week(self, today, expected):
        assert run_weekly.current_week_of(today) == expected


class TestIdempotence:
    def test_creates_one_draft(self, db, pipeline):
        assert run_weekly.main() == 0
        assert db.query(Draft).count() == 1

    def test_second_run_creates_nothing(self, db, pipeline):
        run_weekly.main()
        assert run_weekly.main() == 0
        assert db.query(Draft).count() == 1

    def test_second_run_skips_scraping_and_the_ai_call(self, db, pipeline):
        run_weekly.main()
        run_weekly.main()
        assert pipeline == {"news": 1, "stages": 1, "draft": 1, "enriched": 1}

    def test_database_rejects_a_duplicate_week(self, db):
        from sqlalchemy.exc import IntegrityError

        db.add(Draft(week_of=date(2026, 9, 7), status="pending_review"))
        db.commit()
        db.add(Draft(week_of=date(2026, 9, 7), status="pending_review"))
        with pytest.raises(IntegrityError):
            db.commit()


class TestPersistence:
    def test_stores_the_generated_sections(self, db, pipeline):
        run_weekly.main()
        draft = db.query(Draft).one()
        assert draft.news_content == "<p>Actu</p>"
        assert draft.status == "pending_review"

    def test_keeps_the_raw_sources(self, db, pipeline):
        run_weekly.main()
        assert db.query(NewsItem).one().url == "https://lemonde.fr/1"
        offer = db.query(StageOffer).one()
        assert offer.company == "GreenYellow"
        assert offer.deadline == date(2026, 12, 7)

    def test_offer_without_deadline_is_accepted(self, db, monkeypatch, pipeline):
        monkeypatch.setattr(
            run_weekly.offer_store, "recent_offers",
            lambda db, days=7, limit=40: [{**STAGES[0], "deadline": None}],
        )
        assert run_weekly.main() == 0
        assert db.query(StageOffer).one().deadline is None


class TestFailureModes:
    def test_fails_when_every_source_is_empty(self, db, monkeypatch, pipeline):
        monkeypatch.setattr(run_weekly, "fetch_news", lambda s: [])
        monkeypatch.setattr(run_weekly.offer_store, "recent_offers", lambda db, **kw: [])
        monkeypatch.setattr(run_weekly.offer_store, "store_offers", lambda db, o: 0)
        assert run_weekly.main() == 1
        assert db.query(Draft).count() == 0

    def test_does_not_call_the_ai_when_there_is_nothing_to_write(
        self, db, monkeypatch, pipeline
    ):
        monkeypatch.setattr(run_weekly, "fetch_news", lambda s: [])
        monkeypatch.setattr(run_weekly.offer_store, "recent_offers", lambda db, **kw: [])
        monkeypatch.setattr(run_weekly.offer_store, "store_offers", lambda db, o: 0)
        run_weekly.main()
        assert pipeline["draft"] == 0


class TestNeverSends:
    def test_the_cron_never_imports_the_sender(self):
        source = Path(run_weekly.__file__).read_text()
        assert "brevo" not in source.lower()
        assert "send_campaign" not in source

    def test_no_send_function_is_reachable_from_the_module(self):
        assert not hasattr(run_weekly, "send_campaign")

    def test_draft_is_left_pending_review(self, db, pipeline):
        run_weekly.main()
        assert db.query(Draft).one().status == "pending_review"
        assert db.query(Draft).one().sent_at is None


class TestGeneratorChoice:
    def test_api_is_forced_when_asked(self, monkeypatch):
        monkeypatch.setattr(run_weekly, "ANTHROPIC_API_KEY", None)
        generate, engine = run_weekly.choose_generator("api")
        assert generate is run_weekly.generate_draft
        assert "API" in engine

    def test_local_is_forced_when_asked(self, monkeypatch):
        monkeypatch.setattr(run_weekly, "ANTHROPIC_API_KEY", "sk-ant-xxx")
        generate, _ = run_weekly.choose_generator("local")
        assert generate is run_weekly.generate_draft_locally

    def test_auto_prefers_the_api_when_a_key_exists(self, monkeypatch):
        # Seul moteur utilisable sans humain : c'est celui du cron.
        monkeypatch.setattr(run_weekly, "ANTHROPIC_API_KEY", "sk-ant-xxx")
        monkeypatch.setattr(run_weekly, "find_cli", lambda: "/bin/claude")
        generate, _ = run_weekly.choose_generator("auto")
        assert generate is run_weekly.generate_draft

    def test_auto_falls_back_to_the_local_cli(self, monkeypatch):
        monkeypatch.setattr(run_weekly, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(run_weekly, "find_cli", lambda: "/bin/claude")
        generate, _ = run_weekly.choose_generator("auto")
        assert generate is run_weekly.generate_draft_locally

    def test_auto_keeps_the_api_when_nothing_is_available(self, monkeypatch):
        # L'erreur de l'API est plus parlante que « CLI introuvable ».
        monkeypatch.setattr(run_weekly, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(run_weekly, "find_cli", lambda: None)
        generate, _ = run_weekly.choose_generator("auto")
        assert generate is run_weekly.generate_draft
