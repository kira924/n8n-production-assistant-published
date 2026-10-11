# Validation

## Automated component checks

- **18 ERP tests:** canonical pricing, tampered totals, quote deduplication, approval-token binding and expiry, repeated/parallel order creation, and invalid resource/date inputs.
- **21 gateway tests:** session isolation, CSRF/origin enforcement, private tool boundaries, explicit/parallel approval, idempotent decisions, decline, budget and capacity rejection, quote ownership, malformed/late callbacks, expiry, submission limits, secure cookies, and generic failure responses.
- **Planning policy checks:** execute the JavaScript code from the exported workflow against valid, malformed, mismatched, and fabricated tool observations. They confirm that the plan text cannot supply a quote identity or amount.
- **JavaScript syntax:** checked for the browser interface and the n8n bootstrap.

Gateway tests use a controlled dispatcher and genuine synthetic ERP responses. They do not call Groq or prove a complete container deployment.

## Real local integration

The natural-language entry was exercised with actual n8n and Groq using an interview brief, a silent product advertisement, and a brief missing date and budget. The model extracted explicit facts with excerpts, recommended resources without requiring the visitor to enter them first, changed recommendations for the product advertisement, and asked for missing date/budget instead of inventing them. The reviewed interview draft continued immediately to a verified estimate and explicit order approval. Component checks also reject invented dates/budgets, fabricated excerpts, unsupported suggestions, price fields in model output, and foreign/reused draft confirmation.

The published workflow has been exercised with n8n 2.42.6 and an actual Groq key in an isolated local runtime. The available request returned the catalog total of 2240 SAR after `catalog`, `availability`, and `quote`; explicit approval created one order and repeating approval returned that same order. Decline created no order. Separate runs rejected a 1000 SAR budget and a three-camera request. Rapid consecutive provider calls also produced a planning error, so these cases do not establish a general success rate or unrestricted quota.

## Cloud checks

The [GitHub checks for the natural-language runtime](https://github.com/kira924/n8n-production-assistant-published/actions/runs/38163607264) passed on October 11, 2026: draft validation, gateway/ERP and PostgreSQL checks, workflow policy checks, Compose startup, and the full cloud container's bootstrap/readiness/restart checks with both exported workflows.

Cloud boundary tests cover route isolation, stripped workflow headers, secure cookie forwarding, payload limits, and keeping the platform request open until the persisted planning result exists. PostgreSQL integration tests use disposable schemas to check persistence across application recreation and serialization of concurrent submissions from separate instances. Set `TEST_DATABASE_URL` to run them locally; CI provides a disposable PostgreSQL service.

The cloud container CI builds the complete n8n/Python image, imports and publishes the workflow with a deliberately invalid test-only provider key, checks readiness and anonymous sessions, hides internal routes, and restarts the container. That startup check does not call Groq.

## Hosted integration

The natural-language interface was tested on the public HTTPS URL with actual n8n, Groq, and Neon on October 11, 2026, with the local test services stopped. An interview description yielded reviewed resource recommendations, a verified estimate, and an explicitly approved synthetic work order. A silent product advertisement yielded a different camera/time recommendation and excluded audio as requested. A brief without a date or budget returned questions with null values instead of invented facts. The displayed catalog was retrieved from the same ERP source used for interpretation and pricing.

The original [live Vercel deployment](https://n8n-production-assistant-published.vercel.app) was independently tested over anonymous HTTPS with actual n8n, Groq, and Neon on October 11, 2026, before the natural-language entry was added. All four structured cases passed: approval with repeat-approval idempotency, decline without an order, budget rejection, and resource-capacity rejection. Each plan used the observed `catalog`, `availability`, `quote` sequence. A different anonymous session could not read the requests. Internal service routes and the n8n editor returned 404.

These checks establish the listed behaviors with synthetic inputs. Free-tier quotas, cold starts, and provider availability still apply.

The exported Compose configuration passed the official Compose CLI's `config --quiet` validation. CI also checks the web/API Compose build and route boundary. Docker Desktop was not installed locally. Browser automation was unavailable; a visual review of responsive layout remains a separate manual check.
