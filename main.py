from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
import json
import os
import sqlite3
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form
from fastapi.params import Query
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import HTTPException, RequestValidationError
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
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

def build_global_title_index():
    GLOBAL_TITLE_INDEX.clear()

    for file in LAWS_BASE_DIR.rglob("*.json"):
        if file.name.endswith(".link.json"):
            continue

        try:
            with open(file, "r", encoding="utf-8") as f:
                content = json.load(f)
        except (OSError, json.JSONDecodeError) as error:
            print(f"Fehler beim Laden von {file}: {error}")
            continue

        book = {
            "id": content.get("id"),
            "abbr": content.get("abbr"),
            "name": content.get("name"),
            "scope": content.get("scope")
        }

        for law in content.get("laws", []):
            nr = str(law.get("nr") or "")
            name = str(law.get("name") or "")

            GLOBAL_TITLE_INDEX.append({
                "id": law["id"],
                "nr": nr,
                "name": name,
                "prefix": content.get("prefix", ""),
                "no_prefix": law.get("no_prefix", False),
                "book": book,
                "_search": f"{nr} {name}".casefold()
            })

    print(
        f"Globaler Titelindex aufgebaut: "
        f"{len(GLOBAL_TITLE_INDEX)} Normen"
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
    build_global_title_index()
    build_global_search_index()
    yield
    GLOBAL_TITLE_INDEX.clear()
    GLOBAL_BOOK_INDEX.clear()

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

# ==================================================
# Error pages
# ==================================================

ERROR_MESSAGES = {
    401: (
        "Anmeldung erforderlich",
        "Für diese Seite musst du angemeldet sein."
    ),
    403: (
        "Kein Zugriff",
        "Du hast keine Berechtigung, diese Seite zu sehen."
    ),
    404: (
        "Seite nicht gefunden",
        "Diese Seite gibt es nicht, oder sie wurde verschoben."
    ),
    405: (
        "Aufruf nicht möglich",
        "Diese Seite kann so nicht aufgerufen werden."
    ),
    422: (
        "Ungültige Anfrage",
        "Die Anfrage konnte nicht verarbeitet werden."
    ),
    500: (
        "Interner Fehler",
        "Auf unserer Seite ist etwas schiefgelaufen. "
        "Bitte versuche es später noch einmal."
    ),
}

DEFAULT_ERROR_MESSAGE = (
    "Es ist ein Fehler aufgetreten",
    "Die Seite konnte nicht angezeigt werden."
)

# Used only when the regular error template cannot be rendered
FALLBACK_ERROR_HTML = """<!doctype html>
<html lang="de"><head><meta charset="UTF-8" />
<title>Legiceptum - {title}</title></head>
<body style="font-family: sans-serif; text-align: center; margin-top: 4rem;">
<h1>{code}</h1><p>{title}</p><p><a href="/">Zur Startseite</a></p>
</body></html>"""


def wants_html(request: Request) -> bool:
    """Browser page loads ask for HTML, fetch() calls usually do not."""
    return "text/html" in request.headers.get("accept", "")


def render_error(request: Request, status_code: int, headers=None):
    title, message = ERROR_MESSAGES.get(status_code, DEFAULT_ERROR_MESSAGE)

    # The error page must never fail itself, so every lookup is guarded
    try:
        user = get_user(request)
    except Exception:
        user = None

    try:
        laws = all_books()
    except Exception:
        laws = []

    try:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "user": user,
                "laws": laws,
                "active_book": None,
                "error_code": status_code,
                "error_title": title,
                "error_message": message,
                "show_login": status_code == 401
            },
            status_code=status_code,
            headers=headers
        )
    except Exception:
        return HTMLResponse(
            FALLBACK_ERROR_HTML.format(code=status_code, title=title),
            status_code=status_code,
            headers=headers
        )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request,
    exc: StarletteHTTPException
):
    if wants_html(request):
        return render_error(request, exc.status_code, exc.headers)

    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request,
    exc: RequestValidationError
):
    if wants_html(request):
        return render_error(request, 422)

    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    # Starlette still logs the traceback after this response was sent
    if wants_html(request):
        return render_error(request, 500)

    return JSONResponse(
        {"detail": "Internal Server Error"},
        status_code=500
    )


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
        raise HTTPException(detail="Book not found", status_code=404)

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

def pager_entry(laws: list[dict], index: int) -> dict | None:
    """Return the data needed to link to laws[index], or None at the edges."""
    if index < 0 or index >= len(laws):
        return None

    entry = laws[index]

    return {
        "id": entry["id"],
        "nr": entry["nr"],
        "name": entry.get("name", ""),
        "no_prefix": entry.get("no_prefix", False)
    }


@app.get("/law/{id}")
async def get_law(
    request: Request,
    id: str,
    highlight: str | None = None,
    clean_print: str | None = Query(None, alias="clean-print"),
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

    laws = content["laws"]

    index = next(
        (i for i, entry in enumerate(laws) if entry["id"] == id),
        None
    )

    if index is None:
        raise HTTPException(detail="Law not found", status_code=404)

    law = laws[index]

    # Neighbours in reading order (the file order is the legal order)
    prev_law = pager_entry(laws, index - 1)
    next_law = pager_entry(laws, index + 1)

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
            "prev_law": prev_law,
            "next_law": next_law,
            "highlight": highlight,
            "law_links": law_links,
            "book_id": content["id"],
            "book_abbr": content["abbr"],
            "book_prefix": content["prefix"],
            "book_name": content["name"],
            "book_updated": formatted,
            "clean_print": clean_print is not None
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
            # Without a bookmark there is no collection membership
            db.execute(
                """
                DELETE FROM collection_items
                WHERE law_id = ?
                AND collection_id IN (
                    SELECT id FROM collections WHERE user_id = ?
                )
                """,
                (law_id, user_id)
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
    collection: int | None = None,
    user=Depends(get_current_user)
):
    with get_db() as db:
        if collection is not None:
            require_owned_collection(db, collection, user["id"])

            rows = db.execute(
                """
                SELECT b.law_id FROM bookmarks b
                JOIN collection_items ci ON ci.law_id = b.law_id
                WHERE b.user_id = ? AND ci.collection_id = ?
                ORDER BY b.created_at DESC
                """,
                (user["id"], collection)
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT law_id FROM bookmarks
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user["id"],)
            ).fetchall()

        collections = fetch_collections(db, user["id"])

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
            "bookmarks": results,
            "collections": collections,
            "active_collection": collection,
            "max_collections": MAX_COLLECTIONS
        }
    )

# ==================================================
# Collections
# ==================================================

MAX_COLLECTIONS = 10


class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)


@lru_cache(maxsize=None)
def _book_law_ids(book: str) -> frozenset[str]:
    file = Path(LAWS_BASE_DIR, book + ".json")

    if not file.is_file():
        return frozenset()

    with open(file, "r", encoding="utf-8") as f:
        content = json.load(f)

    return frozenset(law["id"] for law in content["laws"])


def law_exists(law_id: str) -> bool:
    book = law_id.split("-", 1)[0]

    # Prevents path tricks such as "../"
    if not book.isalnum():
        return False

    return law_id in _book_law_ids(book)


def require_owned_collection(db, collection_id: int, user_id: int):
    row = db.execute(
        "SELECT id, user_id FROM collections WHERE id = ?",
        (collection_id,)
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return row


def fetch_collections(db, user_id: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT c.id, c.name, COUNT(b.id) AS count
        FROM collections c
        LEFT JOIN collection_items ci ON ci.collection_id = c.id
        LEFT JOIN bookmarks b
            ON b.law_id = ci.law_id AND b.user_id = c.user_id
        WHERE c.user_id = ?
        GROUP BY c.id
        ORDER BY c.name COLLATE NOCASE
        """,
        (user_id,)
    ).fetchall()

    return [dict(row) for row in rows]


@app.get("/collections")
async def list_collections(user=Depends(get_current_user)):
    with get_db() as db:
        return {"collections": fetch_collections(db, user["id"])}


@app.post("/collections")
async def create_collection(
    data: CollectionRequest,
    user=Depends(get_current_user)
):
    name = data.name.strip()

    if not name:
        raise HTTPException(status_code=422, detail="Name required")

    with get_db() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM collections WHERE user_id = ?",
            (user["id"],)
        ).fetchone()[0]

        if total >= MAX_COLLECTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Maximal {MAX_COLLECTIONS} Sammlungen erlaubt"
            )

        try:
            cursor = db.execute(
                "INSERT INTO collections (user_id, name) VALUES (?, ?)",
                (user["id"], name)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="Eine Sammlung mit diesem Namen existiert bereits"
            )

        return {"id": cursor.lastrowid, "name": name, "count": 0}


@app.patch("/collections/{collection_id}")
async def rename_collection(
    collection_id: int,
    data: CollectionRequest,
    user=Depends(get_current_user)
):
    name = data.name.strip()

    if not name:
        raise HTTPException(status_code=422, detail="Name required")

    with get_db() as db:
        require_owned_collection(db, collection_id, user["id"])

        try:
            db.execute(
                "UPDATE collections SET name = ? WHERE id = ?",
                (name, collection_id)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="Eine Sammlung mit diesem Namen existiert bereits"
            )

    return {"id": collection_id, "name": name}


@app.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: int,
    user=Depends(get_current_user)
):
    with get_db() as db:
        require_owned_collection(db, collection_id, user["id"])

        db.execute(
            "DELETE FROM collections WHERE id = ?",
            (collection_id,)
        )

    return {"success": True}


@app.get("/collections/for-law/{law_id}")
async def collections_for_law(
    law_id: str,
    user=Depends(get_current_user)
):
    with get_db() as db:
        collections = fetch_collections(db, user["id"])

        rows = db.execute(
            """
            SELECT ci.collection_id
            FROM collection_items ci
            JOIN collections c ON c.id = ci.collection_id
            WHERE c.user_id = ? AND ci.law_id = ?
            """,
            (user["id"], law_id)
        ).fetchall()

    return {
        "collections": collections,
        "member_of": [row["collection_id"] for row in rows]
    }


@app.put("/collections/{collection_id}/items/{law_id}")
async def add_to_collection(
    collection_id: int,
    law_id: str,
    user=Depends(get_current_user)
):
    with get_db() as db:
        require_owned_collection(db, collection_id, user["id"])

        if not law_exists(law_id):
            raise HTTPException(status_code=404, detail="Law not found")

        # Adding to a collection automatically bookmarks the norm
        db.execute(
            """
            INSERT INTO bookmarks (user_id, law_id)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM bookmarks WHERE user_id = ? AND law_id = ?
            )
            """,
            (user["id"], law_id, user["id"], law_id)
        )

        db.execute(
            """
            INSERT OR IGNORE INTO collection_items (collection_id, law_id)
            VALUES (?, ?)
            """,
            (collection_id, law_id)
        )

    return {"success": True, "bookmarked": True}


@app.delete("/collections/{collection_id}/items/{law_id}")
async def remove_from_collection(
    collection_id: int,
    law_id: str,
    user=Depends(get_current_user)
):
    with get_db() as db:
        require_owned_collection(db, collection_id, user["id"])

        db.execute(
            """
            DELETE FROM collection_items
            WHERE collection_id = ? AND law_id = ?
            """,
            (collection_id, law_id)
        )

    return {"success": True}


class SettingsRequest(BaseModel):
    theme: Literal["auto", "light", "dark"]
    font_size: Literal["s", "m", "l", "xl"]


@app.put("/settings")
async def set_settings(
    data: SettingsRequest,
    user=Depends(get_current_user)
):
    with get_db() as db:
        db.execute("""
        INSERT INTO user_settings (user_id, theme, font_size)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            theme = excluded.theme,
            font_size = excluded.font_size,
            updated_at = CURRENT_TIMESTAMP;
        """, (user["id"], data.theme, data.font_size))

    return {"success": True}


class NoteSetRequest(BaseModel):
    content: str = Field(max_length=2000)

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