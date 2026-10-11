# Validation

## Automated component checks

- **18 ERP tests:** canonical pricing, tampered totals, quote deduplication, approval-token binding and expiry, repeated/parallel order creation, and invalid resource/date inputs.
- **21 gateway tests:** session isolation, CSRF/origin enforcement, private tool boundaries, explicit/parallel approval, idempotent decisions, decline, budget and capacity rejection, quote ownership, malformed/late callbacks, expiry, submission limits, secure cookies, and generic failure responses.
- **Planning policy checks:** execute the JavaScript code from the exported workflow against valid, malformed, mismatched, and fabricated tool observations. They confirm that the plan text cannot supply a quote identity or amount.
- **JavaScript syntax:** checked for the browser interface and the n8n bootstrap.

Gateway tests use a controlled dispatcher and genuine synthetic ERP responses. They do not call Groq or prove a complete container deployment.

## Runtime status

The Docker configuration is prepared for operator review. Docker Engine/Desktop was unavailable in the local validation environment, so a complete `docker compose up` run has not been verified there. The Docker build, bootstrap, authenticated webhook, and provider quotas must be checked with the operator's configured Groq key before public hosting.

The web-facing workflow is an adaptation of a previously exercised local planning flow. Its new webhook, internal route authentication, and callback integration require their own end-to-end run; component checks are not presented as that run.

The exported Compose configuration passed the official Compose CLI's `config --quiet` validation. Browser automation was unavailable in the local validation environment; responsive layout and browser interactions still require a visual review. The GitHub workflow also checks the component tests, container build/startup of the web/API services, and private-route proxy boundary.
