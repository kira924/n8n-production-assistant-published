# Validation

## Automated component checks

- **18 ERP tests:** canonical pricing, tampered totals, quote deduplication, approval-token binding and expiry, repeated/parallel order creation, and invalid resource/date inputs.
- **21 gateway tests:** session isolation, CSRF/origin enforcement, private tool boundaries, explicit/parallel approval, idempotent decisions, decline, budget and capacity rejection, quote ownership, malformed/late callbacks, expiry, submission limits, secure cookies, and generic failure responses.
- **Planning policy checks:** execute the JavaScript code from the exported workflow against valid, malformed, mismatched, and fabricated tool observations. They confirm that the plan text cannot supply a quote identity or amount.
- **JavaScript syntax:** checked for the browser interface and the n8n bootstrap.

Gateway tests use a controlled dispatcher and genuine synthetic ERP responses. They do not call Groq or prove a complete container deployment.

## Real local integration

The published workflow has been exercised with n8n 2.42.6 and an actual Groq key in an isolated local runtime. The available request returned the catalog total of 2240 SAR after `catalog`, `availability`, and `quote`; explicit approval created one order and repeating approval returned that same order. Decline created no order. Separate runs rejected a 1000 SAR budget and a three-camera request. Rapid consecutive provider calls also produced a planning error, so these cases do not establish a general success rate or unrestricted quota.

## Cloud checks

Cloud boundary tests cover route isolation, stripped workflow headers, secure cookie forwarding, payload limits, and keeping the platform request open until the persisted planning result exists. PostgreSQL integration tests use disposable schemas to check persistence across application recreation and serialization of concurrent submissions from separate instances. Set `TEST_DATABASE_URL` to run them locally; CI provides a disposable PostgreSQL service.

The cloud container CI builds the complete n8n/Python image, imports and publishes the workflow with a deliberately invalid test-only provider key, checks readiness and anonymous sessions, hides internal routes, and restarts the container. That startup check does not call Groq. A hosted deployment still requires its own real-provider and anonymous-access checks.

The exported Compose configuration passed the official Compose CLI's `config --quiet` validation. CI also checks the web/API Compose build and route boundary. Docker Desktop was not installed locally. Browser automation was unavailable; a visual review of responsive layout remains a separate manual check.
