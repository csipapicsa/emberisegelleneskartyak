# Documentation

| | |
|---|---|
| [architecture.md](architecture.md) | how the game is built and run: components, game engine, data, interfaces, delivery |
| [TODO.md](TODO.md) | open work, in priority order |
| **operations/** | |
| [runbooks/deploy.md](runbooks/deploy.md) | releases, rollbacks, releasing the computer player, moving production into its container |
| [backups.md](operations/backups.md) | daily database backups and how to restore |
| [privacy.md](operations/privacy.md) | how the privacy notice is implemented: pseudonyms, retention |
| [../deploy/containers/](../deploy/containers/README.md) | staging and production containers: setup and day-to-day commands |
| **features/** | |
| [analytics.md](features/analytics.md) | internal usage statistics and the admin page |
| [card-ideas.md](features/card-ideas.md) | community card ideas, voting and moderation |
| [voice.md](features/voice.md) | WebRTC voice chat design |
| [ads/](features/ads/plan.md) | ad slot and analytics rollout plan (the privacy notice text itself is the site's Hungarian copy) |
| **ml/** | |
| [roadmap.md](ml/roadmap.md) | the computer player's ML lifecycle: architecture, milestones, evaluation |
| [computer-player.md](ml/computer-player.md) | the computer player in the game: serving, keeping its data apart, promotion, release |
| [implementation-plan.md](ml/implementation-plan.md) | the working checklist: tasks per milestone, who does what, done-when checks |
| [pipeline.md](ml/pipeline.md) | running the pipeline: setup, export, validation, embedding benchmark |
| [gameplay-logging.md](ml/gameplay-logging.md) | the anonymous gameplay log: what is recorded, schema, privacy |
| [data-collection-plan.md](ml/data-collection-plan.md) | background: which data a computer player needs and why |
| **worklogs/** | dated notes on finished work, including the original June 2026 specification |

Documentation is in English. The game's own text stays Hungarian (the UI, and the privacy notice shown on the
site), as do some dated worklogs from mid-2026, kept as they were written.
