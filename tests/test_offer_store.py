from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db import offer_store
from src.db.models import Base, CollectedOffer, utcnow


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def offre(url, **champs):
    return {
        "url": url,
        "title": champs.get("title", "Stage M&A"),
        "company": champs.get("company", "Ardian"),
        "location": champs.get("location", "Paris"),
        "deadline": champs.get("deadline", "2027-01-31"),
    }


class TestStoreOffers:
    def test_stores_new_offers(self, db):
        assert offer_store.store_offers(db, [offre("u1"), offre("u2")]) == 2
        assert db.query(CollectedOffer).count() == 2

    def test_a_known_url_is_not_duplicated(self, db):
        offer_store.store_offers(db, [offre("u1")])
        assert offer_store.store_offers(db, [offre("u1")]) == 0
        assert db.query(CollectedOffer).count() == 1

    def test_one_duplicate_does_not_drop_the_others(self, db):
        offer_store.store_offers(db, [offre("u1")])
        assert offer_store.store_offers(db, [offre("u1"), offre("u2")]) == 1
        assert db.query(CollectedOffer).count() == 2

    def test_offer_without_url_is_ignored(self, db):
        assert offer_store.store_offers(db, [{"title": "x"}]) == 0

    def test_deadline_string_becomes_a_date(self, db):
        offer_store.store_offers(db, [offre("u1", deadline="2027-01-31")])
        assert db.query(CollectedOffer).one().deadline == date(2027, 1, 31)

    def test_missing_or_unparsable_deadline(self, db):
        offer_store.store_offers(db, [offre("u1", deadline=None), offre("u2", deadline="bientôt")])
        assert all(o.deadline is None for o in db.query(CollectedOffer).all())


class TestKnownUrls:
    def test_lists_what_is_stored(self, db):
        offer_store.store_offers(db, [offre("u1"), offre("u2")])
        assert offer_store.known_urls(db) == {"u1", "u2"}

    def test_empty_store(self, db):
        assert offer_store.known_urls(db) == set()


class TestRecentOffers:
    def test_returns_stored_offers_newest_first(self, db):
        offer_store.store_offers(db, [offre("u1", title="A")])
        offer_store.store_offers(db, [offre("u2", title="B")])
        db.query(CollectedOffer).filter_by(url="u1").update(
            {"collected_at": utcnow() - timedelta(days=2)}
        )
        db.commit()
        assert [o["title"] for o in offer_store.recent_offers(db)] == ["B", "A"]

    def test_ignores_offers_older_than_the_window(self, db):
        offer_store.store_offers(db, [offre("u1")])
        db.query(CollectedOffer).update({"collected_at": utcnow() - timedelta(days=9)})
        db.commit()
        assert offer_store.recent_offers(db, days=7) == []

    def test_drops_offers_whose_deadline_has_passed(self, db):
        # Publier une candidature close enverrait les lecteurs dans le mur.
        hier = (date.today() - timedelta(days=1)).isoformat()
        offer_store.store_offers(db, [offre("u1", deadline=hier), offre("u2")])
        assert [o["url"] for o in offer_store.recent_offers(db)] == ["u2"]

    def test_keeps_offers_without_a_deadline(self, db):
        offer_store.store_offers(db, [offre("u1", deadline=None)])
        assert len(offer_store.recent_offers(db)) == 1

    def test_keeps_an_offer_closing_today(self, db):
        offer_store.store_offers(db, [offre("u1", deadline=date.today().isoformat())])
        assert len(offer_store.recent_offers(db)) == 1

    def test_respects_the_limit(self, db):
        offer_store.store_offers(db, [offre(f"u{i}") for i in range(10)])
        assert len(offer_store.recent_offers(db, limit=4)) == 4

    def test_shape_matches_what_the_draft_expects(self, db):
        offer_store.store_offers(db, [offre("u1")])
        assert set(offer_store.recent_offers(db)[0]) == {
            "title", "company", "location", "deadline", "url"
        }


class TestPurge:
    def test_removes_offers_past_the_retention(self, db):
        offer_store.store_offers(db, [offre("u1"), offre("u2")])
        db.query(CollectedOffer).filter_by(url="u1").update(
            {"collected_at": utcnow() - timedelta(days=45)}
        )
        db.commit()
        assert offer_store.purge_old(db, days=30) == 1
        assert offer_store.known_urls(db) == {"u2"}

    def test_keeps_everything_recent(self, db):
        offer_store.store_offers(db, [offre("u1")])
        assert offer_store.purge_old(db, days=30) == 0
