"""Build a small card_master.db with made-up cards, for local development and CI.

The real database/card_master.db (the translated card texts) is not in the
repository. This script writes a stand-in with the same tables and views that
server/db.py reads, filled with original sample cards, so a fresh clone can
run the game and the test suite.

    python scripts/make_sample_db.py              # writes database/card_master.db
    python scripts/make_sample_db.py --out x.db   # somewhere else
    python scripts/make_sample_db.py --force      # replace an existing file

It refuses to overwrite an existing file without --force, so it can't wipe
the real card database on a server.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile

DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "..", "database", "card_master.db")

# Only the columns and views server/db.py uses. Both views are copied verbatim
# from the real file; tests/test_sample_db.py checks they still match it.
SCHEMA = """
CREATE TABLE editions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    sheet           TEXT,
    hun_name        TEXT,
    origin_language TEXT NOT NULL DEFAULT 'ENG' CHECK(origin_language IN ('ENG','HUN')),
    sort_order      INTEGER
);
CREATE TABLE cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL CHECK(type IN ('Black','White')),
    origin_language TEXT NOT NULL DEFAULT 'HUN' CHECK(origin_language IN ('ENG','HUN')),
    norm_key        TEXT NOT NULL DEFAULT '',
    eng             TEXT,
    hun_reviewed    TEXT,
    validated       INTEGER NOT NULL DEFAULT 0 CHECK(validated IN (0,1)),
    skip            INTEGER NOT NULL DEFAULT 0 CHECK(skip IN (0,1)),
    pick            INTEGER,
    draw            INTEGER NOT NULL DEFAULT 0,
    duplicate_of    INTEGER REFERENCES cards(id)
);
CREATE TABLE card_editions (
    card_id    INTEGER NOT NULL REFERENCES cards(id),
    edition_id INTEGER NOT NULL REFERENCES editions(id),
    PRIMARY KEY(card_id, edition_id)
);
CREATE VIEW game_cards AS
    SELECT id AS card_id, type, pick, draw, hun_reviewed AS text, eng
    FROM cards
    WHERE validated = 1 AND skip = 0 AND duplicate_of IS NULL;
CREATE VIEW edition_status AS
    SELECT e.id AS edition_id, coalesce(e.hun_name, e.name) AS name, e.origin_language, e.sort_order,
           count(*) AS cards,
           sum(c.validated) AS playable,
           sum(c.validated AND c.type = 'Black') AS playable_black,
           sum(c.validated AND c.type = 'White') AS playable_white,
           count(*) - sum(c.validated) AS missing,
           count(*) = sum(c.validated) AS complete
    FROM editions e
    JOIN card_editions ce ON ce.edition_id = e.id
    JOIN cards c ON c.id = ce.card_id AND c.skip = 0 AND c.duplicate_of IS NULL
    GROUP BY e.id;
"""

BLANK = "________"  # exactly 8 underscores, as in the real data

# (text, pick). A black card without a blank is a question (pick 1).
BASE_BLACK = [
    (f"A hétfő reggelt csak {BLANK} teheti elviselhetővé.", 1),
    ("Mi van a nagymama pincéjében?", 1),
    (f"Az új rendelet szerint {BLANK} mostantól kötelező.", 1),
    (f"A tanár azért késett, mert {BLANK}.", 1),
    ("Mi a titkos összetevő a pörköltben?", 1),
    (f"A céges csapatépítő fénypontja {BLANK} volt.", 1),
    (f"{BLANK}: a magyar foci jövője.", 1),
    (f"Első randin soha ne hozd szóba {BLANK}-t.", 1),
    (f"{BLANK} + {BLANK} = igaz szerelem.", 2),
    (f"Először {BLANK}, aztán {BLANK}. Klasszikus péntek este.", 2),
]
EXTRA_BLACK = [
    ("Mi tartja életben a Balatont augusztusban?", 1),
    (f"Az önéletrajzomban szerepel: {BLANK}.", 1),
    ("Mit nem próbált még ki a tömegközlekedés?", 1),
    (f"A lakócsoport ma {BLANK} miatt vitatkozik.", 1),
    ("Mit rejt a fiók, amit senki sem nyit ki?", 1),
    (f"A szomszéd hajnalban megint {BLANK} miatt kopogott.", 1),
]
BASE_WHITE = [
    "Egy kóbor macska", "A nagynéném receptje", "Három hét szabadság", "Egy rossz szóvicc",
    "A lottó ötös", "Egy lapos sör", "A tegnapi pizza maradéka", "Egy elfelejtett jelszó",
    "Hajnali négyórás ébredés", "Egy túl hosszú hangüzenet", "A szomszéd fúrógépe", "Egy büdös zokni",
    "Az anyós váratlan látogatása", "Egy lejárt kupon", "Túl sok fokhagyma", "A végtelen Excel-tábla",
    "Egy dühös hattyú", "Egy rosszul időzített tüsszentés", "A pótkulcs, ami sosem volt meg",
    "Egy önjelölt influenszer", "Kétes eredetű lángos", "A hétvégi kerti parti", "Egy beragadt lift",
    "Az ingyen kóstoló a boltban", "Egy rejtélyes számla", "A közös családi naptár", "Egy kínos csend",
    "A wifi jelszava", "Egy elszabadult roller", "A mikró csipogása", "Egy túl kicsi kabát",
    "Egy túlbuzgó porszívórobot", "Egy elfelejtett születésnap", "A válasz mindenkinek gomb",
    "Egy kiégett izzó", "A konyhai mérleg igazsága", "Egy zenélő képeslap", "Az utolsó szelet torta",
    "Egy titokzatos kulcscsomó", "A nyaralós diavetítés",
]
EXTRA_WHITE = [
    "Egy agresszív galamb", "Az elmaradt edzés", "Egy fél pár kesztyű", "A kockás pléd",
    "A tökéletes parkolóhely", "Egy rossz döntés éjfélkor", "Az ingyenes próbaidőszak",
    "Egy giccses hűtőmágnes", "A tavalyi újévi fogadalom", "Egy hangos kakas", "A szomszéd kutyája",
    "Egy túlárazott kávé", "A kölcsönkért fűnyíró", "Egy lemerült telefon", "A titkos családi pletyka",
    "Egy hibás GPS", "Egy pohár langyos víz", "A gyerekkori naplóm", "Egy kétméteres plüssmackó",
    "A vasárnapi ebéd", "Egy rosszul sikerült frizura", "A munkahelyi mikró", "Egy késő vonat",
    "Egy megsértődött kolléga", "A kerti törpe", "Egy titokban megevett csoki",
    "A végtelen felhasználási feltételek", "Egy kéretlen tanács", "Egy elveszett távirányító",
]
SHARED_WHITE = "Egy nyitva felejtett hűtő"   # in both complete packs: tests the DISTINCT deck query
DRAFT_WHITE = "Egy még le nem fordított kártya"  # not validated: its pack stays hidden


def build(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO editions (id, name, hun_name, origin_language, sort_order) VALUES (?, ?, ?, 'HUN', ?)",
            [(1, "Sample Base", "Minta alap", 1),
             (2, "Sample Extra", "Minta kiegészítő", 2),
             (3, "Sample Draft", "Minta félkész", 3)],
        )

        def add(card_type: str, text: str, pick: int | None, editions: list[int], validated: int = 1) -> None:
            cur = conn.execute(
                "INSERT INTO cards (type, norm_key, hun_reviewed, validated, pick) VALUES (?, ?, ?, ?, ?)",
                (card_type, text.lower(), text if validated else None, validated, pick),
            )
            conn.executemany(
                "INSERT INTO card_editions (card_id, edition_id) VALUES (?, ?)",
                [(cur.lastrowid, e) for e in editions],
            )

        for text, pick in BASE_BLACK:
            add("Black", text, pick if pick > 1 else None, [1])
        for text, pick in EXTRA_BLACK:
            add("Black", text, pick if pick > 1 else None, [2])
        for text in BASE_WHITE:
            add("White", text, None, [1])
        for text in EXTRA_WHITE:
            add("White", text, None, [2])
        add("White", SHARED_WHITE, None, [1, 2, 3])
        add("White", DRAFT_WHITE, None, [3], validated=0)
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the database")
    parser.add_argument("--force", action="store_true", help="replace an existing file")
    args = parser.parse_args(argv)

    out = os.path.abspath(args.out)
    if os.path.exists(out) and not args.force:
        print(f"{out} already exists; pass --force to replace it.", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # Build next to the target, then rename: the swap is atomic, and a running
    # server never opens a half-written file.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".tmp")
    os.close(fd)
    try:
        os.remove(tmp)
        build(tmp)
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
