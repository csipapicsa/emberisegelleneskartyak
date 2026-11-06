# Plan — Static, Printable Card-Pack PDFs (2026-07-16)

## Status

Planned. This document defines the implementation of downloadable,
print-ready PDFs for every card edition. It makes no runtime changes itself.

## Goal

Publish printable PDFs for every edition and card type. PDFs are generated in
a scheduled background job and saved in the static directory, so a visitor
downloads an already-created file instead of waiting for a web request to
render one.

| Format key | Player-facing label | Appearance | Contents |
| --- | --- | --- | --- |
| `white` | Fehér kártyák | White card, black text | White cards |
| `black` | Fekete kártyák | Black card, white text | Black cards |
| `black-white-bg` | Fekete kártyák – fehér háttér | White card, black text/border | Black cards; ink-saving |

The lobby will show one download control on the right of each edition. It
opens a short menu with the three formats. Choosing a format links directly to
the static PDF and starts a normal browser download.

## Product decisions

- Downloads are public; a player does not need to create or join a room.
- One file contains one edition and one card type. Room selections are not
  mixed into a single download.
- Only cards where `language = 'HUN'` are printed. This matches the playable
  deck and the counts already shown in the lobby.
- The virtual `Ti küldtétek` edition is included, using only
  `approved = 1` card ideas.
- Paper is A4 portrait. A card is poker-card size: 63 × 88 mm.
- Version one contains card fronts only, not printable card backs or
  duplex-print alignment.
- The nightly schedule is accepted for community content. A newly approved
  community card can take up to a day to appear in a PDF. A later enhancement
  can run the generator after moderation, with the nightly run retained as a
  safety net.

## Rationale for static generation

Pre-generation is the preferred design for this application:

- Download latency is just file transfer; Bōks does not need rendering while a
  player waits.
- The FastAPI process does not consume CPU or block on large PDF generation.
- A failed generation is visible in a systemd journal rather than as a
  user-facing download failure.
- Outputs are easy to inspect, cache, back up, or later serve from a CDN.

There are nine editions and three variants: 27 files. Bōks is the largest
edition, but a once-daily, off-request job is appropriate. The generator must
log duration, pages, and file size so this assumption is measurable.

## Planned files

```text
server/
  pdf_cards.py                    # reusable PDF layout/rendering library
  assets/fonts/
    NotoSans-Regular.ttf
    NotoSans-Bold.ttf
    OFL.txt                        # SIL Open Font License
scripts/
  generate_card_pdfs.py            # scheduled batch command
systemd/
  emberisegellenes-pdf-generation.service
  emberisegellenes-pdf-generation.timer
static/
  downloads/cards/                 # generated artefacts, not committed
    .gitkeep
    manifest.json
    magyar-alap-feher.pdf
    magyar-alap-fekete.pdf
    magyar-alap-fekete-feher-hatter.pdf
tests/
  test_pdf_cards.py
```

Add this to `.gitignore`:

```gitignore
static/downloads/cards/*.pdf
static/downloads/cards/manifest.json
```

Only `.gitkeep` is committed. Generated PDFs are binary deployment
artefacts and must not be committed into Git.

## Stable edition slugs and filenames

Never derive a download filename in the browser from the edition's display
name. Labels can be renamed and contain accented characters. Instead, add a
stable ASCII slug map alongside `ORDER` in `server/edition_display.py`.

| Raw database name | Display label | Slug |
| --- | --- | --- |
| `Magyar Alap` | Magyar Alap | `magyar-alap` |
| `Vegyes` | Vegyes | `vegyes` |
| `Ti küldtétek` | Ti küldtétek | `ti-kuldtetek` |
| `2nd Expansion` | Második kiadás | `masodik-kiadas` |
| `3rd Expansion` | Harmadik kiadás | `harmadik-kiadas` |
| `Negyedik (4th)` | Negyedik kiadás | `negyedik-kiadas` |
| `Ötödik (5th)` | Ötödik kiadás | `otodik-kiadas` |
| `Hatodik (6th)` | Hatodik kiadás | `hatodik-kiadas` |
| `Bōks` | Bōks | `boks` |

The generator and `/api/editions` use the same helper. For an unlisted
edition, use a safe fallback: Unicode normalisation and ASCII transliteration,
lowercase, non-alphanumerics collapsed to hyphens, and database ID appended
only if a collision occurs.

Output names are deterministic:

```text
{slug}-feher.pdf
{slug}-fekete.pdf
{slug}-fekete-feher-hatter.pdf
```

## Card data contract

Add a dedicated read-only printing query in `server/db.py`. Do not reuse a
query whose ordering is unspecified:

```sql
SELECT id, text, type, COALESCE(pick, 1) AS pick
FROM cards
WHERE edition_id = ?
  AND language = 'HUN'
  AND type = ?
ORDER BY id ASC;
```

- Bind all SQL parameters; never interpolate an edition ID or type.
- Include every playable `pick` value. For a black card with `pick > 1`,
  print a `Játssz ki: N` footer.
- For `Ti küldtétek`, call `messages_db.get_community_cards()` and select
  the requested type. Make its source deterministic by adding `ORDER BY id
  ASC` or sorting the returned cards by ID before rendering.
- The card text is data, not HTML. The PDF renderer must draw text directly,
  not feed community text to HTML/XML without escaping it.

## PDF renderer

### Dependency and font

Add the following runtime dependency:

```text
reportlab>=4.2
```

Use ReportLab because it works with the existing Python/FastAPI deployment,
does not require a browser or system HTML renderer, and supports exact
millimetre positioning. Bundle Noto Sans Regular and Bold under
`server/assets/fonts/` with the SIL Open Font License. Standard PDF fonts are
not enough to reliably render Hungarian characters such as `ő` and `ű`.

Font paths must resolve from the repository base directory, not from the
current working directory. Register the TrueType fonts once per process.

### Geometry

Use ReportLab's `mm` unit:

```python
PAGE_WIDTH, PAGE_HEIGHT = A4
CARD_WIDTH  = 63 * mm
CARD_HEIGHT = 88 * mm
COLUMNS, ROWS = 3, 3
GUTTER = 2 * mm
LEFT_MARGIN = (PAGE_WIDTH - COLUMNS * CARD_WIDTH - (COLUMNS - 1) * GUTTER) / 2
TOP_MARGIN = (PAGE_HEIGHT - ROWS * CARD_HEIGHT - (ROWS - 1) * GUTTER) / 2
```

This creates nine cards per page, with 8.5 mm left/right margins and 14.5 mm
top/bottom margins. Draw short, thin crop marks at card boundaries in the
gutter and page edges. Render cards row-major: left-to-right, then
top-to-bottom. The expected page count is `ceil(card_count / 9)`.

### Card appearance

Every card has:

- a rounded outline;
- an 8 mm inner text margin;
- bold wrapped card text;
- a small `Emberiség Ellenes Kártyák` mark and edition label in the footer;
- a `Játssz ki: N` label on multi-pick black cards.

| Variant | Background | Text | Border |
| --- | --- | --- | --- |
| White | white | black | black |
| Black | black | white | white |
| Black on white background | white | black | black, plus `FEKETE KÁRTYA` footer tag |

Preserve newline characters. Wrap by measured font width
(`pdfmetrics.stringWidth`), not character count. Start at 18 pt and reduce
in 0.5 pt steps to 8 pt. If a card still does not fit, use 7 pt and log the
card ID and edition. Never silently truncate content. Underscores in black
cards must remain visible as supplied.

### Library API

Keep rendering separate from file-system and HTTP concerns:

```python
def render_card_pdf(
    cards: list[dict],
    *,
    edition_name: str,
    variant: Literal['white', 'black', 'black-white-bg'],
) -> bytes:
    """Render one complete PDF document in memory."""
```

The batch script writes bytes to disk. This design makes the renderer
straightforward to test and avoids duplicating the layout if an authenticated
on-demand endpoint is needed in the future.

## Batch generator

Implement `scripts/generate_card_pdfs.py`, runnable both manually and by
systemd:

```bash
venv/bin/python scripts/generate_card_pdfs.py
```

Default workflow:

1. Open `cards.db` and `messages.db` with existing read-only helpers.
2. Load normal editions in `edition_display.sort_key` order, plus the
   community edition.
3. Generate the white, black, and ink-saving-black files for every edition.
4. Write each output to a same-directory `.tmp` filename, call `fsync`,
   then publish with `os.replace`. A static URL therefore resolves to either
   a complete old PDF or a complete new PDF, never a partial file.
5. Once *all* PDFs succeed, generate and atomically replace
   `manifest.json`. If any PDF fails, return non-zero and retain the old
   manifest, leaving the last complete set visible.
6. Delete PDFs for removed editions only after the new manifest is published.
7. Log timestamp, edition, variant, card count, page count, byte size,
   duration, and tracebacks on failure.

Support these flags for recovery and development:

```text
--output-dir PATH        # default: static/downloads/cards
--edition ID_OR_SLUG     # one edition only
--format FORMAT          # white, black, black-white-bg
--check                  # validate and render in memory; do not write files
```

The scheduled command has no filters and regenerates the complete collection.

### Manifest

Write UTF-8 `static/downloads/cards/manifest.json` after a complete batch:

```json
{
  "generated_at": "2026-07-16T03:20:12Z",
  "generator_version": 1,
  "editions": {
    "magyar-alap": {
      "name": "Magyar Alap",
      "id": 5,
      "white_count": 371,
      "black_count": 56,
      "downloads": {
        "white": "magyar-alap-feher.pdf",
        "black": "magyar-alap-fekete.pdf",
        "black-white-bg": "magyar-alap-fekete-feher-hatter.pdf"
      }
    }
  }
}
```

The manifest is an operator/debugging record. The frontend should receive
download URLs from `/api/editions`, not fetch and interpret the manifest.

## API and lobby changes

### Edition API

Preserve the existing edition fields: `id`, display `name`, `community`,
`black_count`, and `white_count`. Add:

```json
{
  "downloads": {
    "white": "/static/downloads/cards/magyar-alap-feher.pdf",
    "black": "/static/downloads/cards/magyar-alap-fekete.pdf",
    "black_white_bg": "/static/downloads/cards/magyar-alap-fekete-feher-hatter.pdf"
  },
  "downloads_available": true
}
```

Use underscore naming in JSON to match the Python/JavaScript convention; map
it to the generator's hyphenated format internally. Before the initial
generation, omit `downloads` or set `downloads_available: false`; the UI
must hide or disable the control rather than create 404 links.

### Lobby structure and accessibility

The existing `.select-option` is a `<label>` around the checkbox. Nesting a
button or menu inside it is invalid interactive HTML and could toggle a pack
while a user tries to download it. Change a rendered row to:

```html
<div class="select-option selected" data-id="5">
  <label class="edition-choice">
    <input type="checkbox" checked>
    <span>Magyar Alap <span class="edition-hint">(56 fekete, 371 fehér)</span></span>
  </label>
  <details class="edition-download">
    <summary aria-label="Magyar Alap PDF letöltése" title="Nyomtatható PDF">⇩</summary>
    <div class="edition-download-menu">
      <a href="..." download>Fehér kártyák</a>
      <a href="..." download>Fekete kártyák</a>
      <a href="..." download>Fekete – fehér háttér</a>
    </div>
  </details>
</div>
```

`<details>` provides keyboard operation without a custom menu script. CSS
must make `.edition-choice` flex to fill the row, keep the download control
at the far right, retain the selected border, offer at least 44 × 44 px touch
targets, and ensure the menu is not clipped by adjacent rows. The selection
change handler must react only to checkbox changes, not details/menu events.

Use same-origin `<a download>` links. Browsers normally download immediately;
a browser that opens its PDF viewer can still save a regular public static
file. Version one does not need a dynamic `Content-Disposition` endpoint.

## Systemd timer

The existing database backup runs daily at 03:05. Run PDF generation after it,
at 03:20.

`systemd/emberisegellenes-pdf-generation.service`:

```ini
[Unit]
Description=Generate printable Emberiség Ellenes Kártyák PDFs
After=local-fs.target

[Service]
Type=oneshot
User=adminuser_developer
WorkingDirectory=/opt/emberisegellenes_kartyak
ExecStart=/opt/emberisegellenes_kartyak/venv/bin/python /opt/emberisegellenes_kartyak/scripts/generate_card_pdfs.py
UMask=022
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/emberisegellenes_kartyak/static/downloads/cards
```

`systemd/emberisegellenes-pdf-generation.timer`:

```ini
[Unit]
Description=Daily generation of printable Emberiség Ellenes Kártyák PDFs

[Timer]
OnCalendar=*-*-* 03:20:00
Persistent=true
Unit=emberisegellenes-pdf-generation.service

[Install]
WantedBy=timers.target
```

`User=adminuser_developer` matches the running web service. Before enabling
the timer, ensure the output directory exists and belongs to that user. The
unit can read project files and databases but can write only the generated
output directory.

After an initial manual run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now emberisegellenes-pdf-generation.timer
systemctl list-timers emberisegellenes-pdf-generation.timer
```

Operations:

```bash
sudo systemctl start emberisegellenes-pdf-generation.service
sudo systemctl status emberisegellenes-pdf-generation.service
sudo journalctl -u emberisegellenes-pdf-generation.service -n 100 --no-pager
```

## Automated test plan

Add `tests/test_pdf_cards.py` using temporary SQLite databases and a
temporary output directory.

1. The regular-card query returns HUN cards only, separates types, preserves
   `pick`, and orders by ID.
2. Community rendering uses only approved cards and is deterministic.
3. Every variant starts with `%PDF-`, can be parsed, and has
   `ceil(card_count / 9)` pages.
4. White, black, and ink-saving-black templates use the expected colours and
   render a multi-pick footer.
5. Text containing `árvíztűrő tükörfúrógép őű` renders successfully with
   the bundled font.
6. A maximum-length card does not fail or truncate; a final font-size fallback
   is logged if required.
7. A fixture batch creates every expected PDF and a valid manifest.
8. An injected renderer failure leaves the previous manifest untouched and
   exits non-zero.
9. The edition API exposes valid URLs only after generated output exists.

Existing edition, card-count, and community tests must remain green. Run:

```bash
venv/bin/python -m pytest -q
```

Manual acceptance checks:

- The mobile and desktop menus are usable.
- Clicking download never changes a selected checkbox.
- Magyar Alap and Bōks downloads open and print correctly in every format.
- A physical A4 page has correct card dimensions, margins, and crop guides.
- A newly approved community card appears after a manual/scheduled generation.
- Static links remain available after a normal FastAPI service restart.

## Deployment and rollback

### Deployment

1. Add code, font assets, dependency, output directory, systemd units, and
   tests.
2. Install the Python dependency in the existing virtual environment.
3. Run the full test suite.
4. Run the generator manually and inspect its manifest and representative PDFs.
5. Deploy/restart `emberisegellenes.service` for the API and lobby change.
6. Reload systemd, enable the timer, and inspect its first successful journal
   entry.

Generated static files do not themselves require an app restart; the restart
is for the updated lobby JavaScript and edition API.

### Rollback

To remove the feature from the UI, remove the `downloads` metadata/control
and restart the web service. Existing static PDFs are harmless. To stop future
generation:

```bash
sudo systemctl disable --now emberisegellenes-pdf-generation.timer
```

Atomic individual file writes and publish-last manifest updates mean the last
complete PDF set remains available if a scheduled generation fails.

## Later enhancements

- Trigger generation immediately after community-card approval.
- Add card backs and duplex-print alignment instructions.
- Add a public download page in addition to lobby controls.
- Include checksums and file sizes in the manifest.
- Serve generated files through nginx with long immutable caching only after
  introducing versioned filenames. Do not use immutable caching with the
  initial daily-replaced filenames.
