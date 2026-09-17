"""Interface web de validation, voir PLAN.md §4.

Auth par mot de passe partagé haché + liste blanche d'emails, session en
cookie signé et daté. Une seule vue : voir le dernier brouillon
pending_review, l'éditer, l'envoyer.

L'envoi n'est JAMAIS déclenché par le cron : il part d'ici, sur action humaine.
"""
import logging
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.app import security
from src.config import (
    ALLOWED_REVIEWER_EMAILS,
    APP_SECRET_KEY,
    COOKIE_SECURE,
    MODE_EMPLOI_URL,
    PAGE_ABONNEMENT_URL,
    REVIEWER_PASSWORD_HASH,
    SESSION_MAX_AGE_SECONDS,
)
from scripts.run_weekly import current_week_of
from src.ai.local_generator import find_cli
from src.db.models import Draft, RegenerationRequest, SessionLocal, utcnow
from src.config import BREVO_LIST_ID
from src.email.brevo_sender import (
    _semaine_en_lettres,
    count_recipients,
    render_newsletter,
    send_campaign,
)
from src.sanitize import sanitize_html

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "spf_session"
SESSION_SALT = "reviewer-session"

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory="src/app/templates")


def _logo_url() -> str | None:
    """URL du logo de l'asso, s'il a été déposé dans src/app/static/.

    Le blason n'est pas versionné : à défaut, les gabarits affichent un
    monogramme typographique plutôt qu'une reproduction approximative.
    """
    for name in ("logo.svg", "logo.png", "logo.jpg", "logo.jpeg", "logo.webp"):
        if (STATIC_DIR / name).is_file():
            return f"/static/{name}"
    return None


templates.env.globals["logo_url"] = _logo_url()

RACINE = Path(__file__).resolve().parent.parent.parent

# Une régénération dure trois à quatre minutes : scraping, lecture des
# articles, puis rédaction. Elle ne peut pas tenir dans une requête HTTP. On
# lance donc le script existant en sous-processus et on suit son état ici.
# L'application tourne en un seul processus : un dictionnaire suffit, et le
# verrou empêche deux régénérations simultanées.
_regeneration = {"en_cours": False, "depuis": None, "erreur": None}
_verrou_regeneration = threading.Lock()


def regeneration_state() -> dict:
    return dict(_regeneration)


def _lancer_regeneration() -> None:
    """Exécute scripts/run_weekly.py --remplacer et retient son issue."""
    try:
        resultat = subprocess.run(
            [sys.executable, "scripts/run_weekly.py", "--generator", "local",
             "--remplacer"],
            cwd=RACINE, capture_output=True, text=True, timeout=1800,
        )
        if resultat.returncode != 0:
            derniere = (resultat.stderr or resultat.stdout or "").strip().splitlines()
            _regeneration["erreur"] = derniere[-1][:300] if derniere else "échec inconnu"
        else:
            _regeneration["erreur"] = None
    except Exception as error:
        _regeneration["erreur"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        _regeneration["en_cours"] = False


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
    client_ip = _client_ip(request)

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
            "regeneration": regeneration_state(),
            # La même formulation que dans l'email, plutôt qu'une date ISO.
            "semaine": _semaine_en_lettres(draft.week_of) if draft else "",
            "reviewer": reviewer,
            "mode_emploi_url": MODE_EMPLOI_URL,
            "page_abonnement_url": PAGE_ABONNEMENT_URL,
            # La rédaction s'appuie sur le CLI Claude Code, installé sur le
            # poste du responsable et sur lui seul. Proposer le bouton là où
            # il échouerait promettrait une régénération impossible.
            "demande_en_attente": (
                db.query(RegenerationRequest)
                .filter(RegenerationRequest.status.in_(["en_attente", "en_cours"]))
                .order_by(RegenerationRequest.requested_at)
                .first()
            ),
            "message": message,
            "error": error,
        },
    )


@app.post("/regenerer")
def regenerate(
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    """Relance la collecte et la rédaction pour la semaine en cours."""
    semaine = current_week_of()
    existant = db.query(Draft).filter(Draft.week_of == semaine).first()
    if existant and existant.status == "sent":
        return _redirect_home(
            error="L'édition de cette semaine est déjà envoyée : elle ne peut "
                  "plus être régénérée."
        )

    # Sur l'hébergement, le CLI qui rédige n'existe pas : la demande est
    # déposée en base, et le poste du responsable la ramasse. Le bureau garde
    # ainsi le bouton depuis son téléphone, au prix d'un délai.
    if find_cli() is None:
        en_attente = (
            db.query(RegenerationRequest)
            .filter(RegenerationRequest.status.in_(["en_attente", "en_cours"]))
            .first()
        )
        if en_attente:
            return _redirect_home(
                message="Une réécriture est déjà demandée, elle est en attente."
            )
        db.add(RegenerationRequest(requested_by=reviewer))
        db.commit()
        logger.info("Réécriture mise en file par %s", reviewer)
        return _redirect_home(
            message="Réécriture demandée. Elle sera lancée dès que l'ordinateur "
                    "du responsable sera disponible, et le brouillon se mettra "
                    "à jour tout seul."
        )

    with _verrou_regeneration:
        if _regeneration["en_cours"]:
            return _redirect_home(message="Une régénération est déjà en cours.")
        _regeneration.update({"en_cours": True, "depuis": utcnow(), "erreur": None})

    threading.Thread(target=_lancer_regeneration, daemon=True).start()
    logger.info("Régénération demandée par %s", reviewer)
    return _redirect_home(
        message="Régénération lancée. Comptez trois à quatre minutes ; la page "
                "se rafraîchit toute seule."
    )


@app.post("/draft/{draft_id}/rouvrir")
def reopen_draft(
    draft_id: int,
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    """Remet une édition envoyée en attente, pour la corriger et la renvoyer.

    Le cas d'usage est l'envoi raté : contenu abîmé, lien mort, erreur
    repérée trop tard. La trace de l'envoi précédent est conservée jusqu'au
    suivant, pour que l'historique ne mente pas entre-temps.
    """
    draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Brouillon introuvable.")
    if draft.status != "sent":
        return _redirect_home(error="Cette édition n'a pas été envoyée.")

    db.query(Draft).filter(Draft.id == draft_id).update(
        {"status": "pending_review"}
    )
    db.commit()
    logger.info("Édition %s rouverte par %s", draft_id, reviewer)
    return _redirect_home(
        message="Édition rouverte. Elle peut être corrigée puis renvoyée."
    )


@app.get("/historique", response_class=HTMLResponse)
def history(
    request: Request,
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    """Toutes les éditions, envoyées ou non, avec leur trace d'envoi.

    Un brouillon envoyé n'est jamais supprimé : son contenu reste lisible
    par l'aperçu, qui sert alors d'archive de ce qui est réellement parti.
    """
    drafts = db.query(Draft).order_by(Draft.week_of.desc()).all()
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "drafts": drafts,
            "reviewer": reviewer,
            "semaines": {d.id: _semaine_en_lettres(d.week_of) for d in drafts},
        },
    )


@app.get("/draft/{draft_id}/apercu", response_class=HTMLResponse)
def preview_draft(
    draft_id: int,
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    """Le brouillon tel qu'il arrivera dans une boîte mail.

    L'interface de relecture montre les fragments ; seul cet aperçu montre
    l'enveloppe — en-tête, blason, pied de page. C'est la dernière chose à
    regarder avant d'envoyer.
    """
    draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Brouillon introuvable.")

    return HTMLResponse(
        render_newsletter(
            news_html=draft.news_content or "",
            stages_html=draft.stages_content or "",
            week_of=draft.week_of,
        )
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
    news_content: str = Form(...),
    stages_content: str = Form(...),
    reviewer: str = Depends(get_current_reviewer),
    db=Depends(get_db),
):
    draft = _get_editable_draft(db, draft_id)

    # Le bouton de validation envoie ce qui est à l'écran : on enregistre
    # d'abord, sinon une relecture non sauvegardée partirait dans le vide et
    # les abonnés recevraient la version précédente.
    draft.news_content = sanitize_html(news_content)
    draft.stages_content = sanitize_html(stages_content)
    db.commit()

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

    # Objet en clair, sans date ISO ni tiret cadratin : c'est la première
    # chose que voit l'abonné dans sa liste de messages.
    subject = f"Newsletter SPF - {_semaine_en_lettres(draft.week_of).lower()}"
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

    # Le nombre d'abonnés est figé maintenant : la liste continuera
    # d'évoluer, l'historique doit dire combien de personnes l'ont reçue.
    db.query(Draft).filter(Draft.id == draft_id).update(
        {
            "status": "sent",
            "sent_at": utcnow(),
            "brevo_campaign_id": str(campaign_id),
            "brevo_list_id": int(BREVO_LIST_ID) if BREVO_LIST_ID else None,
            "recipient_count": count_recipients(),
        }
    )
    db.commit()
    logger.info(
        "Newsletter %s envoyée par %s (campagne Brevo %s)",
        draft_id, reviewer, campaign_id,
    )
    return _redirect_home(message="Newsletter envoyée aux abonnés.")


def _client_ip(request: Request) -> str:
    """IP réelle du visiteur, même derrière un tunnel ou un proxy.

    Sans ça, toutes les requêtes arrivant par le tunnel portent l'adresse
    127.0.0.1 : le blocage après cinq échecs deviendrait global, et un seul
    intrus verrouillerait le bureau entier.

    L'en-tête n'est lu que si la requête vient d'une adresse locale — c'est le
    cas d'un tunnel qui tourne sur la même machine. Exposer l'app directement
    à Internet rendrait cet en-tête falsifiable, donc on ne le croit jamais
    venant d'ailleurs.
    """
    direct = request.client.host if request.client else "inconnu"
    if direct not in ("127.0.0.1", "::1", "localhost"):
        return direct

    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for", ""
    )
    # x-forwarded-for est une liste ; le premier élément est le client d'origine.
    first = forwarded.split(",")[0].strip()
    return first or direct


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
