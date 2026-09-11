import gzip
import json
from datetime import datetime, timezone

import pytest

from src.scraper import stage_scraper


def jsonld_page(posting, extra_blocks=()):
    blocks = "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>'
        for b in (*extra_blocks, posting)
    )
    return f"<html><head>{blocks}</head><body></body></html>"


JOB_POSTING = {
    "@type": "JobPosting",
    "title": "Stage (H/F) - Financement de Projet et M&A",
    "employmentType": "INTERN",
    "hiringOrganization": {"@type": "Organization", "name": "GreenYellow"},
    "jobLocation": {"address": {"addressLocality": "Puteaux"}},
    "validThrough": "2026-12-07T14:17:22.000Z",
}


class TestLooksLikeFinanceStage:
    @pytest.mark.parametrize("url", [
        "https://w.com/fr/companies/greenyellow/jobs/stage-financement-de-projet-et-m-a_puteaux",
        "https://w.com/fr/companies/ardian/jobs/private-equity-stage-mars-2027_paris",
        "https://w.com/fr/companies/x/jobs/internship-asset-management_puteaux",
        # L'annonce en anglais reste éligible si elle est en France : les
        # meilleures offres parisiennes du vivier sont publiées en anglais.
        "https://w.com/en/companies/clipperton/jobs/technology-m-a-analyst-paris-january-2027-internship_paris",
        "https://w.com/en/companies/naxicap/jobs/stage-analyste-private-equity-small-caps_paris",
    ])
    def test_keeps_finance_internships(self, url):
        assert stage_scraper._looks_like_finance_stage(url)

    @pytest.mark.parametrize("url", [
        "https://w.com/fr/companies/x/jobs/internship-asset-management_london",
        "https://w.com/en/companies/zipline-1/jobs/strategic-finance-intern-2027_san-francisco",
        "https://w.com/en/companies/workwize/jobs/finance-intern_amsterdam",
        "https://w.com/en/companies/bally-s/jobs/fall-spring-finance-internship_providence",
    ])
    def test_drops_offers_outside_france(self, url):
        assert not stage_scraper._looks_like_finance_stage(url)

    @pytest.mark.parametrize("url", [
        "https://w.com/fr/companies/x/jobs/stage-developpeur-frontend_lyon",
        "https://w.com/fr/companies/x/jobs/directeur-financier-h-f_paris",
        "https://w.com/fr/companies/x/jobs/stage-hote-de-caisse_louviers",
    ])
    def test_drops_non_finance_or_non_internship(self, url):
        assert not stage_scraper._looks_like_finance_stage(url)

    @pytest.mark.parametrize("url", [
        # "services financiers" désigne ici le client, pas le métier.
        "https://w.com/fr/companies/sopra/jobs/stage-developpeur-se-java-cobol-services-financiers_rodez",
        "https://w.com/fr/companies/sopra/jobs/stage-analyste-cybersecurite-investigation_colomiers",
        "https://w.com/fr/companies/vinci/jobs/analyste-fonctionnel-sap-finance-1-stage-de-fin-d-etudes_paris",
        "https://w.com/fr/companies/sopra/jobs/stage-charge-de-recrutement-services-financiers_paris",
    ])
    def test_drops_it_and_hr_roles_sold_as_finance(self, url):
        assert not stage_scraper._looks_like_finance_stage(url)

    @pytest.mark.parametrize("url", [
        "https://w.com/fr/companies/x/jobs/stage-comptabilite-gestion-f-h_angouleme",
        "https://w.com/fr/companies/x/jobs/stage-controleur-de-gestion-h-f_bordeaux",
    ])
    def test_drops_accounting_and_management_control(self, url):
        assert not stage_scraper._looks_like_finance_stage(url)


class TestPrioritise:
    def moment(self, day):
        return datetime(2026, 9, day, tzinfo=timezone.utc)

    def url(self, company, slug):
        return f"https://w.com/fr/companies/{company}/jobs/{slug}"

    def test_spreads_the_request_budget_across_employers(self):
        # Un employeur qui publie en masse ne doit pas manger tout le budget.
        candidates = [
            (self.moment(10), self.url("credit-mutuel", f"stage-audit-{i}"))
            for i in range(5)
        ] + [
            (self.moment(9), self.url("ardian", "stage-private-equity")),
            (self.moment(8), self.url("bnp", "stage-m-a")),
        ]
        ordered = stage_scraper._prioritise(candidates)
        companies = [stage_scraper._company_slug(u) for u in ordered[:3]]
        assert companies == ["credit-mutuel", "ardian", "bnp"]

    def test_keeps_every_candidate(self):
        candidates = [
            (self.moment(10), self.url("a", "stage-m-a")),
            (self.moment(9), self.url("a", "stage-audit")),
            (self.moment(8), self.url("b", "stage-invest")),
        ]
        assert sorted(stage_scraper._prioritise(candidates)) == sorted(
            u for _, u in candidates
        )

    def test_most_recent_employer_comes_first(self):
        candidates = [
            (self.moment(8), self.url("vieux", "stage-audit")),
            (self.moment(11), self.url("recent", "stage-m-a")),
        ]
        ordered = stage_scraper._prioritise(candidates)
        assert stage_scraper._company_slug(ordered[0]) == "recent"

    def test_empty_input(self):
        assert stage_scraper._prioritise([]) == []


class TestOfferFromJsonld:
    def test_extracts_every_field(self):
        offer = stage_scraper._offer_from_jsonld(jsonld_page(JOB_POSTING), "https://w.com/j")
        assert offer == {
            "title": "Stage (H/F) - Financement de Projet et M&A",
            "company": "GreenYellow",
            "location": "Puteaux",
            "deadline": "2026-12-07",
            "url": "https://w.com/j",
        }

    def test_finds_posting_among_other_jsonld_blocks(self):
        html = jsonld_page(JOB_POSTING, extra_blocks=[{"@type": "BreadcrumbList"}])
        assert stage_scraper._offer_from_jsonld(html, "https://w.com/j") is not None

    def test_ignores_non_internship_offers(self):
        posting = {**JOB_POSTING, "employmentType": "FULL_TIME"}
        assert stage_scraper._offer_from_jsonld(jsonld_page(posting), "u") is None

    def test_accepts_employment_type_given_as_list(self):
        posting = {**JOB_POSTING, "employmentType": ["INTERN", "PART_TIME"]}
        assert stage_scraper._offer_from_jsonld(jsonld_page(posting), "u") is not None

    def test_returns_none_without_job_posting(self):
        assert stage_scraper._offer_from_jsonld("<html></html>", "u") is None

    def test_survives_malformed_jsonld(self):
        html = '<script type="application/ld+json">{not json</script>'
        assert stage_scraper._offer_from_jsonld(html, "u") is None

    def test_missing_deadline_becomes_none(self):
        posting = {k: v for k, v in JOB_POSTING.items() if k != "validThrough"}
        offer = stage_scraper._offer_from_jsonld(jsonld_page(posting), "u")
        assert offer["deadline"] is None

    def test_drops_offers_whose_country_is_not_france(self):
        posting = {**JOB_POSTING, "jobLocation": {"address": {
            "addressLocality": "Providence", "addressCountry": "US"}}}
        assert stage_scraper._offer_from_jsonld(jsonld_page(posting), "u") is None

    def test_keeps_offers_in_france(self):
        posting = {**JOB_POSTING, "jobLocation": {"address": {
            "addressLocality": "Paris", "addressCountry": "FR"}}}
        assert stage_scraper._offer_from_jsonld(jsonld_page(posting), "u") is not None

    def test_accepts_country_given_as_an_object(self):
        posting = {**JOB_POSTING, "jobLocation": {"address": {
            "addressLocality": "Paris", "addressCountry": {"name": "France"}}}}
        assert stage_scraper._offer_from_jsonld(jsonld_page(posting), "u") is not None

    def test_keeps_offer_when_country_is_absent(self):
        # Ne pas jeter une offre valable pour un champ que le site omet.
        assert stage_scraper._offer_from_jsonld(jsonld_page(JOB_POSTING), "u") is not None

    def test_location_given_as_list(self):
        posting = {**JOB_POSTING, "jobLocation": [{"address": {"addressLocality": "Lyon"}}]}
        offer = stage_scraper._offer_from_jsonld(jsonld_page(posting), "u")
        assert offer["location"] == "Lyon"


class TestDecode:
    def test_decompresses_gzipped_payload(self):
        assert stage_scraper._decode(gzip.compress(b"<loc>x</loc>")) == "<loc>x</loc>"

    def test_passes_through_plain_payload(self):
        assert stage_scraper._decode(b"<loc>x</loc>") == "<loc>x</loc>"


class TestDeduplicate:
    def test_drops_repeated_offers(self):
        offer = {"url": "u", "title": "t", "company": "c"}
        assert len(stage_scraper._deduplicate([offer, dict(offer)])) == 1

    def test_drops_the_same_offer_republished_under_another_url(self):
        offers = [
            {"url": "https://w.com/j/a_3izzycjr", "title": "Finance Internship",
             "company": "Bally's Interactive"},
            {"url": "https://w.com/j/a_tliwxefk", "title": "Finance Internship",
             "company": "Bally's Interactive"},
        ]
        assert len(stage_scraper._deduplicate(offers)) == 1

    def test_keeps_distinct_offers_from_the_same_company(self):
        offers = [
            {"url": "u1", "title": "Stage M&A", "company": "Ardian"},
            {"url": "u2", "title": "Stage Private Equity", "company": "Ardian"},
        ]
        assert len(stage_scraper._deduplicate(offers)) == 2

    def test_tolerates_missing_company(self):
        offers = [
            {"url": "u1", "title": "Stage M&A", "company": None},
            {"url": "u2", "title": "Stage M&A", "company": None},
        ]
        assert len(stage_scraper._deduplicate(offers)) == 1


class TestThrottleDetection:
    @pytest.mark.asyncio
    async def test_empty_body_is_reported_as_throttling(self):
        class Response:
            status_code = 202
            content = b""

            def raise_for_status(self):
                pass

        class Client:
            async def get(self, url):
                return Response()

        with pytest.raises(stage_scraper.SiteThrottledError):
            await stage_scraper._get(Client(), "https://w.com/j")

    @pytest.mark.asyncio
    async def test_non_empty_body_passes_through(self):
        class Response:
            status_code = 200
            content = b"<html></html>"

            def raise_for_status(self):
                pass

        class Client:
            async def get(self, url):
                return Response()

        assert await stage_scraper._get(Client(), "u") == b"<html></html>"


class TestUserAgent:
    def test_is_ascii_encodable(self):
        # Les en-têtes HTTP sont ASCII : un accent ferait planter la requête.
        stage_scraper.USER_AGENT.encode("ascii")

    def test_identifies_the_bot_and_a_contact(self):
        assert "SPFNewsletterBot" in stage_scraper.USER_AGENT
        assert "+http" in stage_scraper.USER_AGENT


class TestNoForbiddenSource:
    def test_linkedin_is_never_targeted(self):
        from src import config
        assert not any(
            "linkedin" in json.dumps(s).lower() for s in config.STAGE_SOURCES
        )

    def test_jobteaser_is_not_targeted(self):
        from src import config
        assert not any(
            "jobteaser" in json.dumps(s).lower() for s in config.STAGE_SOURCES
        )
