from __future__ import annotations
import sqlite3
import html
import os
from datetime import datetime, timezone

from server.privacy import PrivacyConfigurationError, pseudonymize_ip

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "messages.db")
CARD_IDEA_MAX_LENGTHS = {"white": 280, "black": 360}

# Synthetic "Ti küldtétek" community edition. These cards live in messages.db
# (card_ideas), not cards.db, so the edition is virtual:
#   COMMUNITY_EDITION_ID  — sentinel id never stored in cards.db.editions
#   COMMUNITY_ID_OFFSET   — added to card_ideas.id when building in-game card
#                           dicts so community ids can't collide with cards.db ids
COMMUNITY_EDITION_ID = -1
COMMUNITY_EDITION_NAME = "Ti küldtétek"
COMMUNITY_ID_OFFSET = 10_000_000
_CARD_TYPE_TO_GAME = {"black": "Black", "white": "White"}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_tables(conn: sqlite3.Connection, *, migrate_legacy_votes: bool = False) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS feedback_like (
            card_id    INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            PRIMARY KEY (card_id, session_id)
        );

        CREATE TABLE IF NOT EXISTS feedback_dislike (
            card_id    INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            PRIMARY KEY (card_id, session_id)
        );

        CREATE TABLE IF NOT EXISTS feedback_comment (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id    INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            username   TEXT NOT NULL,
            comment    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS card_ideas (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            card_type             TEXT NOT NULL CHECK (card_type IN ('white', 'black')),
            text                  TEXT NOT NULL,
            "like"                INTEGER NOT NULL DEFAULT 0 CHECK ("like" >= 0),
            dislike               INTEGER NOT NULL DEFAULT 0 CHECK (dislike >= 0),
            approved              INTEGER NOT NULL DEFAULT 0,
            seen_by_admin         INTEGER NOT NULL DEFAULT 0,
            number_of_white_cards INTEGER NOT NULL DEFAULT 1,
            created_at            TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS card_idea_votes (
            idea_id    INTEGER NOT NULL,
            voter_hash TEXT    NOT NULL,
            vote       TEXT    NOT NULL CHECK (vote IN ('like', 'dislike')),
            created_at TEXT    NOT NULL,
            PRIMARY KEY (idea_id, voter_hash)
        );
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(card_ideas)").fetchall()}
    if "like" not in columns:
        conn.execute('ALTER TABLE card_ideas ADD COLUMN "like" INTEGER NOT NULL DEFAULT 0 CHECK ("like" >= 0)')
    if "dislike" not in columns:
        conn.execute("ALTER TABLE card_ideas ADD COLUMN dislike INTEGER NOT NULL DEFAULT 0 CHECK (dislike >= 0)")
    if "approved" not in columns:
        conn.execute("ALTER TABLE card_ideas ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")
    if "seen_by_admin" not in columns:
        conn.execute("ALTER TABLE card_ideas ADD COLUMN seen_by_admin INTEGER NOT NULL DEFAULT 0")
    if "number_of_white_cards" not in columns:
        conn.execute("ALTER TABLE card_ideas ADD COLUMN number_of_white_cards INTEGER NOT NULL DEFAULT 1")
    _migrate_legacy_vote_ips(conn, enabled=migrate_legacy_votes)
    conn.commit()


def _migrate_legacy_vote_ips(conn: sqlite3.Connection, *, enabled: bool) -> None:
    """Replace the legacy raw-IP voting table with purpose-bound HMACs.

    This is intentionally opt-in so importing the application during tests
    cannot alter the production runtime database.  The real application calls
    it during its lifespan startup.
    """
    vote_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(card_idea_votes)").fetchall()
    }
    if "voter_ip" not in vote_columns:
        return
    if not enabled:
        return

    conn.execute("""
        CREATE TABLE card_idea_votes_hmac (
            idea_id    INTEGER NOT NULL,
            voter_hash TEXT    NOT NULL,
            vote       TEXT    NOT NULL CHECK (vote IN ('like', 'dislike')),
            created_at TEXT    NOT NULL,
            PRIMARY KEY (idea_id, voter_hash)
        )
    """)
    rows = conn.execute(
        "SELECT idea_id, voter_ip, vote, created_at FROM card_idea_votes"
    ).fetchall()
    try:
        conn.executemany(
            """INSERT INTO card_idea_votes_hmac (idea_id, voter_hash, vote, created_at)
               VALUES (?, ?, ?, ?)""",
            [
                (idea_id, pseudonymize_ip(voter_ip, "card-idea-vote"), vote, created_at)
                for idea_id, voter_ip, vote, created_at in rows
            ],
        )
    except PrivacyConfigurationError:
        conn.execute("DROP TABLE card_idea_votes_hmac")
        raise
    conn.execute("DROP TABLE card_idea_votes")
    conn.execute("ALTER TABLE card_idea_votes_hmac RENAME TO card_idea_votes")


def save_message(conn: sqlite3.Connection, name: str, email: str, message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO messages (name, email, message, created_at) VALUES (?, ?, ?, ?)",
        (name, email, message, now),
    )
    conn.commit()


def _strip_unsafe_controls(value: str) -> str:
    return "".join(
        char for char in value
        if char in ("\n", "\r", "\t") or (ord(char) >= 32 and ord(char) != 127)
    )


def sanitize_card_idea_text(card_type: str, text: str) -> str:
    if card_type not in CARD_IDEA_MAX_LENGTHS:
        raise ValueError("invalid card type")
    if not isinstance(text, str):
        raise TypeError("card idea text must be a string")

    clean_text = _strip_unsafe_controls(text).strip()
    clean_text = clean_text[:CARD_IDEA_MAX_LENGTHS[card_type]]
    return html.escape(clean_text, quote=True)


def save_card_idea(conn: sqlite3.Connection, card_type: str, text: str) -> bool:
    if not isinstance(card_type, str):
        raise TypeError("card type must be a string")
    clean_type = card_type.strip().lower()
    if clean_type not in CARD_IDEA_MAX_LENGTHS:
        raise ValueError("invalid card type")

    clean_text = sanitize_card_idea_text(clean_type, text)
    if not clean_text:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        'INSERT INTO card_ideas (card_type, text, "like", dislike, created_at) VALUES (?, ?, 0, 0, ?)',
        (clean_type, clean_text, now),
    )
    conn.commit()
    return True


def get_community_cards(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Build game-ready card dicts from every approved card idea.

    Returns (black, white) lists shaped like cards.db rows so they can be mixed
    into a normal deck: type 'Black'/'White', pick (black cards only, from
    number_of_white_cards), edition_id sentinel, community flag, and an id
    offset so it can't collide with cards.db ids. Only approved==1 rows are
    included so unreviewed submissions never show up in-game.
    """
    rows = conn.execute(
        "SELECT id, card_type, text, number_of_white_cards FROM card_ideas WHERE approved = 1"
    ).fetchall()
    black, white = [], []
    for r in rows:
        card = {
            "id": r["id"] + COMMUNITY_ID_OFFSET,
            "text": r["text"],
            "type": _CARD_TYPE_TO_GAME[r["card_type"]],
            "pick": r["number_of_white_cards"],
            "edition_id": COMMUNITY_EDITION_ID,
            "community": True,
        }
        (black if r["card_type"] == "black" else white).append(card)
    return black, white


def list_card_ideas(conn: sqlite3.Connection, voter_hash: str | None) -> list[dict]:
    """Every card idea with live like/dislike counts, this IP's own vote, and
    moderation status, newest submission first."""
    rows = conn.execute(
        """
        SELECT
            ci.id,
            ci.card_type,
            ci.text,
            ci.created_at,
            ci.approved,
            ci.seen_by_admin,
            COALESCE(SUM(v.vote = 'like'), 0)    AS likes,
            COALESCE(SUM(v.vote = 'dislike'), 0) AS dislikes,
            MAX(CASE WHEN v.voter_hash = ? THEN v.vote END) AS my_vote
        FROM card_ideas ci
        LEFT JOIN card_idea_votes v ON v.idea_id = ci.id
        GROUP BY ci.id
        ORDER BY ci.created_at DESC, ci.id DESC
        """,
        (voter_hash,),
    ).fetchall()
    return [dict(r) for r in rows]


def _refresh_idea_counts(conn: sqlite3.Connection, idea_id: int) -> dict:
    """Recompute and persist the denormalized like/dislike columns from votes."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(vote = 'like'), 0)    AS likes,
            COALESCE(SUM(vote = 'dislike'), 0) AS dislikes
        FROM card_idea_votes WHERE idea_id = ?
        """,
        (idea_id,),
    ).fetchone()
    likes, dislikes = row["likes"], row["dislikes"]
    conn.execute(
        'UPDATE card_ideas SET "like" = ?, dislike = ? WHERE id = ?',
        (likes, dislikes, idea_id),
    )
    return {"likes": likes, "dislikes": dislikes}


def vote_card_idea(conn: sqlite3.Connection, idea_id: int, voter_hash: str, vote: str) -> dict | None:
    """Cast one vote per IP per card. Toggling the same vote removes it; the
    opposite vote replaces it. Returns updated counts + my_vote, or None if the
    idea does not exist."""
    if vote not in ("like", "dislike"):
        raise ValueError("invalid vote")
    if not conn.execute("SELECT 1 FROM card_ideas WHERE id = ?", (idea_id,)).fetchone():
        return None

    existing = conn.execute(
        "SELECT vote FROM card_idea_votes WHERE idea_id = ? AND voter_hash = ?",
        (idea_id, voter_hash),
    ).fetchone()

    if existing and existing["vote"] == vote:
        # Same button again → un-vote
        conn.execute(
            "DELETE FROM card_idea_votes WHERE idea_id = ? AND voter_hash = ?",
            (idea_id, voter_hash),
        )
        my_vote = None
    else:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO card_idea_votes (idea_id, voter_hash, vote, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(idea_id, voter_hash) DO UPDATE SET vote = excluded.vote
            """,
            (idea_id, voter_hash, vote, now),
        )
        my_vote = vote

    counts = _refresh_idea_counts(conn, idea_id)
    conn.commit()
    return {"id": idea_id, "my_vote": my_vote, **counts}


def add_like(conn: sqlite3.Connection, card_id: int, session_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO feedback_like (card_id, session_id) VALUES (?, ?)",
        (card_id, session_id),
    )
    conn.execute(
        "DELETE FROM feedback_dislike WHERE card_id = ? AND session_id = ?",
        (card_id, session_id),
    )
    conn.commit()


def add_dislike(conn: sqlite3.Connection, card_id: int, session_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO feedback_dislike (card_id, session_id) VALUES (?, ?)",
        (card_id, session_id),
    )
    conn.execute(
        "DELETE FROM feedback_like WHERE card_id = ? AND session_id = ?",
        (card_id, session_id),
    )
    conn.commit()


def get_chat_messages(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    conn.execute("DELETE FROM chat_messages WHERE created_at < datetime('now', '-30 days')")
    conn.commit()
    rows = conn.execute(
        "SELECT id, username, message, created_at FROM chat_messages ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def post_chat_message(conn: sqlite3.Connection, username: str, message: str) -> None:
    clean_user = html.escape(username.strip()[:24])
    clean_msg  = html.escape(message.strip()[:500])
    if not clean_user or not clean_msg:
        return
    conn.execute(
        "INSERT INTO chat_messages (username, message) VALUES (?, ?)",
        (clean_user, clean_msg),
    )
    conn.commit()


def add_comment(conn: sqlite3.Connection, card_id: int, session_id: str, username: str, comment: str) -> None:
    clean = html.escape(comment.strip())[:500]
    if not clean:
        return
    conn.execute(
        "INSERT INTO feedback_comment (card_id, session_id, username, comment) VALUES (?, ?, ?, ?)",
        (card_id, session_id, username, clean),
    )
    conn.commit()
