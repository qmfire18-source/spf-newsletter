from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app import main, security
from src.db.models import Base, Draft
from src.email import brevo_sender

REVIEWER = "bureau@sciencespo.fr"
PASSWORD = "un-mot-de-passe-du-bureau"


@pytest.fixture
def db_session(monkeypatch):
    # StaticPool + check_same_thread : la base en mémoire doit survivre au
    # passage dans le pool de threads de FastAPI.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    monkeypatch.setattr(main, "SessionLocal", lambda: session)
    # La session est partagée avec les requêtes : ne pas la fermer entre elles.
    monkeypatch.setattr(session, "close", lambda: None)
    yield session


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(main, "APP_SECRET_KEY", "cle-de-test")
    monkeypatch.setattr(main, "ALLOWED_REVIEWER_EMAILS", [REVIEWER])
    monkeypatch.setattr(main, "REVIEWER_PASSWORD_HASH", security.hash_password(PASSWORD))
    # TestClient parle en http:// : un cookie Secure ne serait jamais renvoyé.
    monkeypatch.setattr(main, "COOKIE_SECURE", False)
    security._failed_attempts.clear()


@pytest.fixture
def client(configured, db_session):
    return TestClient(main.app, follow_redirects=False)


@pytest.fixture
def draft(db_session):
    draft = Draft(
        week_of=date(2026, 9, 7),
        news_content="<p>Actu</p>",
        stages_content="<p>Stage</p>",
        status="pending_review",
        created_at=datetime(2026, 9, 7, 8, 0),
    )
    db_session.add(draft)
    db_session.commit()
    return draft


def login(client):
    return client.post("/login", data={"email": REVIEWER, "password": PASSWORD})


class TestPasswordHashing:
    def test_roundtrip(self):
        assert security.verify_password(PASSWORD, security.hash_password(PASSWORD))

    def test_rejects_wrong_password(self):
        assert not security.verify_password("faux", security.hash_password(PASSWORD))

    def test_hash_is_salted(self):
        assert security.hash_password(PASSWORD) != security.hash_password(PASSWORD)

    @pytest.mark.parametrize("bad", ["", "pas-un-hash", "md5$1$aa$bb", None])
    def test_rejects_malformed_hash(self, bad):
        assert not security.verify_password(PASSWORD, bad)


class TestAuthenticationRequired:
    @pytest.mark.parametrize("method,path", [
        ("get", "/"),
        ("post", "/draft/1/save"),
        ("post", "/draft/1/send"),
    ])
    def test_protected_routes_redirect_to_login(self, client, method, path):
        response = getattr(client, method)(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    def test_login_page_is_public(self, client):
        assert client.get("/login").status_code == 200

    def test_forged_cookie_is_rejected(self, client):
        client.cookies.set(main.SESSION_COOKIE_NAME, "valeur-inventee")
        assert client.get("/").headers["location"] == "/login"

    def test_cookie_signed_with_another_key_is_rejected(self, client, monkeypatch):
        from itsdangerous import URLSafeTimedSerializer

        forged = URLSafeTimedSerializer("mauvaise-cle", salt=main.SESSION_SALT)
        client.cookies.set(main.SESSION_COOKIE_NAME, forged.dumps(REVIEWER))
        assert client.get("/").headers["location"] == "/login"

    def test_expired_session_is_rejected(self, client, monkeypatch):
        login(client)
        monkeypatch.setattr(main, "SESSION_MAX_AGE_SECONDS", -1)
        assert client.get("/").headers["location"] == "/login"

    def test_email_removed_from_whitelist_loses_access(self, client, monkeypatch):
        login(client)
        monkeypatch.setattr(main, "ALLOWED_REVIEWER_EMAILS", [])
        assert client.get("/").headers["location"] == "/login"


class TestLogin:
    def test_valid_credentials_set_a_session_cookie(self, client):
        response = login(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert main.SESSION_COOKIE_NAME in response.cookies

    def test_cookie_is_httponly_and_samesite_strict(self, client):
        header = login(client).headers["set-cookie"].lower()
        assert "httponly" in header
        assert "samesite=strict" in header

    def test_wrong_password_is_refused(self, client):
        response = client.post(
            "/login", data={"email": REVIEWER, "password": "faux"}
        )
        assert main.SESSION_COOKIE_NAME not in response.cookies
        assert response.headers["location"].startswith("/login?")

    def test_email_outside_whitelist_is_refused(self, client):
        response = client.post(
            "/login", data={"email": "intrus@ailleurs.fr", "password": PASSWORD}
        )
        assert main.SESSION_COOKIE_NAME not in response.cookies

    def test_lockout_after_repeated_failures(self, client):
        for _ in range(security.MAX_FAILED_ATTEMPTS):
            client.post("/login", data={"email": REVIEWER, "password": "faux"})
        # Même le bon mot de passe est refusé pendant le verrouillage.
        response = login(client)
        assert main.SESSION_COOKIE_NAME not in response.cookies

    def test_logout_clears_the_session(self, client):
        login(client)
        client.post("/logout")
        assert client.get("/").headers["location"] == "/login"


class TestReviewPage:
    def test_shows_the_pending_draft(self, client, draft):
        login(client)
        body = client.get("/").text
        # La semaine est écrite en toutes lettres, comme dans l'email.
        assert "Semaine du 7 septembre 2026" in body
        assert "Actu" in body

    def test_ignores_already_sent_drafts(self, client, db_session, draft):
        draft.status = "sent"
        db_session.commit()
        login(client)
        assert "Aucun brouillon en attente" in client.get("/").text


class TestSaveDraft:
    def test_persists_edits(self, client, db_session, draft):
        login(client)
        client.post(
            f"/draft/{draft.id}/save",
            data={"news_content": "<p>Corrigé</p>", "stages_content": "<p>S</p>"},
        )
        db_session.refresh(draft)
        assert draft.news_content == "<p>Corrigé</p>"
        assert draft.reviewed_by == REVIEWER

    def test_strips_dangerous_markup(self, client, db_session, draft):
        login(client)
        client.post(
            f"/draft/{draft.id}/save",
            data={
                "news_content": "<p>ok</p><script>alert(1)</script>",
                "stages_content": "<img src=x onerror=alert(2)>",
            },
        )
        db_session.refresh(draft)
        assert "script" not in draft.news_content
        assert "onerror" not in draft.stages_content

    def test_unknown_draft_returns_404(self, client):
        login(client)
        response = client.post(
            "/draft/999/save",
            data={"news_content": "a", "stages_content": "b"},
        )
        assert response.status_code == 404


ENVOI = {"news_content": "<p>Actu relue</p>", "stages_content": "<p>Stage</p>"}


class TestSendDraft:
    def test_sends_and_marks_as_sent(self, client, db_session, draft, monkeypatch):
        sent = []
        monkeypatch.setattr(
            main, "send_campaign",
            lambda subject, html_content: sent.append((subject, html_content)) or "42",
        )
        login(client)
        client.post(f"/draft/{draft.id}/send", data=ENVOI)
        db_session.refresh(draft)
        assert draft.status == "sent"
        assert draft.sent_at is not None
        assert len(sent) == 1
        # Ce qui part est la version relue à l'écran, pas celle en base avant.
        assert "Actu relue" in sent[0][1]

    def test_second_send_does_not_call_brevo_again(
        self, client, db_session, draft, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            main, "send_campaign",
            lambda subject, html_content: calls.append(subject) or "42",
        )
        login(client)
        client.post(f"/draft/{draft.id}/send", data=ENVOI)
        client.post(f"/draft/{draft.id}/send", data=ENVOI)
        assert len(calls) == 1

    def test_failed_send_leaves_the_draft_editable(
        self, client, db_session, draft, monkeypatch
    ):
        def boom(subject, html_content):
            raise RuntimeError("Brevo indisponible")

        monkeypatch.setattr(main, "send_campaign", boom)
        login(client)
        response = client.post(f"/draft/{draft.id}/send", data=ENVOI)
        db_session.refresh(draft)
        assert draft.status == "pending_review"
        assert "error" in response.headers["location"]

    def test_send_requires_authentication(self, client, draft, monkeypatch):
        calls = []
        monkeypatch.setattr(
            main, "send_campaign", lambda **kw: calls.append(kw) or "42"
        )
        response = client.post(f"/draft/{draft.id}/send")
        assert response.headers["location"] == "/login"
        assert calls == []


class TestValidationSendsWhatIsOnScreen:
    def test_edits_are_saved_before_sending(self, client, db_session, draft, monkeypatch):
        sent = []
        monkeypatch.setattr(
            main, "send_campaign",
            lambda subject, html_content: sent.append(html_content) or "42",
        )
        login(client)
        client.post(
            f"/draft/{draft.id}/send",
            data={"news_content": "<p>Version corrigée</p>", "stages_content": "<p>S</p>"},
        )
        db_session.refresh(draft)
        assert draft.news_content == "<p>Version corrigée</p>"
        assert "Version corrigée" in sent[0]

    def test_edits_survive_a_failed_send(self, client, db_session, draft, monkeypatch):
        # L'envoi échoue, mais la relecture ne doit pas être perdue.
        def boom(subject, html_content):
            raise RuntimeError("Brevo indisponible")

        monkeypatch.setattr(main, "send_campaign", boom)
        login(client)
        client.post(
            f"/draft/{draft.id}/send",
            data={"news_content": "<p>À conserver</p>", "stages_content": "<p>S</p>"},
        )
        db_session.refresh(draft)
        assert draft.news_content == "<p>À conserver</p>"
        assert draft.status == "pending_review"

    def test_dangerous_html_is_stripped_on_send(self, client, db_session, draft, monkeypatch):
        sent = []
        monkeypatch.setattr(
            main, "send_campaign",
            lambda subject, html_content: sent.append(html_content) or "42",
        )
        login(client)
        client.post(
            f"/draft/{draft.id}/send",
            data={
                "news_content": '<p>ok</p><script>alert(1)</script>',
                "stages_content": "<p>S</p>",
            },
        )
        assert "script" not in sent[0]


class TestEmailPreview:
    def test_shows_the_draft_inside_the_mail_wrapper(self, client, draft):
        login(client)
        page = client.get(f"/draft/{draft.id}/apercu")
        assert page.status_code == 200
        assert "Sciences Po Finance" in page.text
        assert brevo_sender.LOGO_URL in page.text
        assert draft.news_content in page.text

    def test_requires_authentication(self, client, draft):
        response = client.get(f"/draft/{draft.id}/apercu")
        assert response.headers["location"] == "/login"

    def test_unknown_draft_is_a_404(self, client):
        login(client)
        assert client.get("/draft/9999/apercu").status_code == 404

    def test_a_sent_draft_can_still_be_reviewed(self, client, db_session, draft):
        # L'aperçu sert aussi d'archive de ce qui est parti.
        draft.status = "sent"
        db_session.commit()
        login(client)
        assert client.get(f"/draft/{draft.id}/apercu").status_code == 200


class TestReviewPageTooling:
    def test_offers_a_summary_to_jump_between_sections(self, client, draft):
        # La page fait plusieurs écrans : les stages sont tout en bas.
        login(client)
        page = client.get("/").text
        assert 'href="#bloc-actus"' in page
        assert 'href="#bloc-stages"' in page
        assert 'id="bloc-stages"' in page

    def test_links_to_the_email_preview(self, client, draft):
        login(client)
        assert f"/draft/{draft.id}/apercu" in client.get("/").text

    def test_shows_live_counters(self, client, draft):
        login(client)
        page = client.get("/").text
        for compteur in ("stat-items", "stat-offres", "stat-mots", "stat-taille"):
            assert compteur in page

    def test_the_preview_is_capped_at_the_email_width(self, client, draft):
        # 34rem = la colonne réelle de l'email (600 px moins 2 × 32 de marge).
        # Relire plus large donne des coupures de ligne qui n'existent pas
        # dans la boîte de réception.
        login(client)
        assert "max-width:34rem" in client.get("/").text

    def test_keeps_a_local_copy_scoped_to_the_draft(self, client, draft):
        login(client)
        assert f'"spf-brouillon-{draft.id}"' in client.get("/").text


class TestHistory:
    def test_requires_authentication(self, client):
        assert client.get("/historique").headers["location"] == "/login"

    def test_lists_every_edition(self, client, db_session, draft):
        from datetime import date as _date
        ancienne = Draft(week_of=_date(2026, 8, 31), news_content="<p>A</p>",
                         stages_content="<p>S</p>", status="sent",
                         created_at=datetime(2026, 8, 31, 9, 0),
                         sent_at=datetime(2026, 8, 31, 11, 0),
                         reviewed_by="bureau@sciencespo.fr",
                         brevo_campaign_id="42", brevo_list_id=4, recipient_count=87)
        db_session.add(ancienne)
        db_session.commit()

        login(client)
        page = client.get("/historique").text
        assert "Semaine du 31 août 2026" in page
        assert "Semaine du 7 septembre 2026" in page

    def test_shows_who_what_and_when_for_a_sent_edition(self, client, db_session, draft):
        draft.status = "sent"
        draft.sent_at = datetime(2026, 9, 8, 10, 30)
        draft.reviewed_by = "bureau@sciencespo.fr"
        draft.recipient_count = 87
        draft.brevo_list_id = 4
        db_session.commit()

        login(client)
        page = client.get("/historique").text
        assert "Envoyée" in page
        assert "08/09/2026 à 10:30" in page
        assert "87" in page
        assert "bureau@sciencespo.fr" in page

    def test_links_to_the_archived_content(self, client, db_session, draft):
        draft.status = "sent"
        db_session.commit()
        login(client)
        assert f"/draft/{draft.id}/apercu" in client.get("/historique").text

    def test_a_pending_edition_is_marked_as_such(self, client, draft):
        login(client)
        assert "En attente" in client.get("/historique").text

    def test_empty_history(self, client, db_session):
        login(client)
        assert "Aucune édition" in client.get("/historique").text


class TestSendRecordsTheDelivery:
    def test_campaign_recipients_and_list_are_kept(
        self, client, db_session, draft, monkeypatch
    ):
        monkeypatch.setattr(main, "send_campaign", lambda subject, html_content: "99")
        monkeypatch.setattr(main, "count_recipients", lambda: 87)
        monkeypatch.setattr(main, "BREVO_LIST_ID", "4")
        login(client)
        client.post(f"/draft/{draft.id}/send", data=ENVOI)

        db_session.refresh(draft)
        assert draft.brevo_campaign_id == "99"
        assert draft.recipient_count == 87
        assert draft.brevo_list_id == 4
        assert draft.sent_at is not None

    def test_a_failed_send_records_nothing(self, client, db_session, draft, monkeypatch):
        def boom(subject, html_content):
            raise RuntimeError("Brevo indisponible")

        monkeypatch.setattr(main, "send_campaign", boom)
        login(client)
        client.post(f"/draft/{draft.id}/send", data=ENVOI)

        db_session.refresh(draft)
        assert draft.brevo_campaign_id is None
        assert draft.recipient_count is None
        assert draft.status == "pending_review"


class TestRegenerate:
    def test_requires_authentication(self, client):
        assert client.post("/regenerer").headers["location"] == "/login"

    def test_launches_a_background_run(self, client, draft, monkeypatch):
        lancements = []
        monkeypatch.setattr(main, "_regeneration",
                            {"en_cours": False, "depuis": None, "erreur": None})

        class FauxFil:
            def __init__(self, target=None, daemon=None): lancements.append(target)
            def start(self): pass

        monkeypatch.setattr(main.threading, "Thread", FauxFil)
        login(client)
        r = client.post("/regenerer")
        assert r.status_code == 303
        assert len(lancements) == 1
        assert main._regeneration["en_cours"] is True

    def test_refuses_while_one_is_running(self, client, draft, monkeypatch):
        monkeypatch.setattr(main, "_regeneration",
                            {"en_cours": True, "depuis": None, "erreur": None})
        lancements = []
        monkeypatch.setattr(main.threading, "Thread",
                            lambda **kw: lancements.append(kw) or type("T", (), {"start": lambda s: None})())
        login(client)
        client.post("/regenerer")
        assert lancements == []

    def test_refuses_to_replace_a_sent_edition(self, client, db_session, draft, monkeypatch):
        # Les abonnés l'ont reçue : on ne réécrit pas ce qui est parti.
        from datetime import date as _date
        draft.week_of = main.current_week_of()
        draft.status = "sent"
        db_session.commit()
        monkeypatch.setattr(main, "_regeneration",
                            {"en_cours": False, "depuis": None, "erreur": None})
        login(client)
        r = client.post("/regenerer")
        assert "error" in r.headers["location"]
        assert main._regeneration["en_cours"] is False

    def test_the_banner_shows_while_running(self, client, draft, monkeypatch):
        monkeypatch.setattr(main, "_regeneration",
                            {"en_cours": True, "depuis": datetime(2026, 9, 15, 11, 5),
                             "erreur": None})
        login(client)
        page = client.get("/").text
        assert "Régénération en cours" in page
        assert 'http-equiv="refresh"' in page

    def test_a_failure_is_reported(self, client, draft, monkeypatch):
        monkeypatch.setattr(main, "_regeneration",
                            {"en_cours": False, "depuis": None,
                             "erreur": "CLI introuvable"})
        login(client)
        assert "CLI introuvable" in client.get("/").text


class TestReopen:
    def test_requires_authentication(self, client, draft):
        assert client.post(f"/draft/{draft.id}/rouvrir").headers["location"] == "/login"

    def test_a_sent_edition_can_be_reopened(self, client, db_session, draft):
        draft.status = "sent"
        draft.sent_at = datetime(2026, 9, 15, 10, 48)
        db_session.commit()
        login(client)
        client.post(f"/draft/{draft.id}/rouvrir")
        db_session.refresh(draft)
        assert draft.status == "pending_review"

    def test_the_previous_send_trace_survives(self, client, db_session, draft):
        # L'historique ne doit pas mentir entre la réouverture et le renvoi.
        draft.status = "sent"
        draft.sent_at = datetime(2026, 9, 15, 10, 48)
        draft.recipient_count = 87
        db_session.commit()
        login(client)
        client.post(f"/draft/{draft.id}/rouvrir")
        db_session.refresh(draft)
        assert draft.sent_at is not None
        assert draft.recipient_count == 87

    def test_a_pending_edition_is_refused(self, client, draft):
        login(client)
        r = client.post(f"/draft/{draft.id}/rouvrir")
        assert "error" in r.headers["location"]

    def test_unknown_draft_is_a_404(self, client):
        login(client)
        assert client.post("/draft/9999/rouvrir").status_code == 404

    def test_a_reopened_edition_can_be_sent_again(self, client, db_session, draft, monkeypatch):
        envois = []
        monkeypatch.setattr(main, "send_campaign",
                            lambda subject, html_content: envois.append(subject) or "2")
        monkeypatch.setattr(main, "count_recipients", lambda: 87)
        draft.status = "sent"
        db_session.commit()
        login(client)
        client.post(f"/draft/{draft.id}/rouvrir")
        client.post(f"/draft/{draft.id}/send", data=ENVOI)
        db_session.refresh(draft)
        assert draft.status == "sent"
        assert len(envois) == 1
