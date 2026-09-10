# Newsletter Sciences Po Finance — Pipeline automatisé

Newsletter hebdomadaire semi-automatisée pour l'association Sciences Po Finance :
1. **Actus financières** — résumées automatiquement à partir de flux RSS/API
2. **Offres de stage** — scrapées automatiquement (JobTeaser, Welcome to the Jungle, pages carrières)
3. **Validation humaine** avant envoi, via une petite interface web
4. **Envoi** à la liste d'abonnés via Brevo

## Architecture

```
Sources (RSS/API actus + scraping stages)
        │
        ▼
Scraper hebdomadaire (cron)
        │
        ▼
Génération du brouillon (API Claude)
        │
        ▼
Interface web de validation (login + édition)
        │
        ▼
Envoi Brevo → Abonnés
```

Voir `PLAN.md` pour le détail technique de chaque brique.

## Stack

| Brique | Techno |
|---|---|
| Scraping actus | `feedparser` + NewsAPI |
| Scraping stages | `Playwright` |
| Génération de contenu | API Anthropic (Claude) |
| Base de données | SQLite (dev) / Postgres via Supabase (prod) |
| Interface de validation | FastAPI + Jinja2 (mono-page, pas de front lourd) |
| Envoi email | API Brevo |
| Orchestration | GitHub Actions (cron hebdomadaire) |

## Installation

```bash
git clone <repo>
cd spf-newsletter
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # puis remplir les clés
```

## Variables d'environnement (`.env`)

```
ANTHROPIC_API_KEY=
BREVO_API_KEY=
BREVO_LIST_ID=
NEWSAPI_KEY=
DATABASE_URL=sqlite:///./spf.db
APP_SECRET_KEY=
ALLOWED_REVIEWER_EMAILS=bureau@sciencespo.fr,president@sciencespo.fr
```

Ne jamais commit le fichier `.env` (déjà dans `.gitignore`).

## Lancer le pipeline manuellement

```bash
python scripts/run_weekly.py
```

Cela va : scraper les actus + les stages → générer le brouillon via Claude →
l'enregistrer en base avec le statut `pending_review`.

## Lancer l'interface de validation

```bash
uvicorn src.app.main:app --reload
```

Puis ouvrir `http://localhost:8000`, se connecter, relire/éditer le brouillon,
cliquer sur "Envoyer".

## Automatisation (GitHub Actions)

Le fichier `.github/workflows/weekly.yml` (à créer, voir `PLAN.md` §6) déclenche
`scripts/run_weekly.py` tous les lundis à 8h. Il ne fait QUE générer le brouillon
et le mettre en attente — jamais l'envoi automatique.

## Structure du projet

```
spf-newsletter/
├── src/
│   ├── scraper/
│   │   ├── news_scraper.py      # récupération actus (RSS/API)
│   │   └── stage_scraper.py     # récupération offres de stage
│   ├── ai/
│   │   └── draft_generator.py   # génération du brouillon via Claude
│   ├── app/
│   │   ├── main.py              # app FastAPI (auth + review + envoi)
│   │   └── templates/review.html
│   ├── email/
│   │   └── brevo_sender.py      # envoi via API Brevo
│   ├── db/
│   │   └── models.py            # modèles SQLAlchemy
│   └── config.py                # chargement des variables d'env
├── scripts/
│   └── run_weekly.py            # script exécuté par le cron
├── tests/
├── requirements.txt
├── .env.example
└── PLAN.md
```

## Légal / bonnes pratiques

- Lien de désabonnement obligatoire dans chaque email (Brevo le gère nativement)
- Ne pas scraper LinkedIn directement (violation des CGU) → privilégier JobTeaser/WTTJ/pages carrières
- Respecter les `robots.txt` des sites scrapés et limiter la fréquence des requêtes
