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


class TestInternshipWordBoundaries:
    @pytest.mark.parametrize("titre", [
        "Internal Advisor Consultant",
        "Internal Audit Manager",
        "International Sales Director",
        "Alternator Systems Engineer",
    ])
    def test_a_marker_hidden_inside_a_word_does_not_count(self, titre):
        # « intern » se cache dans « Internal » : un consultant interne
        # n'est pas un stagiaire.
        assert not employer_scraper._is_internship(titre)

    @pytest.mark.parametrize("titre", [
        "M&A intern - Large Cap",
        "2027 London Financial Advisory Summer Internship",
        "VIE Finance Londres",
        "Off-cycle internship",
    ])
    def test_the_marker_as_a_whole_word_counts(self, titre):
        assert employer_scraper._is_internship(titre)


class TestFetchOracle:
    def payload(self, *annonces):
        return {"items": [{"requisitionList": list(annonces)}]}

    def annonce(self, title="M&A Internship", loc="Paris, France",
                pays="France", ident="123", fin="2027-01-31T00:00:00+00:00"):
        return {"Title": title, "PrimaryLocation": loc,
                "PrimaryLocationCountry": pays, "Id": ident, "PostingEndDate": fin}

    def test_keeps_internships_in_scope(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: self.payload(
            self.annonce(title="M&A Internship", loc="Paris, France"),
            self.annonce(title="Managing Director", loc="Paris, France"),
            self.annonce(title="Stage gestion privée", loc="Lyon, France"),
            self.annonce(title="Summer Internship", loc="London, United Kingdom",
                         pays="United Kingdom"),
        ))
        offres = employer_scraper.fetch_oracle("h", ["CX_1"], "Lazard")
        assert [o["title"] for o in offres] == ["M&A Internship", "Summer Internship"]

    def test_builds_a_usable_link_and_deadline(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)
        monkeypatch.setattr(employer_scraper, "_get_json",
                            lambda url, session=None: self.payload(self.annonce()))
        o = employer_scraper.fetch_oracle("h.example", ["CX_2"], "Lazard")[0]
        assert o["url"].startswith("https://h.example/hcmUI/CandidateExperience/")
        assert o["url"].endswith("/CX_2/job/123")
        assert o["deadline"] == "2027-01-31"
        assert o["company"] == "Lazard"

    def test_a_missing_deadline_is_none(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)
        monkeypatch.setattr(employer_scraper, "_get_json",
                            lambda url, session=None: self.payload(self.annonce(fin=None)))
        assert employer_scraper.fetch_oracle("h", ["CX_1"], "L")[0]["deadline"] is None

    def test_both_portals_are_queried(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)
        vus = []

        def faux(url, session=None):
            vus.append(url)
            return self.payload(self.annonce(ident=str(len(vus))))

        monkeypatch.setattr(employer_scraper, "_get_json", faux)
        offres = employer_scraper.fetch_oracle("h", ["CX_1", "CX_2"], "Lazard")
        assert len(vus) == 2 and len(offres) == 2

    def test_the_expand_parameter_is_requested(self, monkeypatch):
        # Sans expand, l'API renvoie le compteur mais pas les annonces.
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)
        vus = []
        monkeypatch.setattr(employer_scraper, "_get_json",
                            lambda url, session=None: vus.append(url) or self.payload())
        employer_scraper.fetch_oracle("h", ["CX_1"], "L")
        assert "expand=requisitionList" in vus[0]

    def test_an_unreachable_portal_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "REQUEST_DELAY_SECONDS", 0)
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: None)
        assert employer_scraper.fetch_oracle("h", ["CX_1"], "L") == []


EURONEXT_LIGNE = '''
<tr>
 <td class="views-field views-field-field-country">{pays} </td>
 <td class="views-field views-field-field-job-title"><a href="{url}">{titre}</a> </td>
 <td class="views-field views-field-field-job-sub-type">{contrat} </td>
 <td class="views-field views-field-name">{ville} </td>
</tr>'''


def euronext_page(*lignes):
    return "<table><tbody>" + "".join(lignes) + "</tbody></table>"


def euronext_ligne(titre="Corporate Actions Intern", contrat="Intern (Fixed Term) (Trainee)",
                   ville="Paris", pays="France", url="/en/about/careers/job-offers/r1-x"):
    return EURONEXT_LIGNE.format(titre=titre, contrat=contrat, ville=ville,
                                 pays=pays, url=url)


class TestFetchEuronext:
    def test_keeps_internships_and_vie(self, monkeypatch):
        page = euronext_page(
            euronext_ligne(titre="Corporate Actions Intern"),
            euronext_ligne(titre="Issuance Product Manager", contrat="Permanent"),
            euronext_ligne(titre="ESG Analyst", contrat="International Graduate Programme VIE",
                           ville="Athens", pays="Greece"),
            euronext_ligne(titre="Consultant", contrat="Fixed Term (Fixed Term)"),
        )
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: page)
        offres = employer_scraper.fetch_euronext()
        assert [o["title"] for o in offres] == ["Corporate Actions Intern", "ESG Analyst"]

    def test_the_contract_column_decides_not_the_title(self, monkeypatch):
        # Un intitulé sans le mot « stage » reste un stage si la colonne le dit.
        page = euronext_page(euronext_ligne(titre="Student Employee"))
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: page)
        assert len(employer_scraper.fetch_euronext()) == 1

    def test_french_provinces_are_dropped(self, monkeypatch):
        page = euronext_page(euronext_ligne(ville="Lyon", pays="France"))
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: page)
        assert employer_scraper.fetch_euronext() == []

    def test_link_and_shape(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_html",
                            lambda url: euronext_page(euronext_ligne()))
        o = employer_scraper.fetch_euronext()[0]
        assert o["url"].startswith("https://www.euronext.com/")
        assert o["company"] == "Euronext"
        assert o["location"] == "Paris, France"

    def test_an_unreachable_site_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: None)
        assert employer_scraper.fetch_euronext() == []


def talentsoft_bloc(titre="Stage Capital Markets H/F", contrat="Stage",
                    entite="Amundi Asset Management", pays="France",
                    url="/offre-de-emploi/emploi-x_1.aspx"):
    return (
        '<li class="ts-offer-list-item offerlist-item">'
        f'<h3><a class="ts-offer-list-item__title-link" href="{url}">{titre}</a></h3>'
        '<ul class="ts-offer-list-item__description ">'
        f"<li>{contrat}</li><li>{entite}</li><li>{pays}</li></ul>"
    )


class TestFetchTalentsoft:
    def test_keeps_only_internships(self, monkeypatch):
        page = "".join([
            talentsoft_bloc(titre="Stage Capital Markets H/F"),
            talentsoft_bloc(titre="Senior Project Manager", contrat="CDI"),
            talentsoft_bloc(titre="Alternance Data", contrat="Alternance"),
        ])
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: page)
        offres = employer_scraper.fetch_talentsoft("jobs.amundi.com", "Amundi")
        assert [o["title"] for o in offres] == ["Stage Capital Markets H/F", "Alternance Data"]

    def test_entity_and_country_make_the_location(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: talentsoft_bloc())
        o = employer_scraper.fetch_talentsoft("jobs.amundi.com", "Amundi")[0]
        assert o["location"] == "Amundi Asset Management, France"
        assert o["company"] == "Amundi"
        assert o["url"].startswith("https://jobs.amundi.com/")

    def test_html_entities_are_decoded(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_html",
                            lambda url: talentsoft_bloc(titre="Stage Compliance &amp; Risk"))
        o = employer_scraper.fetch_talentsoft("h", "Amundi")[0]
        assert o["title"] == "Stage Compliance & Risk"

    def test_a_block_without_a_link_is_skipped(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_html",
                            lambda url: '<li class="ts-offer-list-item">rien</li>')
        assert employer_scraper.fetch_talentsoft("h", "Amundi") == []

    def test_an_unreachable_site_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_html", lambda url: None)
        assert employer_scraper.fetch_talentsoft("h", "Amundi") == []


def lever_annonce(text="Regulatory Reporting Intern", commitment="Internship",
                  location="Paris", country="FR", url="https://jobs.lever.co/x/1"):
    return {"text": text, "country": country, "hostedUrl": url,
            "categories": {"commitment": commitment, "location": location}}


class TestFetchLever:
    def test_the_commitment_field_decides(self, monkeypatch):
        # Plus sûr qu'un mot-clé : « Internal Auditor » ne doit pas passer.
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: [
            lever_annonce(text="Regulatory Reporting Intern", commitment="Internship"),
            lever_annonce(text="Internal Auditor", commitment="Full-time"),
        ])
        offres = employer_scraper.fetch_lever("qonto", "Qonto")
        assert [o["title"] for o in offres] == ["Regulatory Reporting Intern"]

    def test_a_french_title_without_commitment_still_counts(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: [
            lever_annonce(text="Alternance - Data Analyst Credit Risk", commitment=None),
        ])
        assert len(employer_scraper.fetch_lever("younited", "Younited")) == 1

    def test_finance_only_drops_commercial_roles(self, monkeypatch):
        # Chez une fintech, tout n'est pas de la finance.
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: [
            lever_annonce(text="Regulatory Reporting Intern"),
            lever_annonce(text="Sales Development Intern"),
            lever_annonce(text="Office Manager Intern"),
        ])
        offres = employer_scraper.fetch_lever("qonto", "Qonto", finance_only=True)
        assert [o["title"] for o in offres] == ["Regulatory Reporting Intern"]

    def test_without_the_flag_everything_relevant_stays(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: [
            lever_annonce(text="Sales Development Intern"),
        ])
        assert len(employer_scraper.fetch_lever("x", "X")) == 1

    def test_flags_are_stripped_from_the_location(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: [
            lever_annonce(location="Milan 🇮🇹", country="IT"),
        ])
        assert employer_scraper.fetch_lever("x", "X")[0]["location"] == "Milan"

    def test_french_provinces_are_dropped(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: [
            lever_annonce(location="Lyon", country="FR"),
        ])
        assert employer_scraper.fetch_lever("x", "X") == []

    def test_an_unreachable_board_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(employer_scraper, "_get_json", lambda url, session=None: None)
        assert employer_scraper.fetch_lever("x", "X") == []


class TestFinanceRoleDetection:
    @pytest.mark.parametrize("titre", [
        "Regulatory Reporting Intern", "Stage contrôle de gestion",
        "Credit Risk Analyst", "Compliance Intern", "Stage M&A",
        "Treasury Intern", "Alternance comptabilité",
    ])
    def test_finance_roles(self, titre):
        assert employer_scraper._is_finance_role(titre)

    @pytest.mark.parametrize("titre", [
        "Sales Development Intern", "Office Manager Intern",
        "Stage graphisme", "Customer Care Intern",
    ])
    def test_other_roles(self, titre):
        assert not employer_scraper._is_finance_role(titre)
