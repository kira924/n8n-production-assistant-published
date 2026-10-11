# Docker setup

Prerequisites: Docker Engine/Desktop with Compose v2, Python 3.12+, and a Groq API key with access to `openai/gpt-oss-120b`.

## Configure and start locally

From the repository root:

```sh
python scripts/setup.py
```

Add `GROQ_API_KEY` to the generated `.env` locally. Keep this file untracked. The setup script generates fresh workflow, reviewer, session, and encryption secrets; it never overwrites an existing configuration.

```sh
docker compose up -d --build
```

Open `http://localhost:5688` and create the n8n owner account. Then configure the workflow:

```sh
docker compose exec -T n8n node /files/bootstrap.mjs
docker compose restart n8n
docker compose ps
```

The bootstrap imports the Groq credential and a distinct header credential, then imports and publishes both `workflows/interpret-brief.json` and `workflows/production-plan.json`. n8n must restart to register workflows published through its CLI. Credentials are stored encrypted in the private n8n volume. The bootstrap's temporary credential file is removed in a `finally` block and is never part of the source.

Open **http://localhost:8088** to use the visitor interface. Startup can take several minutes on the first run. Wait for the n8n readiness check before submitting requests.

## Services and storage

| Service | Exposure | Role |
|---|---|---|
| `web` | `127.0.0.1:8088` | Static interface; proxies only `/api/` and `/health` |
| `api` | Docker network only | Visitor sessions, quote verification, human decisions, synthetic ERP |
| `n8n` | Admin editor on `127.0.0.1:5688` | Groq planning workflow and authenticated internal webhook |

The named `demo_data` and `n8n_data` volumes persist synthetic records and configuration. `docker compose stop` stops services without deleting their volumes. This stack uses its own volume namespace and does not depend on a previous local installation.

## Public hosting

For the full Vercel/Neon deployment, see [Vercel container deployment](VERCEL.md). The remaining guidance applies to a conventional Compose host.

Choose the hosting platform and domain before enabling public access. Put the web service behind an HTTPS reverse proxy, set `PUBLIC_ORIGIN` to the exact external origin, and recreate the API container to enable secure visitor cookies. If an external proxy must reach port 8088, set `PUBLIC_BIND_IP` for that deployment. Keep the API and n8n editor off the public proxy.

The n8n editor's local HTTP cookie setting is intended for loopback administration. Remote administration requires an authenticated private access path; it is not part of the visitor interface.

The nginx gateway overwrites `X-Real-IP` with its direct client's address. A hosting platform with another proxy in front must configure trusted client-address forwarding; otherwise visitors behind that proxy share the same IP quota. Do not trust arbitrary visitor-supplied forwarding headers.

Request limits are five submissions per session per hour, ten per observed IP per hour, forty globally per rolling day, and one active interpretation or planning execution at a time. A reviewed draft and its subsequent quote count as one submission. Reinterpreting a changed brief consumes a new submission. Provider quotas may be lower. The application rejects excess requests and does not automatically switch to simulated model results.

## Data and troubleshooting

Visitor sessions last one hour; a request is accessible within its own session for thirty minutes. Old gateway jobs are pruned when a new job arrives after their rolling 24-hour retention window. Synthetic ERP records remain in the demo volume until the operator removes or rotates that volume. Use synthetic briefs without personal information.

If planning is unavailable, check that the n8n owner setup and bootstrap completed, that n8n restarted after publishing, and that the Groq key/model is valid. The interface returns a generic error; it never exposes internal provider errors or credentials.

The model has one node retry after two seconds and a 150-second workflow timeout. The gateway expires unanswered planning jobs after 180 seconds. An uncertain human-decision write ends in an error rather than issuing another review token automatically.

References: [n8n Docker Compose](https://docs.n8n.io/deploy/host-n8n/install-options/use-a-cloud-provider/use-docker-compose), [n8n CLI publishing](https://docs.n8n.io/deploy/host-n8n/configure-n8n/use-the-command-line), [Docker health-based dependencies](https://docs.docker.com/compose/how-tos/startup-order/).
