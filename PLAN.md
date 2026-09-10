# Plan technique détaillé

## 0. Modèle de données (`src/db/models.py`)

```python
class Draft(Base):
    id: int
    week_of: date
    news_content: str        # markdown généré par l'IA
    stages_content: str      # markdown généré par l'IA
    status: str              # "pending_review" | "approved" | "sent"
    created_at: datetime
    reviewed_by: str | None
    sent_at: datetime | None

class NewsItem(Base):
    id: int
    draft_id: int (FK)
    title: str
    source: str
    url: str
    raw_summary: str

class StageOffer(Base):
    id: int
    draft_id: int (FK)
    title: str
    company: str
    location: str
    deadline: date | None
    url: str
```

## 1. `src/scraper/news_scraper.py`

**Fonction principale** : `fetch_news(sources: list[str]) -> list[NewsItem]`

- Utiliser `feedparser` pour les flux RSS (Les Echos, Zonebourse, Boursorama...)
- Compléter avec l'API NewsAPI filtrée par mots-clés (`finance`, `marchés`, `M&A`, `banque centrale`)
- Dédupliquer par URL/titre similaire (`difflib` ou fuzzy matching simple)
- Retourner une liste d'objets bruts (titre, source, url, extrait) — **pas de résumé ici**, ça c'est le rôle de l'IA

```python
def fetch_news(sources: list[dict]) -> list[dict]:
    """
    sources: [{"type": "rss", "url": "..."}, {"type": "newsapi", "query": "..."}]
    Retourne une liste de dicts bruts, dédupliqués, limités aux 7 derniers jours.
    """
```

## 2. `src/scraper/stage_scraper.py`

**Fonction principale** : `fetch_stage_offers(sources: list[str]) -> list[StageOffer]`

- `Playwright` en mode headless pour JobTeaser / Welcome to the Jungle / pages carrières
- Filtrer par mots-clés : "stage", "finance", "M&A", "gestion d'actifs", "marchés de capitaux"
- **Ne pas scraper LinkedIn** (violation CGU, risque de ban)
- Respecter un délai entre requêtes (`time.sleep` ou `asyncio.sleep`) pour rester raisonnable
- Retourner titre, entreprise, lieu, deadline si disponible, lien

```python
async def fetch_stage_offers(target_sites: list[dict]) -> list[dict]:
    """
    Lance Playwright, visite chaque site cible, extrait les offres correspondant
    aux mots-clés finance. Retourne une liste de dicts.
    """
```

## 3. `src/ai/draft_generator.py`

**Fonction principale** : `generate_draft(news: list[dict], stages: list[dict]) -> dict`

- Appel à l'API Claude (`anthropic` SDK Python)
- Prompt structuré avec :
  - Charte éditoriale fixe (ton, longueur, structure) donnée en system prompt
  - Les news brutes + les offres de stage en input
  - Format de sortie demandé : JSON strict `{"news_html": "...", "stages_html": "..."}`
- Parser la réponse JSON, gérer les erreurs de parsing (retry une fois si échec)

```python
def generate_draft(news_items: list[dict], stage_items: list[dict]) -> dict:
    """
    Construit le prompt, appelle l'API Claude, parse la réponse JSON.
    Retourne {"news_html": str, "stages_html": str}.
    Lève DraftGenerationError si le parsing échoue après retry.
    """
```

Exemple de structure de prompt (system) :
```
Tu es le rédacteur de la newsletter hebdomadaire de Sciences Po Finance.
Ton : professionnel, concis, accessible à des étudiants.
Structure attendue : 3-5 actus max, 1-2 phrases chacune, puis une section
"Stages de la semaine" avec titre/entreprise/deadline/lien pour chaque offre.
Réponds UNIQUEMENT en JSON valide, sans texte autour.
```

## 4. `src/app/main.py` — interface de validation

- FastAPI + Jinja2, pas de framework front lourd
- Auth simple : liste blanche d'emails (`ALLOWED_REVIEWER_EMAILS`) + Google OAuth, ou à défaut un mot de passe partagé + session cookie signé
- Routes :
  - `GET /login`
  - `GET /` → affiche le dernier `Draft` en statut `pending_review`, éditable (textarea ou éditeur markdown simple)
  - `POST /draft/{id}/save` → sauvegarde les éditions
  - `POST /draft/{id}/send` → appelle `brevo_sender.send_campaign()`, passe le statut à `sent`
- Protéger toutes les routes sauf `/login` avec un middleware de vérification de session

## 5. `src/email/brevo_sender.py`

**Fonction principale** : `send_campaign(subject: str, html_content: str) -> str`

- Utiliser l'API Brevo (`sib-api-v3-sdk` ou requêtes HTTP directes)
- Créer une campagne, l'associer à la liste d'abonnés (`BREVO_LIST_ID`), puis la déclencher
- Le lien de désabonnement est géré automatiquement par Brevo — ne pas y toucher

```python
def send_campaign(subject: str, html_content: str, list_id: int) -> str:
    """
    Crée et envoie une campagne Brevo. Retourne l'ID de la campagne créée.
    """
```

## 6. Orchestration (`scripts/run_weekly.py` + GitHub Actions)

```python
def main():
    news = fetch_news(NEWS_SOURCES)
    stages = fetch_stage_offers(STAGE_SOURCES)
    draft = generate_draft(news, stages)
    save_draft_to_db(draft, status="pending_review")
    # Optionnel : notifier le bureau (email/Slack) que le brouillon est prêt
```

`.github/workflows/weekly.yml` :
```yaml
on:
  schedule:
    - cron: '0 7 * * 1'  # lundi 8h Paris (UTC+1)
  workflow_dispatch: {}
jobs:
  generate-draft:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r requirements.txt && playwright install chromium
      - run: python scripts/run_weekly.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          NEWSAPI_KEY: ${{ secrets.NEWSAPI_KEY }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

**Important** : ce workflow ne fait que générer le brouillon. L'envoi reste manuel, déclenché depuis l'interface web par un humain.

## 7. Ordre de développement conseillé

1. `src/db/models.py` — poser le schéma
2. `src/scraper/news_scraper.py` — le plus simple, RSS d'abord
3. `src/ai/draft_generator.py` — brancher l'API Claude, tester sur des news statiques
4. `src/scraper/stage_scraper.py` — plus long à cause du scraping dynamique
5. `src/app/main.py` — interface de validation
6. `src/email/brevo_sender.py` — dernier maillon, teste en dernier avec un envoi à toi-même
7. `scripts/run_weekly.py` + GitHub Actions — une fois tout le reste validé manuellement

## 8. Points d'attention

- **Rate limiting scraping** : espacer les requêtes, mettre un `User-Agent` correct, respecter `robots.txt`
- **Coûts API Claude** : le prompt de génération est appelé 1x/semaine, coût négligeable
- **Sécurité de l'interface** : même simple, ne jamais exposer de bouton "envoyer" sans auth
- **Idempotence** : si le cron tourne deux fois, ne pas dupliquer un `Draft` pour la même semaine (vérifier `week_of` avant insertion)
