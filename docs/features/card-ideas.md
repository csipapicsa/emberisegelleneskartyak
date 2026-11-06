# Card Idea Submission

## Summary

Users can submit new white or black card ideas from `/kartyaotlet.html`.

The landing page first section links to the idea page with the text `Ötlet beküldés` and the URL:

```text
https://emberisegelleneskartyak.hu/kartyaotlet.html
```

## User Flow

1. The user opens `/kartyaotlet.html`.
2. The user chooses `Fehér kártya` or `Fekete kártya`.
3. A text input form is shown.
4. Black card submissions show this helper text:

```text
Ha helyet akarsz kihagyni a behelyettesítő kártyának, használj sok spacet.
```

5. The form posts JSON to `POST /api/card-ideas`.

## API

```http
POST /api/card-ideas
Content-Type: application/json
```

Body:

```json
{
  "card_type": "white",
  "text": "Beküldött kártyaszöveg"
}
```

`card_type` must be `white` or `black`. Empty text is rejected with HTTP 400. Request bodies with `Content-Length` greater than 4096 bytes are rejected with HTTP 413 before JSON parsing.

## Storage

Ideas are stored in `messages.db`, table `card_ideas`.

```sql
CREATE TABLE IF NOT EXISTS card_ideas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_type  TEXT NOT NULL CHECK (card_type IN ('white', 'black')),
    text       TEXT NOT NULL,
    "like"     INTEGER NOT NULL DEFAULT 0 CHECK ("like" >= 0),
    dislike    INTEGER NOT NULL DEFAULT 0 CHECK (dislike >= 0),
    created_at TEXT NOT NULL
);
```

The `"like"` and `dislike` counters default to `0`. They are present now so a later voting feature can increment them with whatever per-user voting constraint is chosen.

## Security Notes

- The backend validates `card_type` against an explicit allowlist.
- The endpoint accepts only JSON objects with string `card_type` and string `text` values.
- Malformed JSON and non-object JSON are rejected.
- Oversized requests are rejected when `Content-Length` is present.
- `deploy/nginx/emberisegellenes.conf` sets `client_max_body_size 16k;` so large request bodies can be rejected before reaching the app after nginx is reloaded with that config.
- Empty submissions are rejected.
- Text length is capped server-side: 280 characters for white cards, 360 for black cards.
- ASCII control characters are stripped except newline, carriage return, and tab.
- Submitted text is stored through parameterized SQLite queries.
- Submitted text is HTML-escaped before storage to prevent raw HTML/script content from being persisted and later rendered unsafely.
- Interior spaces are preserved, so black card blanks can be represented with multiple spaces.

This protects the card idea inputs from stored XSS, SQL injection, malformed JSON, and oversized normal requests. It does not by itself prevent spam or volumetric denial-of-service traffic; those require operational controls such as nginx rate limiting, a CAPTCHA, moderation, or per-user/per-IP submission limits.

## Tests

Coverage was added to `tests/test_messages.py` for:

- `card_ideas` table creation.
- `like` and `dislike` counter columns.
- default counter values.
- black-card interior spacing.
- XSS escaping.
- SQL injection payloads remaining plain stored text.
- non-string JSON field rejection.
- malformed JSON rejection.
- oversized request rejection.
- unsafe control-character stripping.
- invalid card type rejection.
- empty text rejection.
- `POST /api/card-ideas` success and validation behavior.

Verification run:

```text
venv/bin/pytest tests/test_messages.py
45 passed

venv/bin/pytest
182 passed
```
