"""Interface web de validation — voir PLAN.md §4.

Auth simple par liste blanche d'emails + session cookie signé.
Une seule vue : voir le dernier brouillon pending_review, l'éditer, l'envoyer.
"""
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer

from src.config import APP_SECRET_KEY, ALLOWED_REVIEWER_EMAILS
from src.db.models import SessionLocal, Draft
from src.email.brevo_sender import send_campaign

app = FastAPI()
templates = Jinja2Templates(directory="src/app/templates")
serializer = URLSafeSerializer(APP_SECRET_KEY)


def get_current_reviewer(request: Request) -> str:
    """TODO: brancher sur un vrai flow OAuth Google restreint au domaine
    sciencespo.fr, ou a minima vérifier un cookie de session signé."""
    session_cookie = request.cookies.get("session")
    if not session_cookie:
        raise HTTPException(status_code=401, detail="Non authentifié")
    email = serializer.loads(session_cookie)
    if email not in ALLOWED_REVIEWER_EMAILS:
        raise HTTPException(status_code=403, detail="Non autorisé")
    return email


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # TODO: formulaire de login ou redirection OAuth
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
def review_draft(request: Request, reviewer: str = Depends(get_current_reviewer)):
    db = SessionLocal()
    draft = (
        db.query(Draft)
        .filter(Draft.status == "pending_review")
        .order_by(Draft.created_at.desc())
        .first()
    )
    return templates.TemplateResponse(
        "review.html", {"request": request, "draft": draft}
    )


@app.post("/draft/{draft_id}/save")
def save_draft(draft_id: int, news_content: str, stages_content: str,
                reviewer: str = Depends(get_current_reviewer)):
    db = SessionLocal()
    draft = db.query(Draft).get(draft_id)
    draft.news_content = news_content
    draft.stages_content = stages_content
    draft.reviewed_by = reviewer
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/draft/{draft_id}/send")
def send_draft(draft_id: int, reviewer: str = Depends(get_current_reviewer)):
    db = SessionLocal()
    draft = db.query(Draft).get(draft_id)
    html = f"{draft.news_content}\n{draft.stages_content}"
    send_campaign(subject=f"Newsletter SPF — semaine du {draft.week_of}", html_content=html)
    draft.status = "sent"
    db.commit()
    return RedirectResponse("/", status_code=303)
