"""Récupération des offres de stage — voir PLAN.md §2.

IMPORTANT : ne jamais scraper LinkedIn directement (violation des CGU, risque
de bannissement). Cibler JobTeaser, Welcome to the Jungle, pages carrières.
"""
import asyncio
from playwright.async_api import async_playwright

KEYWORDS = ["stage", "finance", "m&a", "gestion d'actifs", "marchés de capitaux"]


async def fetch_stage_offers(target_sites: list[dict]) -> list[dict]:
    """
    target_sites: [{"name": "jobteaser", "url": "..."}, ...]
    Retourne : [{"title", "company", "location", "deadline", "url"}]
    """
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        for site in target_sites:
            offers = await _scrape_site(page, site)
            results.extend(offers)
            await asyncio.sleep(2)  # politesse — éviter de spammer le site
        await browser.close()
    return results


async def _scrape_site(page, site: dict) -> list[dict]:
    await page.goto(site["url"])
    # TODO: sélecteurs CSS spécifiques à chaque site — à écrire une fois
    # la structure HTML de JobTeaser/WTTJ inspectée manuellement
    # ex: cards = await page.query_selector_all(".job-card")
    return []


def run_fetch_stage_offers(target_sites: list[dict]) -> list[dict]:
    """Wrapper synchrone pour appel depuis un script non-async."""
    return asyncio.run(fetch_stage_offers(target_sites))
