# Gameplay logging for machine learning: data spec

**Status:** draft for review, not implemented yet.
**Goal:** record every decision players make in a round, so that models can later learn which white card
a player picks for a black card, and which card the others reward. Background and the modelling plan:
[emberisegellenes_kartyak_learning_data_collection.md](../emberisegellenes_kartyak_learning_data_collection.md).

Design rules:

- **Decisions with their alternatives.** A pick only means something next to the cards that weren't picked,
  so every pick is stored with the whole hand, and every vote with every submission it competed against.
- **Raw data only.** Card texts and IDs, never embeddings. Embeddings are derived later, so several
  embedding models can be compared on the same data.
- **Store what can't be recomputed, derive the rest.** Vote counts, winners, ties and scores all follow from
  the stored votes, so they aren't stored.
- **The game never waits for the log,** and a logging error never breaks a game.
- **Humans and models stay separable,** so a model never learns from an older model by accident.
- **Anonymous.** No player identifiers at all: the goal is a working MLflow pipeline on real data, not
  per-player personalization. Most games come from groups that play once anyway (60 days of room
  statistics: 92% of hosts played on a single day).

## What is recorded

| Moment in the game | Recorded |
|---|---|
| Game starts | settings (czar or voting, own card votable, rounds, timer, packs), app version |
| A player first appears in a game | seat number, human or which model |
| Card swap / new hand | which cards left the hand, which came in |
| Player submits | final hand in display order, the picked cards in blank order |
| Cards are revealed | the order the submissions were shown in |
| Czar picks / players vote | who voted for which submission (a czar pick is a single vote) |
| Round ends | black card, czar, outcome, number of players who could vote |

Not recorded: anything that identifies a player or device (usernames, IP addresses or their hashes,
session IDs, user agents), chat, voice, the exact start time, time spent looking at cards, mouse movement.

## Identifiers

- **Card text (`text_id`).** One ID per distinct card text, not per card. When a translation is reviewed or
  edited, the new wording gets a new `text_id`, so every round keeps exactly the text that was on screen.
  `card_id` links back to the card catalogue.
- **Seat.** A player's number within one game (0, 1, 2, ...). It links one player's hands, picks and votes
  across the rounds of a game, and means nothing outside it. 1 byte per row.
- **Model (`model_id`).** A model player's registered name and version (the MLflow model registry).

## Schema

A separate SQLite file, `gameplay.db` in the data directory, so it can be copied, backed up and exported
without touching the game's own databases.

```sql
-- Catalogue: one row per distinct card text that has been dealt.
CREATE TABLE card_text (
    text_id  INTEGER PRIMARY KEY,
    card_id  INTEGER NOT NULL,          -- card in the card catalogue
    black    INTEGER NOT NULL,          -- 1 black, 0 white
    pick     INTEGER NOT NULL,          -- black cards: number of blanks (1-3)
    text     TEXT    NOT NULL           -- exactly as shown
);
CREATE INDEX card_text_card ON card_text (card_id);

CREATE TABLE model  (model_id  INTEGER PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                     UNIQUE (name, version));

CREATE TABLE game (
    game_id     INTEGER PRIMARY KEY,
    day         INTEGER NOT NULL,       -- start date as days since 1970-01-01 (no time of day)
    vote_mode   INTEGER NOT NULL,       -- 0 czar, 1 everyone votes
    self_vote   INTEGER NOT NULL,       -- 1 if a player may vote for their own card
    rounds      INTEGER NOT NULL,
    timer       INTEGER NOT NULL,       -- seconds per phase
    editions    TEXT    NOT NULL,       -- selected pack IDs, e.g. '1,4'
    app_version TEXT                    -- commit the server ran (EEK_VERSION)
);

CREATE TABLE game_player (
    game_id   INTEGER NOT NULL,
    seat      INTEGER NOT NULL,
    model_id  INTEGER,                  -- NULL for a human player
    PRIMARY KEY (game_id, seat)
) WITHOUT ROWID;

CREATE TABLE round (
    game_id  INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    t        INTEGER NOT NULL,          -- seconds since game start
    black    INTEGER NOT NULL,          -- text_id of the black card
    czar     INTEGER,                   -- seat; NULL in vote mode
    outcome  INTEGER NOT NULL,          -- 0 decided, 1 nobody submitted, 2 nobody picked or voted, 3 game closed mid-round
    eligible INTEGER NOT NULL,          -- players who could vote (czar mode: 1)
    shown    BLOB,                      -- seats in the order the submissions were shown
    PRIMARY KEY (game_id, round_no)
) WITHOUT ROWID;

-- One row per player per round, including players who didn't submit (picks NULL).
CREATE TABLE decision (
    game_id  INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    seat     INTEGER NOT NULL,
    hand     BLOB    NOT NULL,          -- text_ids of the final hand, in display order
    picks    BLOB,                      -- text_ids picked, in blank order; NULL = no submission
    p_chosen REAL,                      -- models only: probability the model gave its pick
    probs    BLOB,                      -- models only: probability per hand card (float16, hand order)
    PRIMARY KEY (game_id, round_no, seat)
) WITHOUT ROWID;

-- Swaps and new hands before submitting: what the player rejected for this black card.
CREATE TABLE hand_change (
    game_id  INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    seat     INTEGER NOT NULL,
    seq      INTEGER NOT NULL,          -- order within the round
    removed  BLOB    NOT NULL,          -- text_ids (1 for a swap, the whole hand for a new hand)
    added    BLOB    NOT NULL,
    PRIMARY KEY (game_id, round_no, seat, seq)
) WITHOUT ROWID;

-- A czar pick is one vote by the czar; in vote mode every voter has a row.
CREATE TABLE vote (
    game_id  INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    voter    INTEGER NOT NULL,          -- seat
    target   INTEGER NOT NULL,          -- seat whose submission got the vote
    PRIMARY KEY (game_id, round_no, voter)
) WITHOUT ROWID;
```

### Why it is small and fast

- **ID lists as varint BLOBs.** A hand is one BLOB of 10 `text_id`s, each LEB128-encoded (2 bytes while
  IDs stay below 16,384), instead of 10 rows. Order is kept for free, which covers display order.
- **Seats** in the per-round tables: 1 byte each.
- **`WITHOUT ROWID` tables keyed by game.** All rows of a game sit together on disk, so exporting or reading
  a game is one range scan.
- **Card texts stored once** per wording; deduplicated in memory, so there's no index on the text itself.

Measured with a prototype of this schema, one month of simulated play (10,000 rounds, 3 players each,
30% vote mode) takes **about 1.5 MB**. The card catalogue adds a one-time 1.6 MB, about 1 MB with the
text index dropped. A year of play stays under 20 MB.

### What is derived, not stored

| Needed for training | Derived from |
|---|---|
| votes per submission, vote share | `vote` grouped by `target` |
| winner(s), ties | the most votes in `vote` |
| scores before a round | earlier rounds' winners (a tie splits the point) |
| number of players in a round | `decision` rows of the round |
| cards a player rejected | `hand_change.removed`, and the hand cards not picked |

## Write path

1. The game calls small, synchronous hooks (`game_started`, `hand_changed`, `submitted`, `revealed`, `voted`,
   `round_ended`) that only append to the room's in-memory buffer.
2. At `round_ended`, the round's rows go to a background writer, which commits them in one transaction:
   a round is either completely in the log or not at all. A room closed mid-round is written with outcome 3.
3. The writer catches and logs its own errors; a full disk or a locked file never reaches the game.
4. `EEK_GAMEPLAY_LOG=0` turns logging off without a deploy.

The export for training (Parquet files read by the MLflow experiments) is a separate script, planned
with the first model.

## Privacy

The log holds no identifier, not even a hashed one, and the start time is kept to the day only, so it
can't be matched to a room in the analytics database by timestamp. The data is anonymous. The
adatkezelés page gets one sentence about it (anonymous game statistics: which cards are played and which
win), with no new banner or setting.

## Known limits

- **No personalization across games.** A device ID could be added later; only data from then on would have it.
- **`app_version`** is `dev` in production until it moves into the container (Phase 2.4).

## Done when

- Each hook has a unit test, and the varint encoding has a round-trip test.
- The e2e browser test finds its full round in `gameplay.db`: hands, picks, shown order and the winning vote.
- A test shows a logging failure doesn't interrupt a game.
- The daily backup includes `gameplay.db`.
- The adatkezelés sentence is live.
