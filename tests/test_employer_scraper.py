import pytest
import requests

from src.scraper import employer_scraper


class FakeResponse:
    def __init__(self, payload=None, status=200, boom=False):
        self._payload = payload or {}
        self.status_code = status
        self._boom = boom

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._boom:
            raise ValueError("pas du JSON")
        return self._payload


def recruitee_payload(*offres):
    return {"offers": list(offres)}


def offre(title="Transaction Services - Intern", city="Paris", country_code="FR",
          country="France", url="https://8advisory.recruitee.com/o/x"):
    return {"title": title, "city": city, "country_code": country_code,
            "country": country, "careers_url": url}


class TestInternshipDetection:
    @pytest.mark.parametrize("titre", [
        "Transaction Services - Intern",
        "Stage fin d'études M&A",
        "Stagiaire en actuariat",
        "Summer Analyst 2027",
        "Off-cycle internship",
        "Alternance contrôle de gestion",
    ])
    def test_internships_are_kept(self, titre):
        assert employer_scraper._is_internship(titre)

    @pytest.mark.parametrize("titre", [
        "Senior Manager Transaction Services",
        "Directeur financier",
        "Avocat collaborateur expérimenté",
    ])
    def test_permanent_roles_are_refused(self, titre):
        assert not employer_scraper._is_internship(titre)


class TestGeographicScope:
    @pytest.mark.parametrize("ville", ["Paris", "Puteaux", "La Défense"])
    def test_paris_region_is_in_scope(self, ville):
        assert employer_scraper._is_in_scope(ville, "FR")

    @pytest.mark.parametrize("ville", ["Nantes", "Bordeaux", "Lyon", "Rodez"])
    def test_french_provinces_are_out(self, ville):
        assert not employer_scraper._is_in_scope(ville, "FR")

    @pytest.mark.parametrize("ville,pays", [
        ("London", "GB"), ("Zurich", "CH"), ("Munich", "DE"),
        ("New York", "US"), ("Luxembourg", "LU"),
    ])
    def test_abroad_is_in_scope(self, ville, pays):
        # Les places financières étrangères sont une cible de l'association.
        assert employer_scraper._is_in_scope(ville, pays)

    def test_a_missing_city_does_not_reject(self):
        assert employer_scraper._is_in_scope(None, "FR")

    def test_accents_do_not_matter(self):
        assert not employer_scraper._is_in_scope("Mérignac", "FR")


class TestFetchRecruitee:
    def test_keeps_only_internships_in_scope(self, monkeypatch):
        payload = recruitee_payload(
            offre(title="Transaction Services - Intern", city="Paris"),
            offre(title="Senior Manager", city="Paris"),
            offre(title="Stage audit", city="Nantes"),
            offre(title="Strategy - Intern", city="Zurich", country_code="CH"),
        )
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: payload)
        offres = employer_scraper.fetch_recruitee("8advisory", "Eight Advisory")
        assert [o["title"] for o in offres] == [
            "Transaction Services - Intern", "Strategy - Intern"
        ]

    def test_shape_matches_the_rest_of_the_pipeline(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json",
                            lambda url, session=None: recruitee_payload(offre()))
        o = employer_scraper.fetch_recruitee("8advisory", "Eight Advisory")[0]
        assert set(o) == {"title", "company", "location", "deadline", "url"}
        assert o["company"] == "Eight Advisory"
        assert o["location"] == "Paris, France"
        assert o["deadline"] is None

    def test_an_unreachable_employer_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: None)
        assert employer_scraper.fetch_recruitee("x", "X") == []

    def test_empty_board(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json",
                            lambda url, session=None: {"offers": []})
        assert employer_scraper.fetch_recruitee("x", "X") == []


class TestGetJson:
    def test_a_failing_request_returns_none(self, monkeypatch):
        def boom(url, **kwargs):
            raise requests.ConnectionError("injoignable")
        monkeypatch.setattr(employer_scraper.requests, "get", boom)
        assert employer_scraper._get_json("https://x") is None

    def test_a_non_json_answer_returns_none(self, monkeypatch):
        monkeypatch.setattr(employer_scraper.requests, "get",
                            lambda url, **kw: FakeResponse(boom=True))
        assert employer_scraper._get_json("https://x") is None

    def test_an_http_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(employer_scraper.requests, "get",
                            lambda url, **kw: FakeResponse(status=403))
        assert employer_scraper._get_json("https://x") is None


class TestFetchEmployerOffers:
    def test_one_broken_employer_does_not_stop_the_others(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)

        def connecteur(source):
            if source["name"] == "Cassé":
                raise RuntimeError("portail en panne")
            return [offre(title="Stage M&A")]

        monkeypatch.setitem(employer_scraper.CONNECTORS, "recruitee", connecteur)
        offres = employer_scraper.fetch_employer_offers([
            {"type": "recruitee", "slug": "a", "name": "Cassé"},
            {"type": "recruitee", "slug": "b", "name": "Sain"},
        ])
        assert len(offres) == 1

    def test_an_unknown_connector_is_skipped(self):
        assert employer_scraper.fetch_employer_offers(
            [{"type": "inconnu", "name": "X"}]
        ) == []

    def test_no_sources(self):
        assert employer_scraper.fetch_employer_offers([]) == []
