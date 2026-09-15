import pytest

from src.scraper import sectors


def offre(company="", title="", url=""):
    return {"company": company, "title": title, "url": url}


class TestClassifyByEmployer:
    @pytest.mark.parametrize("nom,attendu", [
        ("BNP Paribas", sectors.BANQUE),
        ("Société Générale", sectors.BANQUE),
        ("Rothschild & Co", sectors.CONSEIL_MA),
        ("Clipperton", sectors.CONSEIL_MA),
        ("Ardian", sectors.PRIVATE_EQUITY),
        ("Eurazeo", sectors.PRIVATE_EQUITY),
        ("Amundi", sectors.GESTION),
        ("Deloitte", sectors.AUDIT),
        ("Forvis Mazars", sectors.AUDIT),
        ("Qonto", sectors.FINTECH),
        ("Euronext", sectors.INSTITUTIONS),
        ("AXA", sectors.ASSURANCE),
        ("Coface", sectors.ASSURANCE),
    ])
    def test_known_employers(self, nom, attendu):
        assert sectors.classify(offre(company=nom)) == attendu

    def test_the_employer_wins_over_the_title(self):
        # Un audit chez Ardian reste du private equity.
        assert sectors.classify(
            offre(company="Ardian", title="Stage audit interne")
        ) == sectors.PRIVATE_EQUITY

    def test_accents_and_case_do_not_matter(self):
        assert sectors.classify(offre(company="SOCIÉTÉ GÉNÉRALE")) == sectors.BANQUE

    def test_the_url_can_identify_the_employer(self):
        assert sectors.classify(
            offre(url="https://w.com/fr/companies/credit-agricole-cib/jobs/x")
        ) == sectors.BANQUE


class TestClassifyByTitle:
    @pytest.mark.parametrize("titre,attendu", [
        ("Stage M&A janvier 2027", sectors.CONSEIL_MA),
        ("Global Investment Banking ECM internship", sectors.CONSEIL_MA),
        ("Stage analyste private equity", sectors.PRIVATE_EQUITY),
        ("Stage gestion de portefeuille", sectors.GESTION),
        ("Auditeur financier stagiaire", sectors.AUDIT),
        ("Stage assistant banquier privé", sectors.BANQUE),
        ("Stage contrôle de gestion", sectors.CORPORATE),
        ("Chargé d'études actuarielles", sectors.ASSURANCE),
    ])
    def test_titles(self, titre, attendu):
        assert sectors.classify(offre(title=titre)) == attendu

    def test_private_equity_wins_over_generic_investment(self):
        # « investissement » seul enverrait ailleurs si l'ordre était inversé.
        assert sectors.classify(
            offre(title="Stage investissement private equity")
        ) == sectors.PRIVATE_EQUITY

    def test_an_unrecognised_offer_is_not_forced(self):
        assert sectors.classify(offre(title="Stage graphisme")) == sectors.AUTRES

    def test_empty_offer(self):
        assert sectors.classify(offre()) == sectors.AUTRES


class TestGrouping:
    def test_follows_the_editorial_order(self):
        groupes = sectors.group_by_sector([
            offre(company="Deloitte", title="Audit"),
            offre(company="Rothschild & Co", title="M&A"),
            offre(company="Ardian", title="PE"),
        ])
        assert [g["secteur"] for g in groupes] == [
            sectors.CONSEIL_MA, sectors.PRIVATE_EQUITY, sectors.AUDIT
        ]

    def test_empty_sectors_are_absent(self):
        groupes = sectors.group_by_sector([offre(company="Ardian")])
        assert len(groupes) == 1

    def test_every_offer_is_kept(self):
        offres = [offre(company="BNP Paribas"), offre(title="???"), offre(company="Amundi")]
        total = sum(len(g["offres"]) for g in sectors.group_by_sector(offres))
        assert total == 3

    def test_no_offers_at_all(self):
        assert sectors.group_by_sector([]) == []
