"""Ajout de colonnes sur une base déjà remplie — voir _add_missing_columns."""
from sqlalchemy import create_engine, inspect, text

from src.db import models


def test_missing_columns_are_added_to_an_existing_table(tmp_path):
    # Une base créée avant l'ajout des colonnes de suivi d'envoi.
    chemin = tmp_path / "ancienne.db"
    engine = create_engine(f"sqlite:///{chemin}")
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE drafts (id INTEGER PRIMARY KEY, week_of DATE NOT NULL UNIQUE)"
        ))
        c.execute(text("INSERT INTO drafts (id, week_of) VALUES (1, '2026-09-07')"))

    models._add_missing_columns(engine)

    colonnes = {c["name"] for c in inspect(engine).get_columns("drafts")}
    for attendue in ("brevo_campaign_id", "brevo_list_id", "recipient_count",
                     "status", "sent_at", "reviewed_by"):
        assert attendue in colonnes

    # La ligne existante survit.
    with engine.begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM drafts")).scalar() == 1


def test_running_twice_is_harmless(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    models.Base.metadata.create_all(engine)
    models._add_missing_columns(engine)
    models._add_missing_columns(engine)


def test_an_empty_database_is_left_to_create_all(tmp_path):
    # Aucune table : rien à altérer, aucune erreur.
    models._add_missing_columns(create_engine(f"sqlite:///{tmp_path / 'vide.db'}"))
