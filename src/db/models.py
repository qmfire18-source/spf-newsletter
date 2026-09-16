"""Modèles SQLAlchemy — voir PLAN.md §0 pour le détail des champs."""
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from src.config import DATABASE_URL

Base = declarative_base()


def utcnow() -> datetime:
    """UTC sans fuseau : les colonnes DateTime ci-dessous sont naïves.

    Remplace datetime.utcnow(), déprécié depuis Python 3.12.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Draft(Base):
    __tablename__ = "drafts"
    id = Column(Integer, primary_key=True)
    # Unique : l'idempotence du cron ne peut pas reposer sur un simple SELECT
    # suivi d'un INSERT, deux exécutions concurrentes passant toutes deux le
    # test. C'est la base qui refuse le doublon.
    week_of = Column(Date, nullable=False, unique=True)
    news_content = Column(Text)      # markdown/html généré par l'IA
    stages_content = Column(Text)    # markdown/html généré par l'IA
    status = Column(String, default="pending_review")  # pending_review | approved | sent
    created_at = Column(DateTime, default=utcnow)
    reviewed_by = Column(String, nullable=True)
    sent_at = Column(DateTime, nullable=True)

    # Trace de l'envoi, pour que l'historique dise à qui et avec quoi.
    brevo_campaign_id = Column(String, nullable=True)
    brevo_list_id = Column(Integer, nullable=True)
    recipient_count = Column(Integer, nullable=True)

    news_items = relationship("NewsItem", back_populates="draft")
    stage_offers = relationship("StageOffer", back_populates="draft")


class NewsItem(Base):
    __tablename__ = "news_items"
    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("drafts.id"))
    title = Column(String)
    source = Column(String)
    url = Column(String)
    raw_summary = Column(Text)

    draft = relationship("Draft", back_populates="news_items")


class StageOffer(Base):
    __tablename__ = "stage_offers"
    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("drafts.id"))
    title = Column(String)
    company = Column(String)
    location = Column(String)
    deadline = Column(Date, nullable=True)
    duration = Column(String, nullable=True)
    start_label = Column(String, nullable=True)
    url = Column(String)

    draft = relationship("Draft", back_populates="stage_offers")


class CollectedOffer(Base):
    """Stock d'offres accumulé au fil des jours, indépendant des brouillons.

    WTTJ nous limite à une poignée de pages par exécution : une seule visite
    hebdomadaire ne ramènerait que six offres sur les ~190 disponibles. On
    collecte donc un peu chaque jour et la newsletter puise dans ce stock.
    L'URL est unique : une offre déjà connue n'est ni revisitée ni dupliquée.
    """

    __tablename__ = "collected_offers"
    id = Column(Integer, primary_key=True)
    url = Column(String, nullable=False, unique=True)
    title = Column(String)
    company = Column(String)
    location = Column(String)
    deadline = Column(Date, nullable=True)
    # Ni la durée ni le début ne figurent dans le JSON-LD : ils sont extraits du
    # texte de l'annonce, qu'on télécharge déjà. Souvent absents, d'où le NULL.
    duration = Column(String, nullable=True)
    start_label = Column(String, nullable=True)
    # Marque une offre dont on a déjà lu le texte, qu'on y ait trouvé une durée
    # ou non : sans elle, les offres muettes seraient revisitées à chaque
    # passage et consommeraient le quota de pages pour rien.
    details_checked_at = Column(DateTime, nullable=True)
    collected_at = Column(DateTime, default=utcnow, nullable=False)


def _url_normalisee(url: str) -> str:
    """Dirige Postgres vers psycopg 3, le seul pilote installé.

    Les hébergeurs distribuent leur chaîne de connexion en `postgresql://`,
    que SQLAlchemy confie par défaut à psycopg2, absent du projet : sans ce
    préfixe explicite, l'application échoue au démarrage sur un pilote
    manquant, et le message n'aide pas à comprendre pourquoi.
    """
    if url.startswith("postgres://"):  # forme héritée, encore servie par certains
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# FastAPI exécute les routes synchrones dans un pool de threads : sans
# check_same_thread=False, SQLite refuse la connexion ouverte dans un autre
# thread. Sans effet sur Postgres en production.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
# pool_pre_ping : l'offre gratuite coupe les connexions inactives, et sans lui
# la première requête après une mise en veille échoue au lieu de se reconnecter.
_options = {} if DATABASE_URL.startswith("sqlite") else {"pool_pre_ping": True}
engine = create_engine(_url_normalisee(DATABASE_URL), connect_args=_connect_args, **_options)
SessionLocal = sessionmaker(bind=engine)


def _add_missing_columns(engine):
    """Ajoute les colonnes apparues après la création de la base.

    `create_all` ne touche pas aux tables existantes : sans ça, une base
    déjà remplie ignorerait les nouvelles colonnes et l'application
    échouerait à la première lecture. SQLite accepte un ALTER TABLE simple,
    ce qui suffit à un projet de cette taille.
    """
    from sqlalchemy import inspect, text

    inspecteur = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspecteur.get_table_names():
            continue
        presentes = {c["name"] for c in inspecteur.get_columns(table.name)}
        for colonne in table.columns:
            if colonne.name in presentes:
                continue
            type_sql = colonne.type.compile(engine.dialect)
            with engine.begin() as connexion:
                connexion.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {colonne.name} {type_sql}")
                )


def init_db():
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
