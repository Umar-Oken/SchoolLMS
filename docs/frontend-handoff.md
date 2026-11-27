# Frontend handoff (P090)

## Gate

**Backend contract complete for integration; not production-ready.** Frontend may
begin against `/api/v1/`, but production release remains blocked by real AI
provider approval/configuration, real S3 smoke, and deploy-operator Nginx/TLS
smoke. AI remains `AI_PROVIDER=disabled` in production.

## Auth and errors

Use `Authorization: Bearer <access>` for API calls. JWT endpoints do not need
CSRF; Django admin/session requests do. Refresh with `/auth/token/refresh/` and
logout with `/auth/logout/`. Errors have `{code,message,fields,request_id}`;
show `fields` beside inputs and retain `request_id` for support. Common status:
`400` validation, `401` login, `403` role, `404` hidden/out-of-scope,
`409` idempotency conflict, `413` file too large, `429` throttle/quota.

## Core contracts

See `API_GUIDE.md` and `/api/schema/` as the frozen contract. Lists use
`page`, `page_size` (max 100) and return paginator metadata. Datetimes are UTC
ISO-8601; render in `Asia/Tashkent`. Scores are decimal strings, not floats.
Files upload through `POST /attachments/`, start `pending_scan`, and never have
public URLs. Download only `/attachments/{id}/download/` after permission and
clean scan; handle stream or an approved S3 redirect.

AI flow: `POST /ai/question-drafts/` requires an `Idempotency-Key`; poll
`GET /ai/jobs/{id}/`. A review job exposes draft provenance/citations. Teacher
uses `GET /ai/jobs/{id}/review/`, PATCHes an individual
`/ai/jobs/{id}/drafts/{draft_id}/` with the current `draft_version`, then POSTs
`/approve/` or `/reject/` with `{ "draft_version": n }`. Approve returns
`question_ids`; it does not publish an exam. Always require the teacher to
check the key and factual citation before approval.

## Test data and blockers

Use `seed_school` only in approved non-production environments. P084 provides
synthetic smoke identities/flows. No frontend code or dependency is included.
The AI evaluation fixture has 30 planned cases: 10 normal, 10 insufficient
source, 10 prompt-injection. With no real provider it is mock-only and does not
prove model safety, citation truth, or educational quality.
