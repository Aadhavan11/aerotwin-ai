# Deploying AeroTwin AI to aerotwin.agprojects.us (Render free tier)

One Render "web service" runs everything: the website and the API. It is built from the `Dockerfile` in this repo and configured by `render.yaml`.

## 1. Put the code on GitHub

The repo's `origin` is `Aadhavan11/aerotwin-ai`. Commit your work, then push it to the branch Render will watch (normally `main`):

    git push origin main

## 2. Create a Render account

Go to https://render.com and sign up (GitHub login is easiest). The free plan does not ask for a credit card.

## 3. Create the service from the blueprint

1. In the Render dashboard click **New > Blueprint**.
2. Connect your GitHub account and pick `Aadhavan11/aerotwin-ai`.
3. Render reads `render.yaml` and shows one service called `aerotwin`.
4. It asks for the secret values:
   - `AUTH_USER`: a username for the site login, for example `aerotwin`.
   - `AUTH_PASS`: a strong password (a long random phrase; use a password manager). Everyone who opens the site needs this login.
   - `ANTHROPIC_API_KEY`: leave it empty for the first deploy. The app then runs in scripted mode (no AI calls, no cost).
5. Click **Apply**. The first build takes several minutes (it builds the website and the Python image).

Check it works at `https://<service>.onrender.com` (the address Render shows). You should get a browser login prompt.

## 4. Add the AI key later (optional)

Render dashboard > your service > **Environment** > add `ANTHROPIC_API_KEY` with your key (it is stored as a secret) > Save. Render redeploys. The app limits AI requests to 6 per minute per visitor and 200 per day overall (`LLM_RATE_PER_MIN`, `LLM_DAILY_CAP`) so the key cannot run up a large bill.

## 5. Custom domain aerotwin.agprojects.us

Do the Render side first:

1. Service > **Settings > Custom Domains > Add Custom Domain**, enter `aerotwin.agprojects.us`. (The blueprint already lists it; add it here if it is not shown.)
2. Render shows the CNAME target, which is `<service>.onrender.com`.

Then Porkbun (DNS for agprojects.us):

1. Log in to Porkbun > **Domain Management** > find `agprojects.us` > **DNS**, and add a record:
   - Type: `CNAME`
   - Host: `aerotwin` (Porkbun adds `.agprojects.us` for you)
   - Answer: `<service>.onrender.com` (your real Render address)
   - TTL: `600`
2. Delete any existing record for the `aerotwin` host that conflicts. Porkbun's default records for the bare domain and the `*` wildcard can stay. (A subdomain is used because it is the simplest setup and keeps the bare domain free.)
3. If you have `CAA` records, one must allow `letsencrypt.org`, or the certificate cannot be issued.
4. Back in Render click **Verify** next to the domain. DNS can take a few minutes up to an hour. The HTTPS certificate is issued automatically.

## 6. What to expect on the free tier

- **Sleep:** after about 15 minutes with no visits the service sleeps. The next visit takes roughly 30 to 60 seconds to wake up.
- **No permanent disk:** the database is rebuilt from the golden scenario at build time. Anything changed in the app (work orders, simulator state) resets whenever the service wakes up or is redeployed. This is by design for a demo.

## 7. Run the production wrapper locally

    cd web && npm ci && npm run build && cd ..
    pip install -r requirements-prod.txt
    python -m scripts.reset_db --scenario golden
    AUTH_USER=demo AUTH_PASS=demo-pass uvicorn app.serve:app --port 10000

Open http://localhost:10000 . The API docs are at http://localhost:10000/api/docs. Leave `AUTH_USER` / `AUTH_PASS` unset to run without a login.
