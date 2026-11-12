# Privacy: implementation plan

## Goal

Keep the data processing the site needs to work, minimise the IP addresses used for statistics and for
card-idea voting, and publish an accurate, easy-to-find privacy notice.

## Steps

1. **IP minimisation and retention** (done).
   - Card-idea votes store a purpose-bound HMAC identifier instead of the raw IP.
   - Analytics store no player names, room codes or user agents; unique visitors are counted with a
     purpose-bound HMAC identifier.
   - Analytics events are kept for 90 days; at startup, old identifier columns that are no longer needed are
     deleted.
2. **Server configuration** (required before deploying).
   - Create `/etc/emberisegellenes/privacy.env` from `deploy/privacy.env.example`, with a strong, secret
     `EEK_PRIVACY_HMAC_SECRET`.
   - Never commit this secret, email it, or put it in the public notice.
   - Existing raw-IP votes are migrated to the HMAC at startup. Without the secret the service deliberately
     refuses to start, so an old schema storing raw IPs can't linger unnoticed.
3. **Public privacy notice** (in progress).
   - A separate `/adatkezeles` page and a footer link on every public page.
   - Purpose, legal basis and retention stated separately for gameplay, chat, contact messages, card ideas,
     voting and internal statistics.
   - `localStorage` (session, name and settings) described separately and plainly. The session needed for
     playing works without separate consent; the convenience settings need a review.
4. **Records and operations.**
   - A simple internal record of processing activities, and a data processing agreement with the hosting
     provider.
   - The actual retention of the nginx access and error logs written down.
   - Access rights, backups and the deletion process checked.

## Decided defaults

- Data controller contact: `gergo.gyori.project@gmail.com`.
- Hosting provider / processor: Contabo.
- Contact messages: 12 months.
- Public message board: 30 days; in-room chat: until the room closes.
- Card ideas: kept indefinitely as game content, not linked to the person who sent them.
- Card-idea vote HMACs: 5 years.
- Nginx access and error logs: 14 days (`deploy/logrotate/emberisegellenes`).

## Release checklist

- [ ] The secret HMAC configuration exists on the server.
- [ ] The drop-in `deploy/systemd/emberisegellenes.service.d/privacy.conf` is copied to
      `/etc/systemd/system/emberisegellenes.service.d/privacy.conf` (it doesn't override an existing
      TURN_SECRET).
- [ ] systemd and nginx configuration checked and reloaded.
- [ ] The logrotate file copied to `/etc/logrotate.d/emberisegellenes`.
- [ ] Every decision above filled in in the privacy notice.
- [ ] The notice is linked from the home page, the contact form and the card idea/voting pages.
- [ ] Data processing agreement and internal record done.
