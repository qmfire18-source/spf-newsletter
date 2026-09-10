"""Modèles SQLAlchemy — voir PLAN.md §0 pour le détail des champs."""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from src.config import DATABASE_URL

Base = declarative_base()


class Draft(Base):
    __tablename__ = "drafts"
    id = Column(Integer, primary_key=True)
    week_of = Column(Date, nullable=False)
    news_content = Column(Text)      # markdown/html généré par l'IA
    stages_content = Column(Text)    # markdown/html généré par l'IA
    status = Column(String, default="pending_review")  # pending_review | approved | sent
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_by = Column(String, nullable=True)
    sent_at = Column(DateTime, nullable=True)

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
    url = Column(String)

    draft = relationship("Draft", back_populates="stage_offers")


# FastAPI exécute les routes synchrones dans un pool de threads : sans
# check_same_thread=False, SQLite refuse la connexion ouverte dans un autre
# thread. Sans effet sur Postgres en production.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    Base.metadata.create_all(engine)
