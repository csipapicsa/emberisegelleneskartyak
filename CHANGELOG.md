# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

The game has been live since April 2026; changes before this file are in the git history and in
[docs/worklogs/](docs/worklogs/).

## [Unreleased]

### Added
- CI on GitHub Actions: lint (ruff), Python and JavaScript tests, gitleaks secret scan over the full history,
  pip-audit; documentation-only PRs skip the slow jobs; weekly scheduled run.
- `scripts/make_sample_db.py`: a card database with made-up cards, so a fresh clone and CI can run without the real
  card texts.
- GPL-3.0-or-later license, `pyproject.toml`, pinned `requirements.txt` and `requirements-dev.txt`.

### Changed
- The `/admin/stats` key comes from the `EEK_ADMIN_STATS_KEY` environment variable and the page stays closed
  without it (it was hardcoded).
- Deployment templates moved to `deploy/`; dated session notes moved to `docs/worklogs/`.
- English README with architecture, quickstart and test instructions.

### Removed
- Card content from the repository: `database/card_master.db`, the retired `cards.db` and its copies,
  `duplicate_cards.csv`, the SEO PDFs.
