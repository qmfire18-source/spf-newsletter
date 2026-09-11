# Newsletter Sciences Po Finance — Pipeline automatisé

Newsletter hebdomadaire semi-automatisée pour l'association Sciences Po Finance :
1. **Actus financières** — résumées automatiquement à partir de flux RSS
2. **Offres de stage** — récupérées sur Welcome to the Jungle via son sitemap
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
| Scraping actus | `feedparser` (RSS + Google News) |
| Scraping stages | `httpx` + JSON-LD (sitemap WTTJ) |
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
cp .env.example .env  # puis remplir les clés
```

## Variables d'environnement (`.env`)

Voir `.env.example` pour la liste complète et commentée.

Génère d'un coup la clé de session et le mot de passe partagé du bureau :

```bash
python -m src.app.security
```

Colle les deux lignes obtenues (`APP_SECRET_KEY` et `REVIEWER_PASSWORD_HASH`)
dans le `.env`. Le mot de passe en clair n'est jamais stocké : seul son hash
PBKDF2 l'est.

`NEWSAPI_KEY` est facultative — la couche mots-clés passe par Google News RSS.
En développement local (HTTP), ajoute `COOKIE_SECURE=false`, sinon le cookie
de session ne sera pas renvoyé par le navigateur.

Ne jamais commit le fichier `.env` (déjà dans `.gitignore`).

## Lancer le pipeline manuellement

```bash
python scripts/run_weekly.py
```

Cela va : scraper les actus + les stages → générer le brouillon via Claude →
l'enregistrer en base avec le statut `pending_review`.

## Inspecter la sélection des stages

```bash
python scripts/preview_stages.py --plan   # vivier + ordre de visite (sitemaps seuls)
python scripts/preview_stages.py          # exécution réelle, affiche les offres
```

Le mode `--plan` ne visite aucune page d'offre : il reste utilisable quand WTTJ
limite le robot, puisque les sitemaps répondent toujours. C'est le moyen de
vérifier ce que le scraper ramènerait avant de dépenser des requêtes.

## Lancer l'interface de validation

```bash
uvicorn src.app.main:app --reload
```

Puis ouvrir `http://localhost:8000`, se connecter avec un email de
`ALLOWED_REVIEWER_EMAILS` et le mot de passe du bureau, relire/éditer le
brouillon, cliquer sur "Envoyer".

L'envoi est verrouillé : un brouillon déjà envoyé ne peut pas repartir, et un
échec Brevo le rend à nouveau modifiable.

## Tests

```bash
pytest
```

## Automatisation (GitHub Actions)

Le fichier `.github/workflows/weekly.yml` déclenche
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
- **LinkedIn n'est jamais scrapé** (violation des CGU)
- **JobTeaser non plus** : le site répond 403 à toute requête automatisée, y
  compris avec un User-Agent de navigateur complet. Le blocage est délibéré,
  le franchir supposerait de se faire passer pour un humain.
- Welcome to the Jungle est interrogé via le sitemap que son `robots.txt`
  publie. Ce `robots.txt` interdisant toute URL à query string, les pages de
  recherche filtrée ne sont pas utilisées.
- Délai de 3 s entre requêtes, User-Agent identifiant le robot avec une URL de
  contact, et arrêt immédiat de la boucle si le site signale une limitation.

## Sélection des actualités

Les flux économie généralistes ramènent beaucoup de hors-sujet et se répètent.
Trois garde-fous, mesurés sur une semaine réelle (342 actus collectées) :

1. **Préfiltre mots-clés finance**, puis **veto conso/société** — carburants,
   pouvoir d'achat, climat, chômage, logement résidentiel. Ces sujets passaient
   par des formules comme « financer le plan d'aide » ou « taux de chômage »,
   soit 27 % des actus retenues. Le macro (BCE, dette, déficit, croissance)
   reste dans le périmètre : c'est l'angle consommateur qui en sort.
2. **Rotation entre sources** — un flux unique occupait 27 % de la liste, et
   donc le haut de ce que voit l'IA. On alterne les rédactions, ce qui fait
   remonter Les Echos, L'Agefi et Option Finance.
3. **Un événement = un item**, imposé par le prompt de génération. Une semaine
   chargée voit le même sujet couvert par une dizaine d'articles ; les
   regrouper relève du jugement éditorial, pas d'un seuil de similarité.

## Sélection des offres de stage

Le sitemap WTTJ expose environ 400 offres stage/finance sur 7 jours, mais le
site nous limite après une poignée de pages. Le budget de requêtes est donc la
ressource rare, et c'est l'**ordre de visite** qui détermine la qualité de la
newsletter — pas le volume. Trois règles, toutes dans `stage_scraper.py` :

1. **Périmètre « cœur finance »** — M&A, banque, investissement, private
   equity, audit financier, gestion d'actifs, trading, risque, actuariat,
   patrimoine. La comptabilité, le contrôle de gestion et la fiscalité en sont
   absents : les inclure triplait le vivier sans servir la ligne éditoriale.
   Une liste d'exclusion écarte les postes IT et RH que leur slug vend comme
   financiers (« développeur services financiers », « analyste fonctionnel SI
   finance », « capital humain »).
2. **France uniquement** — filtrée sur la ville côté slug, puis confirmée par
   le champ `addressCountry` du JSON-LD. La langue de l'annonce n'est *pas* un
   critère : les meilleures offres parisiennes du vivier (Naxicap, Clipperton,
   iBanFirst) sont publiées en anglais.
3. **Rotation entre employeurs** — trois employeurs pèsent à eux seuls un
   tiers du sitemap. On tourne entre entreprises plutôt que de trier par date,
   ce qui garantit autant d'employeurs différents que d'offres récoltées.
