# Deployment Guide — Swing Trading Bot on TrueNAS Scale

This guide walks through deploying the Swing Trading Bot (Streamlit + APScheduler +
Gemini + Upstox) on a **TrueNAS SCALE 25.04** server accessed over **Tailscale**.

---

## Prerequisites

- TrueNAS SCALE 25.04 with **Apps** enabled (Docker runtime).
- An SSH session into TrueNAS (admin user with `sudo`).
- A **GitHub repository** containing this project (for auto-deploy webhook).
- **Upstox** account with a Developer App and an **Analytics Access Token** (read-only, 1 year).
- **Google AI Studio** API key (Gemini).
- **Tailscale** installed on TrueNAS and your client machine.

---

## 1. Enable Apps / Docker on TrueNAS SCALE

1. Go to **Apps → Settings → Advanced Settings**.
2. If prompted, choose the app pool and enable **Docker** runtime.
3. Go to **Apps → Discover Apps → Launch Docker Image**, or use **Custom App**.

> We will run `docker compose`, so use the TrueNAS **App** for the app *or* install the
> Docker CLI. The simplest supported path is the **Custom App** method in step 4.

---

## 2. Install Tailscale (if not already)

```bash
sudo tailscale up
```

Follow the login link printed in the terminal. After it connects, note the Tailscale IP:

```bash
tailscale ip -4
```

You will reach the UI at `http://<TAILSCALE_IP>:8501`.

---

## 3. Clone the repository

```bash
cd /mnt
sudo mkdir -p /mnt/swing_bot
sudo chown $(whoami) /mnt/swing_bot
cd /mnt/swing_bot
git clone https://github.com/<your-org>/<your-repo>.git .
```

> The webhook service binds the repo directory to `/app`. Keep this clone location fixed.

---

## 4. Create the `.env` file

```bash
cp .env.example .env
nano .env
```

Fill in real values:

```ini
# Upstox
UPSTOX_API_KEY=your_upstox_api_key_here
UPSTOX_ANALYTICS_TOKEN=YOUR_REAL_ANALYTICS_TOKEN

# Gemini
GEMINI_API_KEY=YOUR_REAL_GEMINI_KEY
GEMINI_MODEL=gemini-3.5-flash

# Webhook
WEBHOOK_SECRET=YOUR_LONG_RANDOM_SECRET
```

### Where to get the Upstox Analytics Token

1. Go to https://account.upstox.com/developer/apps and open/create your app.
2. Open the **Analytics** tab.
3. Click **Generate** and copy the token. It is read-only and valid for **1 year**.

> The standard OAuth access token expires at 3:30 AM IST daily and has **no refresh token**
> support — do **not** use it. The Analytics token avoids daily regeneration.

### Where to get the Gemini key

1. Go to https://aistudio.google.com/apikey and create a key.
2. Free tier allows ~20 `generate_content` calls/day on `gemini-3.5-flash`. For production
   (portfolio + top-10 BUY analysis daily), a **paid tier is recommended**.

---

## 5. Start the stack

```bash
cd /mnt/swing_bot
docker compose up -d --build
```

Verify:

```bash
docker compose ps
docker compose logs -f swing-bot
```

Expected: Streamlit listening on `0.0.0.0:8501`.

---

## 6. Verify over Tailscale

On your local machine:

1. Ensure Tailscale is running.
2. Open `http://<TAILSCALE_IP>:8501` in a browser.
3. You should see the **Swing Trading Bot** dashboard.

Test the health endpoint:

```bash
curl http://<TAILSCALE_IP>:8501/_stcore/health
# -> ok
```

---

## 7. Schedule & daily operation

- The APScheduler runs **weekdays at 3:45 PM IST** automatically (timezone `Asia/Kolkata`),
  fetching EOD data, computing technicals, fetching news, and asking Gemini.
- **Budget is daily:** defaults to `0`. Open the **Budget** tab, set today's amount, and click
  **Update Budget & Recompute BUY Analysis**. This runs Phase 1 (portfolio SELL/HOLD) and
  Phase 2 (budget-filtered BUY) and writes recommendations to SQLite.
- Review results under **Daily Recommendations**, mark Executed/Rejected after manually
  placing the AMO on Upstox.
- The **Ask Gemini** tab gives interactive Q&A grounded in today's recommendations.

> If you want a *scheduled* budget, set it each morning before 3:45 PM; the daily job reads
> the current value from the DB.

---

## 8. GitHub auto-deploy webhook

1. Create a GitHub webhook on your repo:
   - **Payload URL:** `http://<TAILSCALE_IP>:9000/hooks/deploy-hook`
   - **Content type:** `application/json`
   - **Secret:** the same `WEBHOOK_SECRET` you put in `.env`
   - **Events:** `push` (default)
2. `deploy.sh` runs on push: `git pull`, `docker compose build`, `docker compose up -d`.

> The `auto-deploy` container (`almir/webhook`) mounts the repo dir and Docker socket, reads
> `.env` for `WEBHOOK_SECRET`, and triggers `/app/deploy.sh`.

---

## 9. Updating manually

```bash
cd /mnt/swing_bot
git pull origin main
docker compose build swing-bot
docker compose up -d swing-bot
```

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `401` from Upstox | Analytics token wrong/expired. Regenerate in Developer Apps → Analytics. |
| `404 This model ... no longer available` | Model retired. Set `GEMINI_MODEL=gemini-3.5-flash` (or newer) in `.env`. |
| `429 RESOURCE_EXHAUSTED` | Free-tier Gemini quota (20/day). Wait for reset or upgrade to paid tier. |
| UI not reachable | Check `docker compose ps`; confirm Tailscale IP and port 8501. |
| Webhook not deploying | Confirm secret matches GitHub; check `docker compose logs auto-deploy`. |
| Database reset on restart | Data lives in `./data` (volume-mounted to `/app/data`). Ensure mount persists. |

---

## 11. Important security notes

- The Analytics token is **read-only** — it cannot place orders. Orders are **manual AMO**
  on the Upstox app, as designed.
- `.env` contains secrets and is excluded from git (see `.gitignore`).
- Ports 8501 and 9000 are only reachable via Tailscale (do **not** port-forward them).
