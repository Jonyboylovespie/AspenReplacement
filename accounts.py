"""Verified Google accounts and revocable, persistent browser sessions."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time

SESSION_SECONDS = 30 * 24 * 60 * 60


def private_file(path, value):
    """Create a secret once without exposing it to other OS users."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(path, 0o600)
    else:
        with os.fdopen(fd, "wb") as file:
            file.write(value)
    return path.read_bytes()


class AccountDatabase:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "accounts.sqlite"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    subject TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                    student_oid TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, subject TEXT NOT NULL,
                    expires INTEGER NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def login(self, claims):
        if (not isinstance(claims.get("sub"), str) or not claims["sub"]
                or claims.get("email_verified") is not True
                or not isinstance(claims.get("email"), str) or not claims["email"]):
            raise ValueError("Google did not verify this account's email address.")
        token = secrets.token_urlsafe(48)
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO accounts(subject,email,name) VALUES(?,?,?) "
                       "ON CONFLICT(subject) DO UPDATE SET email=excluded.email,name=excluded.name",
                       (claims["sub"], claims["email"], claims.get("name", claims["email"])))
            db.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)",
                       (self.digest(token), claims["sub"], int(time.time()) + SESSION_SECONDS))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def account(self, token):
        if not isinstance(token, str) or not token:
            return None
        with closing(self.connect()) as db:
            row = db.execute("SELECT a.* FROM accounts a JOIN sessions s ON a.subject=s.subject "
                             "WHERE s.digest=? AND s.expires>?",
                             (self.digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        if token:
            with closing(self.connect()) as db, db:
                db.execute("DELETE FROM sessions WHERE digest=?", (self.digest(token),))

    def claim_student(self, subject, student_oid):
        """Bind an Aspen student to one Google account; never silently switch it."""
        if not isinstance(student_oid, str) or not student_oid.strip():
            raise ValueError("Aspen did not return a valid student account.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT student_oid FROM accounts WHERE subject=?", (subject,)).fetchone()
            if not row or row[0] not in (None, student_oid):
                raise ValueError("This Google account is already paired with a different Aspen student. Sign into Aspen with the student you connected first.")
            try:
                db.execute("UPDATE accounts SET student_oid=? WHERE subject=?", (student_oid, subject))
            except sqlite3.IntegrityError:
                raise ValueError("This Aspen student is already connected to another Google account.") from None

    def account_directory(self, subject):
        return self.directory / "users" / hashlib.sha256(subject.encode()).hexdigest()
