# Vercel container deployment

The full stack runs in a Vercel Container Runtime function: a public HTTP proxy, a private FastAPI gateway/ERP, and private n8n. Builds run on Vercel; Docker does not need to be installed on the operator's laptop. This is an HTTP-only demonstration with no scheduled workflows, production bookings, or exposed n8n editor.

## Configuration

Link the repository to a Vercel project, select the Hobby plan, and attach a Neon Free PostgreSQL resource to the production environment. Set these production environment variables using the dashboard or `vercel env add` with stdin:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Pooled PostgreSQL URL for gateway transactions |
| `DATABASE_URL_UNPOOLED` | Direct PostgreSQL URL for n8n and the startup lock |
| `GROQ_API_KEY` | Server-only Groq key with access to `openai/gpt-oss-120b` |
| `WORKFLOW_SERVICE_TOKEN` | Internal workflow authentication, at least 32 characters |
| `REVIEWER_SECRET` | Internal human decision service, at least 32 characters |
| `SESSION_SECRET` | Cookie/CSRF ownership, at least 32 characters |
| `N8N_ENCRYPTION_KEY` | Stable n8n credential encryption key |
| `PUBLIC_ORIGIN` | Exact HTTPS production origin, without a trailing slash |
| `PORT` | `8080` |

Generate the four secrets with `python scripts/setup.py`, keep them private and stable across deployments, and enter the Groq key locally. Never commit `.env`, `.env.cloud`, or `.vercel/`. Use a 300-second function limit in the project's Functions settings and the Frankfurt region. Disable deployment authentication for the public demo; the editor and service routes remain hidden by the application's allowlist.

Deploy with `vercel deploy --prod`. `Dockerfile.vercel` selects Container Runtime. On startup, a direct PostgreSQL advisory lock serializes schema creation and official n8n credential/workflow import. A private fingerprint marker skips imports when the workflow and credentials have not changed. Temporary credential files are deleted, execution payload saving is disabled, and n8n stores credentials encrypted in its own schema.

## Lifecycle

Vercel scales idle production containers down. The interface waits for the gateway and n8n readiness before enabling submission. Brief interpretation uses a synchronous authenticated n8n webhook; the public request remains open while the model returns a validated draft. The quote submission request stays in flight while n8n plans; this prevents relying on work after a serverless response has completed. One global interpretation/planning slot and transaction locks are shared in PostgreSQL even if the platform starts multiple instances. This deployment supports the two exported HTTP workflows; scheduled triggers, queue workers, arbitrary workflows, and public editor access require a different hosting design.

Sessions, synthetic quotes, decisions, and orders survive instance restarts. Visitors must use the canonical production origin because CSRF checks bind mutations to it. Preview deployments are not configured with production secrets. After changing configuration, redeploy and check health, an anonymous session, the actual model/tool chain, human decisions, rejection cases, and restart persistence before sharing the URL.

Vercel Hobby, Neon Free, and Groq have usage limits. A free demo is subject to cold starts and provider availability; exceeding limits can stop service. The interface returns an error instead of fabricating model results. Use synthetic briefs without personal information.

References: [Vercel Container Runtime](https://vercel.com/docs/functions/runtimes/container), [Vercel Hobby](https://vercel.com/docs/plans/hobby), [Neon integration](https://vercel.com/marketplace/neon/neon), [n8n database settings](https://docs.n8n.io/deploy/host-n8n/configure-n8n/choose-n8ns-database), [n8n CLI](https://docs.n8n.io/deploy/host-n8n/configure-n8n/use-the-command-line).
