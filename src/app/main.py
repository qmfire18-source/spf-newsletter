"""Interface web de validation — voir PLAN.md §4.

Auth par mot de passe partagé haché + liste blanche d'emails, session en
cookie signé et daté. Une seule vue : voir le dernier brouillon
pending_review, l'éditer, l'envoyer.

L'envoi n'est JAMAIS déclenché par le cron : il part d'ici, sur action humaine.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.app import security
from src.config import (
    ALLOWED_REVIEWER_EMAILS,
    APP_SECRET_KEY,
    COOKIE_SECURE,
    REVIEWER_PASSWORD_HASH,
    SESSION_MAX_AGE_SECONDS,
)
from src.db.models import Draft, SessionLocal
from src.email.brevo_sender import render_newsletter, send_campaign
from src.sanitize import sanitize_html

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "spf_session"
SESSION_SALT = "reviewer-session"

app = FastAPI()
templates = Jinja2Templates(directory="src/app/templates")


class NotAuthenticated(Exception):
    """Déclenche une redirection vers /login plutôt qu'une erreur JSON."""


def _serializer() -> URLSafeTimedSerializer:
    # Construit à la demande : sans APP_SECRET_KEY, importer ce module ne doit
    # pas échouer, mais toute tentative d'authentification doit refuser.
    if not APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY manquante : authentification impossible.")
    return URLSafeTimedSerializer(APP_SECRET_KEY, salt=SESSION_SALT)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_reviewer(request: Request) -> str:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        raise NotAuthenticated
    try:
        email = _serializer().loads(cookie, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired, RuntimeError):
        raise NotAuthenticated
    # La liste blanche est revérifiée à chaque requête : retirer quelqu'un du
    # .env doit le déconnecter, sans attendre l'expiration de son cookie.
    if not isinstance(email, str) or email.lower() not in ALLOWED_REVIEWER_EMAILS:
        raise NotAuthenticated
    return email.lower()


@app.exception_handler(NotAuthenticated)
def redirect_to_login(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    client_ip = request.client.host if request.client else "inconnu"

    if security.is_locked_out(client_ip):
        logger.warning("Connexion bloquée (trop d'échecs) depuis %s", client_ip)
        return _login_error("Trop de tentatives. Réessaye dans quelques minutes.")

    if not REVIEWER_PASSWORD_HASH:
        logger.error("REVIEWER_PASSWORD_HASH absente : voir src/app/security.py")
        return _login_error("Authentification non configurée côté serveur.")

    # Le mot de passe est vérifié même si l'email est inconnu, pour ne pas
    # révéler par le temps de réponse quels emails sont sur la liste blanche.
    password_ok = security.verify_password(password, REVIEWER_PASSWORD_HASH)
    email_ok = email.strip().lower() in ALLOWED_REVIEWER_EMAILS

    if not (password_ok and email_ok):
        security.record_failure(client_ip)
        logger.warning("Échec de connexion pour %r depuis %s", email, client_ip)
        return _login_error("Email ou mot de passe incorrect.")

    security.reset_failures(client_ip)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _serializer().dumps(email.strip().lower()),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def review_draft(
    request: Request,
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
    message: str | None = None,
    error: str | None = None,
):
    draft = (
        db.query(Draft)
        .filter(Draft.status == "pending_review")
        .order_by(Draft.created_at.desc())
        .first()
    )
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "draft": draft,
            "reviewer": reviewer,
            "message": message,
            "error": error,
        },
    )


@app.post("/draft/{draft_id}/save")
def save_draft(
    draft_id: int,
    news_content: str = Form(...),
    stages_content: str = Form(...),
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    draft = _get_editable_draft(db, draft_id)
    draft.news_content = sanitize_html(news_content)
    draft.stages_content = sanitize_html(stages_content)
    draft.reviewed_by = reviewer
    db.commit()
    return _redirect_home(message="Modifications enregistrées.")


@app.post("/draft/{draft_id}/send")
def send_draft(
    draft_id: int,
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    draft = _get_editable_draft(db, draft_id)

    # Verrou anti-double-envoi : on passe le brouillon à "approved" en exigeant
    # qu'il soit encore "pending_review". Si deux clics arrivent en parallèle,
    # un seul voit rowcount == 1 et appelle Brevo.
    locked = (
        db.query(Draft)
        .filter(Draft.id == draft_id, Draft.status == "pending_review")
        .update({"status": "approved", "reviewed_by": reviewer})
    )
    db.commit()
    if not locked:
        return _redirect_home(error="Ce brouillon a déjà été envoyé.")

    subject = f"Newsletter SPF — semaine du {draft.week_of}"
    html = render_newsletter(
        news_html=draft.news_content or "",
        stages_html=draft.stages_content or "",
        week_of=draft.week_of,
    )
    try:
        campaign_id = send_campaign(subject=subject, html_content=html)
    except Exception:
        # L'envoi a échoué : on rend le brouillon rééditable plutôt que de le
        # laisser bloqué en "approved".
        logger.exception("Envoi Brevo échoué pour le brouillon %s", draft_id)
        db.query(Draft).filter(Draft.id == draft_id).update(
            {"status": "pending_review"}
        )
        db.commit()
        return _redirect_home(
            error="L'envoi Brevo a échoué. Le brouillon reste modifiable."
        )

    db.query(Draft).filter(Draft.id == draft_id).update(
        {"status": "sent", "sent_at": _utcnow()}
    )
    db.commit()
    logger.info(
        "Newsletter %s envoyée par %s (campagne Brevo %s)",
        draft_id, reviewer, campaign_id,
    )
    return _redirect_home(message="Newsletter envoyée aux abonnés.")


def _get_editable_draft(db, draft_id: int) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Brouillon introuvable")
    return draft


def _redirect_home(message: str = "", error: str = "") -> RedirectResponse:
    query = {k: v for k, v in (("message", message), ("error", error)) if v}
    return RedirectResponse(f"/?{urlencode(query)}", status_code=303)


def _login_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/login?{urlencode({'error': message})}", status_code=303)


def _utcnow() -> datetime:
    # created_at/sent_at sont des DateTime naïfs côté SQLAlchemy : on stocke
    # de l'UTC sans fuseau pour rester cohérent avec datetime.utcnow.
    return datetime.now(timezone.utc).replace(tzinfo=None)
