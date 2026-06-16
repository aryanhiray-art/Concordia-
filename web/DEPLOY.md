# Deploy the Concord member app to Vercel

The backend already runs on the forge VPS (`concord-api`, isolated `concord`
user). The browser only ever talks to this Vercel app over HTTPS; Vercel proxies
`/api` and the Robinhood OAuth callback to the forge backend server-side (see
`next.config.mjs`) — so there's no CORS, no mixed-content, and the OAuth
redirect is this app's own HTTPS URL.

## One-time

The forge API is exposed at **`http://<vps-ip>/concord-api`** (nginx → 127.0.0.1:8780).

1. **Deploy** this `web/` folder to Vercel (`vercel` / import the repo, root = `concord/web`).
2. **Set the Vercel project env var:**
   ```
   FORGE_BACKEND = http://<vps-ip>/concord-api
   ```
   (Leave `NEXT_PUBLIC_CONCORD_API` unset so the app calls same-origin `/api`.)
   Redeploy so the rewrite picks it up.
3. **Point the forge backend at the Vercel URL** (so the Robinhood OAuth redirect
   lands back on Vercel). On the VPS, in `/opt/concord/.env`:
   ```
   API_PUBLIC_URL=https://<your-app>.vercel.app
   WEB_ORIGIN=https://<your-app>.vercel.app
   ```
   then `systemctl restart concord-api`.

That's it. Share the invite code (`INVITE_CODE` in `/opt/concord/.env`) with members.

## Local dev

```
cp .env.local.example .env.local   # NEXT_PUBLIC_CONCORD_API=http://localhost:8780
npm install && npm run dev          # talks directly to a local backend on :8780
```

## Notes / hardening
- The forge API is currently plain HTTP on the VPS IP. The browser↔Vercel hop is
  HTTPS; the Vercel↔forge hop is server-to-server HTTP. To encrypt that hop too,
  point a subdomain at the VPS and run `certbot`, then set
  `FORGE_BACKEND=https://concord-api.<domain>`.
- Robinhood requires the member's account to be **Agentic-enabled** to trade.
