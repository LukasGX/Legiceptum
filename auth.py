import hashlib
import re
import secrets
import sqlite3

from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from database.db import get_db


password_hasher = PasswordHasher()

SESSION_DURATION = timedelta(days=7)
CONFIRMATION_DURATION = timedelta(hours=24)
MIN_PASSWORD_LENGTH = 12
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 1024
USERNAME_TAKEN_MESSAGE = "Dieser Benutzername ist bereits vergeben."

# Deliberately simple: one @, no whitespace, a dot in the domain part
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


# Verified against when the user does not exist, so the response time
# does not reveal whether an account exists
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_user(username: str, email: str, password: str) -> str | None:
    """Store a pending registration and return its confirmation token.

    Returns None if the email already belongs to an account. A taken username
    is reported openly, a taken email is not, so callers must answer both
    outcomes identically for the email case. No user row is created before
    the confirmation link is used.
    """
    username = username.strip()
    email = email.strip().lower()

    if len(username) < 3 or len(username) > 32:
        raise ValueError("Ungültiger Benutzername.")

    # "@" is reserved for emails so a login identifier is never ambiguous
    if "@" in username:
        raise ValueError("Der Benutzername darf kein @ enthalten.")

    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Ungültige E-Mail-Adresse.")

    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein."
        )

    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("Das Passwort ist zu lang.")

    # Always hash, so a taken email costs the same time as a new account
    password_hash = hash_password(password)

    token = secrets.token_urlsafe(32)

    try:
        with get_db() as db:
            _delete_expired_pending(db)

            if _exists(db, "users", "email", email):
                return None

            # A newer attempt for the same email replaces the older one
            db.execute(
                "DELETE FROM pending_registrations WHERE email = ? COLLATE NOCASE",
                (email,)
            )

            if (_exists(db, "users", "username", username)
                    or _exists(db, "pending_registrations", "username", username)):
                raise ValueError(USERNAME_TAKEN_MESSAGE)

            db.execute(
                """
                INSERT INTO pending_registrations
                    (username, email, password_hash, token_hash, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, email, password_hash, hash_token(token),
                 _confirmation_expiry())
            )

            return token

    except sqlite3.IntegrityError as exc:
        # Lost a race between the check and the insert
        if "username" in str(exc):
            raise ValueError(USERNAME_TAKEN_MESSAGE) from exc
        if "email" in str(exc):
            return None
        raise


def confirm_registration(token: str) -> bool:
    """Turn a pending registration into an account. False if the token is invalid."""
    if not token:
        return False

    now = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as db:
            pending = db.execute(
                """
                SELECT id, username, email, password_hash
                FROM pending_registrations
                WHERE token_hash = ? AND expires_at > ?
                """,
                (hash_token(token), now)
            ).fetchone()

            if pending is None:
                return False

            db.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (pending["username"], pending["email"], pending["password_hash"])
            )
            db.execute(
                "DELETE FROM pending_registrations WHERE id = ?",
                (pending["id"],)
            )

            return True

    except sqlite3.IntegrityError:
        # The username or email was taken by a confirmed account meanwhile
        return False


def renew_pending_registration(email: str) -> tuple[str, str] | None:
    """Issue a fresh token for a pending registration.

    Returns (token, email) or None if no registration is pending for the email.
    """
    email = email.strip().lower()
    token = secrets.token_urlsafe(32)

    with get_db() as db:
        _delete_expired_pending(db)

        cursor = db.execute(
            """
            UPDATE pending_registrations
            SET token_hash = ?, expires_at = ?
            WHERE email = ? COLLATE NOCASE
            """,
            (hash_token(token), _confirmation_expiry(), email)
        )

        if cursor.rowcount == 0:
            return None

    return token, email


def _confirmation_expiry() -> str:
    return (datetime.now(timezone.utc) + CONFIRMATION_DURATION).isoformat()


def _delete_expired_pending(db):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "DELETE FROM pending_registrations WHERE expires_at <= ?",
        (now,)
    )


def _exists(db, table: str, column: str, value: str) -> bool:
    # table and column are internal constants, never user input
    row = db.execute(
        f"SELECT 1 FROM {table} WHERE {column} = ? COLLATE NOCASE",
        (value,)
    ).fetchone()
    return row is not None


def authenticate_user(identifier: str, password: str):
    identifier = identifier.strip()

    # Never feed oversized input to the hasher
    if len(password) > MAX_PASSWORD_LENGTH:
        return None

    # An identifier with "@" is an email, anything else a username
    column = "email" if "@" in identifier else "username"

    with get_db() as db:
        user = db.execute(
            f"""
            SELECT id, username, email, password_hash
            FROM users
            WHERE {column} = ? COLLATE NOCASE
            """,
            (identifier,)
        ).fetchone()

    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None

    if not verify_password(password, user["password_hash"]):
        return None

    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"]
    }


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)

    expires_at = (
        datetime.now(timezone.utc) + SESSION_DURATION
    ).isoformat()

    with get_db() as db:
        db.execute(
            """
            INSERT INTO sessions (token_hash, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, user_id, expires_at)
        )

    return token


def get_session_user(token: str):
    if not token:
        return None

    token_hash = hash_token(token)
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        session = db.execute(
            """
            SELECT users.id, users.username, users.email,
                   user_settings.theme, user_settings.font_size
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            LEFT JOIN user_settings ON user_settings.user_id = users.id
            WHERE sessions.token_hash = ?
              AND sessions.expires_at > ?
            """,
            (token_hash, now)
        ).fetchone()

    if session is None:
        return None

    return dict(session)


def revoke_session(token: str):
    if not token:
        return

    token_hash = hash_token(token)

    with get_db() as db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash = ?",
            (token_hash,)
        )


def cleanup_expired_sessions():
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        db.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (now,)
        )