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

## Générer le brouillon sans clé API

Le CLI Claude Code installé sur la machine sert de moteur de rédaction, avec
les identifiants de l'abonnement — sans clé API ni frais supplémentaires :

```bash
python scripts/run_weekly.py --generator local
```

Sans `--generator`, le moteur est choisi tout seul : l'API si
`ANTHROPIC_API_KEY` est renseignée, le CLI local sinon. Le CLI est cherché dans
le `PATH`, puis dans les extensions VS Code.

**Ce moteur ne convient pas au cron GitHub Actions** : il exige une session
Claude Code authentifiée, que le runner n'a pas. Pour une exécution
automatique, voir la programmation locale ci-dessous.

## Collecte des offres de stage

WTTJ coupe le scraper après quelques pages : une visite unique ne ramène que
six offres sur les ~190 du vivier hebdomadaire. La collecte est donc
**incrémentale** — chaque exécution écarte ce qui est déjà en stock et va
chercher du nouveau :

```bash
python scripts/collect_stages.py
```

Les offres s'accumulent dans la table `collected_offers` et la newsletter puise
dans les sept derniers jours. Trois écartées d'office : celle dont la date
limite est passée, celle déjà parue dans une édition précédente, et — au-delà
de 30 jours — celles purgées du stock.

**Pas de répétition d'une semaine sur l'autre** : actualités comme offres, tout
ce qui figure dans une édition antérieure est exclu de la suivante. L'édition
en cours ne se bloque pas elle-même, donc régénérer le brouillon de la semaine
reste possible.

Lancée chaque jour, la collecte porte le stock d'environ 6 offres à une
quarantaine — sans jamais forcer la limitation du site, au contraire : sept
petites visites la ménagent davantage qu'une grosse.

## Génération automatique chaque lundi (sans clé API)

```bash
./scripts/install_schedule.sh            # lundi 10h30 + collecte quotidienne
./scripts/install_schedule.sh --a 9:15   # autre heure pour la newsletter
./scripts/install_schedule.sh --retirer  # désactiver
```

Deux LaunchAgents sont installés : la collecte d'offres chaque jour à 7h15, et
la newsletter le lundi à 10h30. Le second lance `scripts/weekly_local.sh`, qui génère le brouillon
avec le CLI local, journalise dans `logs/weekly.log` et affiche une
notification. **L'envoi n'est jamais automatique** : il reste déclenché à la
main depuis l'interface de validation.

launchd rattrape un rendez-vous manqué au réveil : si le Mac dort le lundi à
8h, la génération part au réveil plutôt que d'être sautée. En revanche, elle ne
tourne pas si la machine est éteinte toute la journée — c'est la limite de
cette approche par rapport au cron GitHub Actions, qui lui demande une clé API.

Vérifier ou déclencher à la main :

```bash
launchctl print gui/$(id -u)/com.sciencespofinance.newsletter | head -20
launchctl kickstart -p gui/$(id -u)/com.sciencespofinance.newsletter
```

## Mode manuel (copier-coller)

L'appel à l'API Claude coûte environ 0,16 $ par édition (~8 $/an). Si tu
préfères ne rien payer, la génération étant hebdomadaire et de toute façon
relue à la main, elle peut passer par une conversation Claude ordinaire :

```bash
python scripts/export_prompt.py          # écrit brouillon_prompt.txt
# coller son contenu dans Claude.ai, enregistrer la réponse JSON
python scripts/import_draft.py reponse.json
```

`export_prompt.py` produit **exactement** ce que `generate_draft` enverrait :
même charte, mêmes données, même séparation entre actualités développables et
brèves. `import_draft.py` accepte une réponse bavarde (bloc Markdown, phrase
d'introduction) et applique le même nettoyage HTML que la voie automatique.

Le seul renoncement est l'automatisation : le cron du lundi ne peut pas faire
cette étape à ta place.

## Générer le brouillon sans clé API

La génération est hebdomadaire et relue à la main : rien n'oblige à passer par
l'API. Le mode manuel produit exactement le prompt que `generate_draft`
enverrait, à coller dans une conversation Claude.ai — coût nul.

```bash
python scripts/export_prompt.py          # écrit brouillon_prompt.txt
# coller son contenu dans Claude.ai, enregistrer la réponse dans reponse.json
python scripts/import_draft.py reponse.json
```

`import_draft.py` accepte une réponse bavarde : le JSON peut être entouré de
texte ou d'un bloc de code Markdown. Le HTML passe par le même nettoyage que la
génération automatique. `--remplacer` écrase le brouillon de la semaine.

Ce mode remplace l'étape IA, pas le reste : le scraping et l'interface de
validation sont identiques. En revanche il ne peut pas tourner dans le cron
GitHub Actions, qui n'a personne pour copier-coller.

## Page d'abonnement publique

`docs/index.html` est publiée par GitHub Pages sur
<https://qmfire18-source.github.io/spf-newsletter/> — URL stable, gratuite,
accessible même quand la machine qui génère la newsletter est éteinte. Un
`git push` suffit à la mettre à jour.

Le formulaire est inactif tant que le compte Brevo n'existe pas : son `action`
vaut `REMPLACER_PAR_URL_BREVO`. Brevo fournit une URL de formulaire hébergé qui
gère l'inscription, la confirmation et le désabonnement — c'est elle qu'il faut
coller là.

## Partager l'interface de validation avec le bureau

```bash
./scripts/partager.sh
```

Démarre le serveur si besoin, puis un tunnel qui lui donne une adresse HTTPS
publique à transmettre au bureau. Sans les identifiants, personne ne peut lire
ni envoyer le brouillon.

Le script tente Cloudflare, puis retombe sur un tunnel SSH via `localhost.run`.
Cette bascule n'est pas théorique : **Cloudflare passe par le port 7844, bloqué
sur beaucoup de réseaux d'école et d'entreprise** — c'était le cas ici. Le
tunnel SSH n'utilise que le port 22 et ne demande rien à installer.

Deux limites à connaître :

- **La machine doit rester allumée**, avec le tunnel lancé. `Ctrl+C` referme
  l'accès extérieur immédiatement.
- **L'adresse change à chaque lancement** (tunnels gratuits, sans compte). Une
  URL fixe suppose un compte et un nom de domaine.

Une fois l'interface exposée, passer `COOKIE_SECURE=true` dans le `.env` :
`http://localhost` cessera de fonctionner, mais le cookie de session ne
circulera plus qu'en HTTPS. Le blocage après cinq échecs de connexion
s'applique par IP réelle, l'en-tête du tunnel étant lu — mais uniquement quand
la requête vient de la machine elle-même, pour qu'il ne soit pas falsifiable.

## Lancer l'interface de validation

```bash
uvicorn src.app.main:app --reload
```

Puis ouvrir `http://localhost:8000`, se connecter avec un email de
`ALLOWED_REVIEWER_EMAILS` et le mot de passe du bureau, relire/éditer le
brouillon, puis « Valider et envoyer aux abonnés ».

Le bouton « Voir l'email » ouvre le brouillon dans son enveloppe complète —
en-tête, blason, pied de page — tel qu'il arrivera dans une boîte mail.
L'interface de relecture ne montre que les fragments ; c'est la dernière chose
à regarder avant d'envoyer. L'aperçu reste accessible après l'envoi et sert
alors d'archive.

Le bouton de validation **enregistre et expédie en une seule action** : ce qui
part est la version affichée à l'écran. Une confirmation est demandée avant l'envoi, qui
est irréversible. « Enregistrer sans envoyer » reste disponible pour reprendre
plus tard.

Le brouillon s'édite **directement dans le rendu** : gras, italique, lien,
sous-titre et liste sont dans la barre d'outils. Le bouton « HTML » ouvre la
source à côté pour les retouches fines ; les deux volets restent synchronisés.
Tout est renettoyé par `sanitize_html` à l'enregistrement, donc une balise
collée hors de la liste blanche est retirée.

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

## Format de la newsletter

La newsletter n'est pas une liste de liens : le lecteur doit comprendre la
semaine sans ouvrir un article. Chaque édition comporte

- **3 à 5 actualités développées** — titre d'accroche, deux à quatre
  paragraphes, les chiffres, et pourquoi ça compte pour un étudiant en
  finance ;
- une section **« En bref »** — les autres sujets, une phrase chacun ;
- les **stages de la semaine**.

Cela suppose le texte des articles : les flux RSS ne livrent qu'un résumé de
**86 caractères en médiane**, le plus souvent le titre répété. Sans lui, le
modèle ne pourrait qu'inventer des chiffres — ce que la charte interdit
formellement. `src/scraper/article_fetcher.py` va donc chercher le texte des
articles retenus, et le prompt sépare explicitement les actualités
développables (celles qui ont un `full_text`) de celles qui n'iront qu'en
brève.

**Ce qui limite le nombre d'items développés :**

| Cause | Effet |
|---|---|
| Liens Google News | URL chiffrée, redirigée vers un mur de consentement — illisible, et on ne le contourne pas |
| Pages rendues en JavaScript (Le Monde, BFMTV) | coquille vide ; on ne lance pas de navigateur |
| Paywalls | texte partiel |

En pratique, une semaine type donne **3 articles développables**. Les augmenter
suppose d'ajouter des flux RSS *directs* (les liens Google News ne mènent nulle
part) : les candidats testés le 2026-09-11 renvoyaient tous 403 ou 404.

`robots.txt` fait foi avant chaque lecture, par domaine, et un `robots.txt`
injoignable vaut refus.

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

## Logo de l'association

Dépose le blason dans `src/app/static/logo.png` (ou `.svg`, `.jpg`, `.webp`) :
l'interface le détecte au démarrage et l'affiche dans l'en-tête. Sans ce
fichier, un monogramme typographique « SPF » le remplace — le blason n'est
volontairement pas redessiné en SVG, une approximation valant moins que son
absence. Le blason actuel est versionné (`src/app/static/logo.jpeg`) ;
remplacer ce fichier suffit à changer l'identité de l'interface.
