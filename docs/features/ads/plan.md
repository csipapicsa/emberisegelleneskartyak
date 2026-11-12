# Umami analytics and AdSense preparation: plan

**Date:** 2026-10-16
**Site:** https://emberisegelleneskartyak.hu/
**Goal:** more accurate traffic and gameplay analytics; a responsible look at advertising; later a single,
unobtrusive banner on the home page only and, if it complies with the rules, on the join page.

> **Brief for the developer (Claude):** first audit the current application, the actual Umami version and how
> Google's policies apply. The numbers come from screenshots, not an API export; treat nothing here as a
> verified fact about the code or configuration. Give a short diagnosis and an itemised plan before changing
> code. **Don't activate real AdSense code, a CMP, auto ads or ad-related consent handling without the
> owner's explicit approval.**

## 1. Starting data (Umami, last 90 days)

| Metric | Reported value |
|---|---:|
| Visitors | 4,270 |
| Visits | 6,250 |
| Views | 39,600 |
| Bounce rate | 35% |
| Visit duration | 7 min 23 s |

**Note:** `Visitors`, `Visits`, `Views`, `Entry page`, `Exit page` and `Sources` are not interchangeable.
39,600 page views are **not** 39,600 ad impressions. The 7m23s is **not** proven active play time: Umami's
duration calculation and single-page game sessions need a separate measurement.

### Pages: path, visitors (not views)

| Path | Visitors |
|---|---:|
| `/` | 3,530 |
| `/join` | 2,260 |
| `/create` | 1,290 |
| `/kartyak` | 374 |
| `/kartyaotlet.html` | 149 |
| `/kartyaszavazas` | 54 |

`/game/NNNN` and `/room/NNNN` appear as separate paths, so the top 10 underestimates these two **page groups**.
These numbers are not a conversion funnel; one visitor can appear on several paths.

### Entry/exit and URL tabs: examples

- Entry path `/`: about **3,240 (81%)**; `/join`: **611 (15%)**; `/create`: **98 (2%)**.
- Exit path `/`: about **1,900 (70%)**; `/join`: **611 (23%)**; `/create`: **66 (2%)**.
- On the URL tab `/` about **3,450 (48%)**, `/join` **1,690 (23%)**, `/create` **1,280 (18%)**; query strings
  group differently here.
- Per-edition card URLs too, e.g. `/kartyak?edition=5`.

**Careful:** these panels use different denominators and Umami's own "Visitors" breakdowns. Don't read the
percentages as a classic user funnel or as the distribution of the 39,600 views.

### Traffic channels (from the screenshot)

| Channel | Visitors | Share |
|---|---:|---:|
| Organic search | 2,310 | 47% |
| Direct | 2,180 | 45% |
| Paid ads | 220 | 5% |
| Organic social | 131 | 3% |
| Referral | 18 | 0% |
| LLM | 3 | 0% |

Search (mainly Google) and direct bring most of the traffic. `Paid ads` is a **source of visits**, **not**
revenue. Direct doesn't only mean typed URLs or proven returning visitors. Part of the LLM traffic may lack a
referrer.

## 2. Business and UX decision: one unobtrusive ad only

**Owner's decision:**

1. Ads **only** on the home page (`/`), and conditionally on the join page (`/join`).
2. **No ads** in a game (`/game/*`), in the lobby (`/room/*`), in the card browser (`/kartyak*`), on card
   ideas, voting, room creation (`/create`) or anywhere else.
3. At most **one** mobile-friendly banner low on the allowed pages; it must not hurt the experience.
4. **No AdSense Auto ads** at first, especially no automatic overlays, anchors or vignettes, which could
   appear where we don't want them. Loading and the ad slot are limited by a route whitelist.
5. The game rules, the WebSocket protocol and the card content are **not changed** for monetisation.

**Compliance:** keeping cards off a page may lower the risk of ads next to problematic content, but it does
**not guarantee** site-level AdSense approval. `/join`, a purely functional page (form/navigation), may be
problematic as **low-value content**. If so, ads go on the **home page only**; no artificial SEO text just for
the ad.

## 3. First: audit the current Umami setup

- Which Umami version runs? Cloud or self-hosted? Which tracking script?
- Which pages load the tracker? Any duplicate script or double page view? Does it work on mobile, in private
  mode, with an ad blocker, on reload?
- Which query strings and room codes does it collect? Any unnecessary parameters, personal data or random
  room codes?
- Why is the Views/Visits ratio so high: page loads, route changes, events or a measurement error? Investigate,
  don't guess.
- How many **Views** really go to `/`, `/join`, `/create`, `/room/*`, `/game/*`, `/kartyak*`? From an export/API
  or a reliable aggregation.
- Do all screenshots use the same date range and time zone?
- Does the rendered home page or `/join` contain random cards, explicit text, chat or other content that
  affects ad compliance?

**First deliverable:** an audit summary `current state → problem → proposed fix → risk → priority`, no changes.

## 4. Normalised paths and reliable view reports

Proposed **analytics groups** (the real routes stay as they are):

| Real URL | Analytics group |
|---|---|
| `/` | `/` |
| `/join` | `/join` |
| `/create` | `/create` |
| `/room/6454` | `/room/:code` |
| `/game/6920` | `/game/:code` |
| `/kartyak?edition=5` | `/kartyak` (edition as an event property, if useful) |
| `/kartyaotlet.html` | `/kartyaotlet.html` |
| `/kartyaszavazas` | `/kartyaszavazas` |

Options, depending on the actual Umami version:

- First check whether Umami's **Breakdown / filter / API** can already group old data correctly. Don't modify
  historical data if possible.
- For new page views, if needed, normalise **only the URL field of the analytics payload** with the tracker's
  `data-before-send` hook; never change the browser's real URL, the app's routing, invite links or room codes.
- Alternatively, depending on the version: `data-auto-pageview="false"` with Umami's documented
  `umami.track(props => ({...props, url: normalizedUrl}))`. **Exactly one** page view per real page load or
  route change, no duplicates.
- `data-auto-track="false"` is **not** the same as `data-auto-pageview="false"`; the latter needs a recent
  enough Umami.
- No room code, session UUID, name, free text or card text in tracking URLs or event payloads. Low-cardinality
  labels only.
- A fallback for other URLs: no silent data loss.

**Acceptance:** 10 different `/game/NNNN` and 10 different `/room/NNNN` visits land in one page group each,
while every real invite link and game room keeps working. No duplicate page views, no leaked room codes.

## 5. Event analytics: what is the game actually used for?

Tie events to **existing, successful server confirmations or state changes**, not just button presses. Don't
change the WebSocket protocol or the gameplay only for measurement.

### Proposed initial event schema

| Event | When (once) | Useful, non-sensitive property |
|---|---|---|
| `room_created` | Room created successfully | `source_page` |
| `room_joined` | Really joined | `entry_method` (code/link, if reliably known) |
| `game_started` | Confirmed game start | `player_count`, `voting_mode`, `planned_rounds` |
| `round_completed` | A finished round's state | `round_number`, `player_count` |
| `game_finished` | Server-side end of game | `completed_rounds`, `player_count` |
| `game_abandoned` | **Only if reliably determined** | an aggregated reason, if possible |
| `cards_browse_opened` | Card browser opened | `edition_id` (public catalogue ID) |

**Duplicates:** if every player's client sends `game_started`, it counts **player participations**, not
games. Two KPIs are needed:

1. **Player participation:** how many players joined/took part; client-side Umami events can estimate it.
2. **Unique games:** how many rooms really started a game, preferably from a minimal, aggregated server-side
   counter, or a single designated sender with reconnect/idempotency handling. **Don't** send the room code
   or session ID to Umami just to deduplicate.

Reconnects, repeated `room_state`/`round_end` messages and page transitions must not double-count
`round_completed` and `game_finished`. If exact unique-game counts need a persistent aggregated counter,
propose a minimal solution and ask for approval first; don't restructure the in-memory state without reason.

### Useful KPIs

- Page views per path group (`/`, `/join`, `/create`, `/room/*`, `/game/*`, `/kartyak*`).
- The share of home page visitors who start joining or creating a room **at least once** (well-defined
  measure and denominator).
- Successful join / create rates, where a reliable counter exists.
- Games started; players per game; rounds completed; share of games finished.
- Country, device type, channel (aggregated).
- Return/retention, if the Umami version and privacy settings measure it reliably.

### Real activity time

Don't call Umami's `Visit duration` "average play time": a WebSocket game can stay on one page for a long
time. If activity time is needed, send only **aggregated, rounded duration bands** (e.g. 0–5, 5–15, 15–30,
30+ min) based on `visibilitychange`/focus and *real game interactions*, no per-second heartbeat. Define the
KPI, its reliability and its privacy impact first.

## 6. The AdSense trial: compliance first, ad code second

### Ad slot whitelist

**Planned allowed paths:** `/`; `/join` **only if it meets the content-value policy**. No ad unit is loaded on
any other route.

### Required compliance audit

1. **Site and page content:** Google forbids monetising sexually explicit pages and may restrict ads next to
   sexual, shocking or profane content. Both the card game's general theme and what actually appears on the
   pages matter; ad-free game pages are **not** an automatic approval.
2. **`/join` as low-value content:** pages that only serve navigation, login or code entry may be unsuitable
   for Google ads. If so, skip `/join` and tell the owner clearly.
3. **EEA/UK/Switzerland consent:** Google requires a certified, IAB TCF-integrated CMP. Check the Google
   Privacy & messaging CMP setup, what loads before consent, and the privacy notice update. "Display-only"
   ads don't remove the privacy obligations.
4. **Policy and UX:** nothing that invites accidental clicks, covers navigation or join buttons, imitates a
   system message or misleads. Check mobile safe areas, screen height, the open keyboard and layout shift.
5. **A static placeholder first** (internal test) on the allowed page; real ads only with explicit permission
   and after AdSense approval.

**Banner design:** one unobtrusive, responsive slot low on the page / above the footer. Not sticky, never
covering the screen. Fewer people scroll down to it, so fewer ads will be seen; that's an accepted UX
trade-off: no forced scrolling or artificial refreshes to raise the numbers.

### Measuring impressions and revenue

- AdSense pays mainly for **ad impressions**; revenue can come without clicks, but there's no fixed amount
  per impression.
- **Umami:** estimates page views and visitors that could carry an ad.
- **AdSense:** verified ad impressions, coverage/fill, actual revenue, Page RPM / Impression RPM and policy
  restrictions are the authoritative revenue data.
- Never present Umami page loads as AdSense impressions or revenue. No artificial ad refresh.

## 7. Revenue estimate: a scenario, not a promise

An earlier, purely illustrative full-site Page RPM calculation from the **39,600 views**:

| Assumed Page RPM | 90 days, if every view could carry an ad |
|---|---:|
| EUR 0.50 | EUR 19.80 |
| EUR 1.00 | EUR 39.60 |
| EUR 2.00 | EUR 79.20 |
| EUR 5.00 | EUR 198.00 |

This is **not** a forecast for the real site: there would be one banner, only on `/` and maybe `/join`, and
the game's theme, consent, ad blockers and policy restrictions all reduce what can be monetised. EUR 5–40 per
90 days came up earlier as an order of magnitude, without enough data behind it. **Next:** measure the real
**views** of `/` and `/join`, by country and device; after an approval, estimate RPM from AdSense's own data.

Only with matching definitions: `eligible_pageviews / 1000 × realized_page_RPM`. If Google already reports a
page-level Page RPM, don't multiply again by the number of ads or the impression rate. Impression RPM has a
different denominator.

## 8. Order of work and acceptance criteria

### P0: audit (read only)

- [ ] Git status, current files, system and Umami versions.
- [ ] Audit of the tracker, routes, analytics events and the pages' real rendered content.
- [ ] A views export per path group for the same period; save the earlier data.
- [ ] AdSense policy check for `/` and `/join`, especially low-value and explicit content.
- [ ] A short audit report with the risks.

### P1: fix the Umami measurement

- [ ] Normalised paths for new data / careful aggregation for old data.
- [ ] One page view per real page load; no duplicates.
- [ ] Minimal, privacy-safe game events with exact definitions.
- [ ] A checked dashboard/export: views, visitors and events separately, with correct denominators.
- [ ] Tests: reconnect, mobile, several players, reload, URL query, cookie/consent.

### P2: prepare ads (without activating them)

- [ ] Ad route whitelist, off by default.
- [ ] One mobile-friendly placeholder slot; never on a game page.
- [ ] Review the Google CMP / privacy notice / script-consent order.
- [ ] AdSense sign-up and review, as the owner decides.

### P3: after a separate approval

- [ ] A real AdSense unit with a proper CMP, on allowed pages only.
- [ ] Test: no ad-network request on `/game/*`, `/room/*`, `/kartyak*`, `/create`.
- [ ] 2–4 weeks of real AdSense data: impressions, revenue, policy status, UX regressions, page speed.
- [ ] If `/join` doesn't qualify, ads on `/` only.

## 9. Operational safety: required limits

Project: `/opt/emberisegellenes_kartyak/`; AlmaLinux 9; nginx + FastAPI/uvicorn + native WebSockets; vanilla
HTML/CSS/JS; SQLite for cards and feedback; every active game lives **in memory**.

- **Read before changing.** Don't invent files or assume the old structure; look at the current code and
  configuration.
- **Don't refactor the gameplay.** Analytics only add; a broken tracker must never block creating or joining
  a room or playing.
- **No personal data or identity in Umami:** no session UUID, username, chat/feedback, card text, room code or
  raw WebSocket message. No hidden user tracking.
- **No separate staging (at the time).** Safe, reversible changes that don't affect current players first;
  diffs and backups. A backup before any risky server or database change.
- **A server restart ends the active games.** Don't restart automatically at peak time or without explicit
  approval. After frontend changes: cache versioning; after every change: functional and log checks.
- Ads and the CMP are a new external dependency; with a network failure or an ad blocker, the site and the
  game must keep working.
- After implementing: a short record of changed files, tests, results, open questions, rollback.

## 10. The assignment

> Review the current source and Umami analytics of Emberiség Ellenes Kártyák along this plan. First **read and
> audit only**, then make a prioritised, minimal-intervention plan. The first implementation: correct view
> reports, path normalisation and privacy-safe game events, keeping the multiplayer game working as it is.
> Ads only on the home page (`/`) and, if its content qualifies, the join page (`/join`), with a single bottom
> banner. **No ad unit on a game, the lobby, the card list or any other page.** Activating AdSense code and the
> CMP needs a separate approval. Show the findings, the files, the measurement definitions, the test plan and
> the risks first; verify approved changes with real data and application tests.

---

## Official documentation and policies (checked 2026-10-16)

- Umami tracker configuration: https://docs.umami.is/docs/tracker-configuration
- Umami tracker functions / pageviews: https://docs.umami.is/docs/tracker-functions
- Umami custom events: https://docs.umami.is/docs/track-events
- Umami metric definitions: https://docs.umami.is/docs/metric-definitions
- Umami Breakdown insight: https://docs.umami.is/docs/breakdown
- Google Publisher Policy, no/low-value content: https://support.google.com/publisherpolicies/answer/11112688?hl=en
- Google Publisher Policy, sexually explicit content: https://support.google.com/publisherpolicies/answer/10438119?hl=en
- Google Publisher Restrictions, sexual content: https://support.google.com/publisherpolicies/answer/10400657?hl=en
- Google Publisher Restrictions, shocking/profane content: https://support.google.com/publisherpolicies/answer/10437538?hl=en
- AdSense CMP rules for EEA/UK/CH: https://support.google.com/adsense/answer/13554116?hl=en
- Google AdSense impression-based payment FAQs: https://support.google.com/adsense/answer/14129646?hl=en

*A technical and business preparation plan, not legal advice and not a guarantee of Google's approval.*
