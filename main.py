from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path

from auth import authenticate_user, create_session, register_user, revoke_session
from database.db import get_db, initialize_database
from dependencies import get_current_user, get_optional_user, get_user

load_dotenv()

GLOBAL_BOOK_INDEX = []
GLOBAL_LAW_INDEX = []

def extract_text(elements: list[dict]) -> str:
    parts = []

    for element in elements:
        if element.get("type") == "text":
            parts.append(element.get("content", ""))

        nested_elements = element.get("elements")
        if isinstance(nested_elements, list):
            parts.append(extract_text(nested_elements))

    return " ".join(parts)


def get_law_fulltext(law: dict) -> str:
    paragraphs = law.get("contents", [])

    return " ".join(
        extract_text(paragraph.get("elements", []))
        for paragraph in paragraphs
    )

def build_global_search_index():
    GLOBAL_BOOK_INDEX.clear()
    GLOBAL_LAW_INDEX.clear()

    for file in LAWS_BASE_DIR.rglob("*.json"):
        if file.name.endswith(".link.json"):
            continue

        with open(file, "r", encoding="utf-8") as f:
            content = json.load(f)

        book = {
            "id": content.get("id"),
            "abbr": content.get("abbr", ""),
            "name": content.get("name", ""),
            "scope": content.get("scope", ""),
            "prefix": content.get("prefix", ""),
        }

        GLOBAL_BOOK_INDEX.append({
            **book,
            "_search": f"{book['name']} {book['abbr']}".casefold()
        })

        for law in content.get("laws", []):
            nr = str(law.get("nr", ""))
            name = str(law.get("name") or "")

            GLOBAL_LAW_INDEX.append({
                "id": law["id"],
                "nr": nr,
                "name": name,
                "prefix": book["prefix"],
                "no_prefix": law.get("no_prefix", False),
                "book": book,
                "fulltext": get_law_fulltext(law),
                "_search": f"{nr} {name}".casefold(),
            })

@asynccontextmanager
async def lifespan(app: FastAPI):
    build_global_search_index()
    yield
    GLOBAL_TITLE_INDEX.clear()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SECRET_KEY"],
    session_cookie="legiceptum_session",
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=True,
)

templates = Jinja2Templates(directory="templates")

initialize_database()

app.mount("/static", StaticFiles(directory="static"), name="static")

LAWS_BASE_DIR = Path("./laws")
GLOBAL_TITLE_INDEX: list[dict] = []
SESSION_COOKIE = "legiceptum_session"

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": "home"
        }
    )

@app.get("/book/{book}")
async def book(
    request: Request,
    book: str,
    user = Depends(get_optional_user)
):
    file = Path(LAWS_BASE_DIR, book + ".json")

    if not file.is_file():
        raise HTTPException(detail=f"File {file} was not found", status_code=404)

    with open(file, "r") as f:
        bc = json.load(f)

    date = datetime.strptime(bc["updated"], "%Y-%m-%d")
    formatted = date.strftime("%d.%m.%Y")

    if user is not None:
        with get_db() as db:
            bookmarks = {
                row["law_id"]
                for row in db.execute(
                    """
                    SELECT law_id FROM bookmarks
                    WHERE user_id = ?
                    """,
                    (user["id"],)
                ).fetchall()
            }

        for law in bc["laws"]:
            law["bookmarked"] = law["id"] in bookmarks
    else:
        for law in bc["laws"]:
            law["bookmarked"] = False

    return templates.TemplateResponse(
        request=request,
        name="book.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": bc["id"],
            "book": bc,
            "book_updated": formatted
        }
    )

@app.get("/law/{id}")
async def get_law(
    request: Request,
    id: str,
    highlight: str | None = None,
    user = Depends(get_optional_user)
):
    splitted = id.split("-", 2)
    book = splitted[0]

    file = Path(LAWS_BASE_DIR, book + ".json")

    if not file.is_file():
        raise HTTPException(detail="Law not found", status_code=404)

    with open(file, "r", encoding="utf-8") as f:
        content = json.load(f)

    date = datetime.strptime(content["updated"], "%Y-%m-%d")
    formatted = date.strftime("%d.%m.%Y")

    law = next(
        (law for law in content["laws"] if law["id"] == id),
        None
    )

    if law is None:
        raise HTTPException(detail="Law not found", status_code=404)

    # Load cross-references
    links_file = Path(LAWS_BASE_DIR, book + ".link.json")
    law_links = []

    if links_file.is_file():
        with open(links_file, "r", encoding="utf-8") as f:
            link_data = json.load(f)

        law_links = [
            link
            for link in link_data.get("links", [])
            if link["source"] == id
        ]

    # bookmark
    if user is not None:
        with get_db() as db:
            user_id = user["id"]

            bookmark = db.execute(
                """
                SELECT 1 FROM bookmarks
                WHERE user_id = ? AND law_id = ?
                """,
                (user_id, law["id"])
            ).fetchone()

            if bookmark: bookmarked = True
            else: bookmarked = False
    else:
        bookmarked = False

    # notes
    if user is not None:
        with get_db() as db:
            notes = db.execute("""
            SELECT *
            FROM notes
            WHERE user_id = ? AND law_id = ?
            """, (user["id"], law["id"])).fetchone()

            if notes: pnotes = notes["content"]
            else: pnotes = ""
    else:
        pnotes = ""

    return templates.TemplateResponse(
        request=request,
        name="law.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": content["id"],
            "bookmarked": bookmarked,
            "notes": pnotes,
            "law": law,
            "highlight": highlight,
            "law_links": law_links,
            "book_id": content["id"],
            "book_abbr": content["abbr"],
            "book_prefix": content["prefix"],
            "book_name": content["name"],
            "book_updated": formatted
        }
    )

@app.post("/laws/all")
async def get_all_laws(request: Request):
    return all_books()

@app.post("/statistic/laws")
async def get_laws_statistic(request: Request):
    books = all_books()

    laws = 0

    for book in books:
        laws += len(book["laws"])

    return laws

def all_books():
    books = []
    
    for file in LAWS_BASE_DIR.rglob("*.json"):
        if file.name.endswith(".link.json"):
            continue

        with open(file, "r") as f:
            content = json.load(f)
            books.append(content)

    return books

class GlobalTitleSearchRequest(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=150, ge=1, le=150)


@app.post("/laws/search/global/title")
async def search_all_titles(data: GlobalTitleSearchRequest):
    query = data.query.strip().casefold()

    if not query:
        return {
            "query": query,
            "page": data.page,
            "limit": data.limit,
            "total": 0,
            "has_more": False,
            "results": []
        }

    matches = [
        entry for entry in GLOBAL_TITLE_INDEX
        if query in entry["_search"]
    ]

    total = len(matches)
    start = (data.page - 1) * data.limit
    end = start + data.limit

    results = [
        {
            "id": entry["id"],
            "nr": entry["nr"],
            "name": entry["name"],
            "prefix": entry["prefix"],
            "no_prefix": entry["no_prefix"],
            "book": entry["book"]
        }
        for entry in matches[start:end]
    ]

    return {
        "query": query,
        "page": data.page,
        "limit": data.limit,
        "total": total,
        "has_more": end < total,
        "results": results
    }

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...)
):
    if password != password_confirm:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": "Die Passwörter stimmen nicht überein."},
            status_code=400
        )

    try:
        user_id = register_user(username, email, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": str(exc)},
            status_code=400
        )

    token = create_session(user_id)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/"
    )

    return response


@app.post("/login")
async def login(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...)
):
    user = authenticate_user(identifier, password)

    if user is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Ungültige Anmeldedaten."},
            status_code=401
        )

    # Alte Session widerrufen, falls vorhanden
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        revoke_session(old_token)

    token = create_session(user["id"])

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/"
    )

    return response

@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": "account",
            "error": None
        }
    )

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": "account",
            "error": None
        }
    )

@app.get("/account")
async def account(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": "account"
        }
    )

@app.get("/search")
async def search_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "user": get_user(request),
            "laws": all_books(),
            "active_book": "search"
        }
    )

class GlobalSearchRequest(BaseModel):
    query: str
    scope: list[Literal[
        "book_name",
        "book_abbreviation",
        "law_number",
        "law_name",
        "law_content"
    ]] = Field(min_length=1)

    page: int = Field(default=1, ge=1)
    limit: int = Field(default=150, ge=1, le=150)

def get_search_snippet(text: str, query: str, context: int = 100) -> str:
    index = text.casefold().find(query.casefold())

    if index == -1:
        return ""

    start = max(0, index - context)
    end = min(len(text), index + len(query) + context)

    snippet = text[start:end]

    if start > 0:
        snippet = "…" + snippet

    if end < len(text):
        snippet += "…"

    return snippet

@app.post("/search")
async def global_search(data: GlobalSearchRequest):
    query = data.query.strip().casefold()
    scopes = set(data.scope)

    if not query:
        return {
            "query": query,
            "scope": data.scope,
            "page": data.page,
            "limit": data.limit,
            "total": 0,
            "has_more": False,
            "results": {
                "books": [],
                "laws": []
            }
        }

    matches = []

    # Gesetzbücher durchsuchen
    if "book_name" in scopes or "book_abbreviation" in scopes:
        for book in GLOBAL_BOOK_INDEX:
            matched_scopes = []
            score = 0

            abbr = book["abbr"].casefold()
            name = book["name"].casefold()

            if "book_name" in scopes and query in name:
                matched_scopes.append("book_name")

                if name == query:
                    score = max(score, 80)
                elif name.startswith(query):
                    score = max(score, 65)
                else:
                    score = max(score, 35)

            if "book_abbreviation" in scopes and query in abbr:
                matched_scopes.append("book_abbreviation")

                if abbr == query:
                    score = max(score, 85)
                elif abbr.startswith(query):
                    score = max(score, 70)
                else:
                    score = max(score, 45)

            if matched_scopes:
                score += (len(matched_scopes) - 1) * 15

                matches.append({
                    "type": "book",
                    "matched_scopes": matched_scopes,
                    "_score": score,
                    "result": {
                        "id": book["id"],
                        "abbr": book["abbr"],
                        "name": book["name"],
                        "scope": book["scope"]
                    }
                })

    # Einzelnormen durchsuchen
    for law in GLOBAL_LAW_INDEX:
        matched_scopes = []
        snippet = None
        score = 0

        nr = law["nr"].casefold()
        name = law["name"].casefold()
        fulltext = law["fulltext"].casefold()

        if "law_number" in scopes and query in nr:
            matched_scopes.append("law_number")

            if nr == query:
                score = max(score, 100)
            elif nr.startswith(query):
                score = max(score, 70)
            else:
                score = max(score, 40)

        if "law_name" in scopes and query in name:
            matched_scopes.append("law_name")

            if name == query:
                score = max(score, 90)
            elif name.startswith(query):
                score = max(score, 75)
            else:
                score = max(score, 40)

        if "law_content" in scopes and query in fulltext:
            matched_scopes.append("law_content")
            snippet = get_search_snippet(law["fulltext"], query)
            score = max(score, 10)

        if matched_scopes:
            # Bonus für mehrere übereinstimmende Suchbereiche
            score += (len(matched_scopes) - 1) * 15

            matches.append({
                "type": "law",
                "matched_scopes": matched_scopes,
                "_score": score,
                "result": {
                    "id": law["id"],
                    "nr": law["nr"],
                    "name": law["name"],
                    "prefix": law["prefix"],
                    "no_prefix": law["no_prefix"],
                    "book": law["book"],
                    "snippet": snippet
                }
            })

    # Gemeinsame Relevanzsortierung
    matches.sort(
        key=lambda entry: entry["_score"],
        reverse=True
    )

    # Gemeinsame Pagination
    total = len(matches)
    start = (data.page - 1) * data.limit
    end = start + data.limit

    paginated = matches[start:end]

    # Ergebnisse gruppieren
    books = [
        {
            **entry["result"],
            "matched_scopes": entry["matched_scopes"]
        }
        for entry in paginated
        if entry["type"] == "book"
    ]

    laws = [
        {
            **entry["result"],
            "matched_scopes": entry["matched_scopes"]
        }
        for entry in paginated
        if entry["type"] == "law"
    ]

    return {
        "query": query,
        "scope": data.scope,
        "page": data.page,
        "limit": data.limit,
        "total": total,
        "has_more": end < total,
        "results": {
            "books": books,
            "laws": laws
        }
    }

@app.get("/logout")
@app.post("/logout")
async def logout_post(request: Request):
    token = request.cookies.get(SESSION_COOKIE)

    if token:
        revoke_session(token)

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax"
    )

    return response

@app.post("/bookmark/{law_id}")
def toggle_bookmark(
    law_id: str,
    user: dict = Depends(get_current_user)
):
    with get_db() as db:
        user_id = user["id"]

        # Prüfen, ob das Bookmark bereits existiert
        bookmark = db.execute(
            """
            SELECT 1 FROM bookmarks
            WHERE user_id = ? AND law_id = ?
            """,
            (user_id, law_id)
        ).fetchone()

        if bookmark:
            db.execute(
                """
                DELETE FROM bookmarks
                WHERE user_id = ? AND law_id = ?
                """,
                (user_id, law_id)
            )
            db.commit()

            return {"bookmarked": False}

        db.execute(
            """
            INSERT INTO bookmarks (user_id, law_id)
            VALUES (?, ?)
            """,
            (user_id, law_id)
        )
        db.commit()

        return {"bookmarked": True}

@app.get("/bookmarks")
async def bookmarks(
    request: Request,
    user=Depends(get_current_user)
):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT law_id FROM bookmarks
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user["id"],)
        ).fetchall()

    bookmarked_ids = {row["law_id"] for row in rows}

    results = []

    for book in all_books():
        for law in book["laws"]:
            if law["id"] not in bookmarked_ids:
                continue

            results.append({
                "id": law["id"],
                "nr": law["nr"],
                "name": law.get("name", ""),
                "prefix": book.get("prefix", ""),
                "no_prefix": law.get("no_prefix", False),
                "book_abbr": book["abbr"],
                "book_name": book["name"],
                "url": f"/law/{law['id']}"
            })

    return templates.TemplateResponse(
        request=request,
        name="bookmarks.html",
        context={
            "user": user,
            "laws": all_books(),
            "active_book": "bookmarks",
            "bookmarks": results
        }
    )

class NoteSetRequest(BaseModel):
    content: str = Field(max_length=5000)

@app.post("/notes/{law_id}")
async def set_notes(
    request: Request,
    law_id: str,
    data: NoteSetRequest,
    user = Depends(get_current_user)
):
    with get_db() as db:
        db.execute("""
        INSERT INTO notes (user_id, law_id, content)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, law_id)
        DO UPDATE SET content = excluded.content;
        """, (user["id"], law_id, data.content))

    return {"success": True}

@app.get("/notes/{law_id}")
async def get_notes(
    request: Request,
    law_id: str,
    user = Depends(get_optional_user)
):
    if user is None:
        return {"notes": None}

    with get_db() as db:
        notes = db.execute("""
        SELECT *
        FROM notes
        WHERE user_id = ? AND law_id = ?
        """, (user["id"], law_id)).fetchone()

        if notes is None:
            return {"notes": None}

    return {"notes": notes["content"]}