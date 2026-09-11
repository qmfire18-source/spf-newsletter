"""Inspecte la sélection des offres de stage, sans toucher à la base ni à l'IA.

Deux modes, selon ce qu'on veut regarder :

    python scripts/preview_stages.py --plan
        Ne lit que les sitemaps et affiche le vivier retenu puis l'ordre de
        visite. Ne visite aucune page d'offre : c'est le mode à utiliser quand
        WTTJ nous limite, puisque les sitemaps, eux, répondent toujours.

    python scripts/preview_stages.py
        Exécution réelle : visite les pages jusqu'à la limitation du site et
        affiche les offres effectivement extraites.
"""
import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.config import STAGE_SOURCES
from src.scraper import stage_scraper as scraper


async def _collect_candidates(site: dict) -> tuple[list, list]:
    """Retourne (tous les candidats du sitemap, ceux retenus par le filtre)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    async with httpx.AsyncClient(
        headers={"User-Agent": scraper.USER_AGENT},
        timeout=scraper.REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        shards = await scraper._wttj_job_shards(client, site["sitemap_index"])
        seen, kept = [], []
        for shard in shards:
            await asyncio.sleep(scraper.REQUEST_DELAY_SECONDS)
            text = scraper._decode(await scraper._get(client, shard))
            for url, lastmod in scraper._SITEMAP_ENTRY_RE.findall(text):
                modified = scraper._parse_datetime(lastmod)
                if not (modified and modified >= cutoff):
                    continue
                seen.append(url)
                if scraper._looks_like_finance_stage(url):
                    kept.append((modified, url))
    return seen, kept


def _show_plan(site: dict) -> None:
    seen, kept = asyncio.run(_collect_candidates(site))
    print(f"\nOffres publiées sur 7 jours : {len(seen)}")
    print(f"Retenues par le filtre finance/France : {len(kept)}")

    ordered = scraper._prioritise(kept)
    employers = Counter(scraper._company_slug(u) for u in ordered)
    print(f"Employeurs représentés : {len(employers)}")
    print(
        f"\nOrdre de visite (le site nous coupe en pratique vers la 6e page, "
        f"plafond {scraper.MAX_OFFERS}) :"
    )
    for rank, url in enumerate(ordered[: scraper.MAX_OFFERS], 1):
        company = scraper._company_slug(url)
        slug = url.split("/jobs/")[-1]
        marker = "→" if rank <= 6 else " "
        print(f" {marker} {rank:2d}. {company:32.32s} {slug[:58]}")

    print("\nEmployeurs les plus présents dans le vivier :")
    for company, count in employers.most_common(8):
        print(f"    {count:3d}  {company}")


def _show_offers(sources: list[dict]) -> None:
    offers = scraper.run_fetch_stage_offers(sources)
    print(f"\n{len(offers)} offre(s) extraite(s) :\n")
    for offer in offers:
        deadline = offer["deadline"] or "sans date limite"
        print(f"  {offer['title']}")
        print(f"    {offer['company']} — {offer['location']} — {deadline}")
        print(f"    {offer['url']}\n")
    if not offers:
        print("  (aucune — voir les avertissements ci-dessus)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        action="store_true",
        help="n'interroger que les sitemaps et montrer l'ordre de visite",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.plan:
        for site in STAGE_SOURCES:
            print(f"=== {site['name']} — plan de visite ===")
            _show_plan(site)
    else:
        _show_offers(STAGE_SOURCES)


if __name__ == "__main__":
    main()
