# TalentMatch Project Specification

## Product Scope

TalentMatch is the fixed CSC4801 recruiting and candidate-matching product. It supports exactly two application roles: **Candidate** and **Employer**. The core application and full automated test suite run locally without any paid credential or third-party service.

Candidates can maintain their own profile and raw-text resume, see every current job ranked by a deterministic match score with matched/missing skill breakdowns, submit one application per job, inspect their own statuses, book eligible interview slots, and optionally request a secondary AI/deterministic role-fit explanation.

Employers can maintain a company profile, manage only their own jobs, see applicants for those jobs ranked with the same score (with inline read-only applicant resume preview), update the four allowed application statuses, manage employer-scoped interview availability, and optionally request a secondary AI/deterministic screening note.

## Architecture

The application uses a server-rendered Flask architecture:

~~~text
Browser
  |  HTTP + session cookie + hardened security headers (CSP, X-Frame-Options, nosniff)
  v
Flask routes
  +-- auth.py        registration, login/logout, role decorators
  +-- candidate.py   candidate profile/resume/jobs/applications/booking/optional AI insight
  +-- employer.py    company/jobs/applicants/status/availability/optional AI insight
  +-- matching.py    single canonical deterministic matching & skill-gap explanation
  +-- ai_advisor.py  optional sandboxed FP-MATCH-2 explanation engine + offline fallback
  +-- services.py    atomic scheduling, booking, application, status, deletion, safe search
  +-- demo_guard.py  DEMO_MODE-only rate limiting and capacity guardrails
  +-- db.py          SQLite connection (FK + WAL + busy_timeout), schema lifecycle, transactions
          |
          v
      SQLite DB
~~~

Jinja templates render the browser UI with automatic HTML escaping. A minimal external script (`recruiting/static/app.js`) pre-fills the +30-minute end time on the employer slot form without duplicating authorization or validation rules. Business rules remain enforced on the server.

## Data Model and Relationships

~~~text
users
 +-- 1:1 candidate_profiles
 +-- 1:1 company_profiles

users(Employer) 1 --- * jobs
users(Candidate) 1 -- * applications * -- 1 jobs
jobs 1 ------------- * interview_slots
applications 1 ----- 0..1 bookings 0..1 ----- 1 interview_slots
~~~

### Tables

- `users(id, username, password_hash, role, created_at)`
  - `username` unique.
  - `role` database check: only `Candidate` or `Employer`.
- `candidate_profiles(user_id, display_name, skills_json, resume_text)`
  - `user_id` is the owning Candidate.
  - `skills_json` stores a JSON array of cleaned display skill strings.
  - `resume_text` is private raw text.
- `company_profiles(user_id, company_name, description)`
  - one profile per Employer.
- `jobs(id, employer_id, title, description, required_skills_json, created_at)`
  - required skills use the same JSON-list representation.
- `applications(id, candidate_id, job_id, status, created_at)`
  - unique `(candidate_id, job_id)`.
  - `status` check allows exactly `Pending`, `Interviewing`, `Rejected`, `Accepted`.
- `interview_slots(id, job_id, employer_id, start_at, end_at)`
  - exact UTC ISO timestamps; each slot has one owning employer and a source job for creation/deletion bookkeeping. Eligible applications for any job of that same employer can book it.
- `bookings(id, application_id, slot_id, booked_at)`
  - unique `application_id` enforces at most one booking per application.
  - unique `slot_id` enforces at most one application per slot.

Foreign keys (`PRAGMA foreign_keys = ON`), `PRAGMA busy_timeout = 5000`, and `PRAGMA journal_mode = WAL` are configured per connection.

## Authentication and Authorization

Passwords are generated with Werkzeug's established `scrypt` password hashing. Registration enforces non-whitespace passwords between 8 and 128 characters. Password hashes never leave backend/database logic.

After login, the session stores only `user_id`. A `before_app_request` hook reloads `id`, `username`, and `role` from the database.

Protected routes use `role_required("Candidate")` or `role_required("Employer")`:

- missing session -> redirect to login;
- authenticated wrong role or wrong owner -> `403 Forbidden`;
- missing object that the caller would otherwise be allowed to access -> `404 Not Found`.

For `/candidate/resume/<candidate_id>`, ownership (`candidate_id == g.user["id"]`) is verified before database lookup so that another candidate cannot enumerate valid candidate IDs or employer IDs via 403-vs-404 differences; any non-owner receives `403`. Object ownership is always rechecked in backend routes and services.

## API Routes or Server Actions

### Public

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Product landing page |
| GET/POST | `/auth/register` | Register Candidate or Employer |
| GET/POST | `/auth/login` | Login |
| GET | `/healthz` | Local/container health check |

### Authenticated common

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/logout` | End session |

### Candidate

| Method | Path | Purpose |
|---|---|---|
| GET | `/candidate/` | All jobs ranked by match score with skill-gap breakdown |
| GET | `/candidate/search?q=...` | Parameterized LIKE-escaped job/company text search with match score |
| GET/POST | `/candidate/profile` | Own display name and technical skills |
| GET/POST | `/candidate/resume/<candidate_id>` | Own private raw-text resume; any other ID returns 403 |
| GET | `/candidate/jobs/<job_id>` | Job detail, company description, match breakdown, and apply action |
| POST | `/candidate/jobs/<job_id>/ai-insight` | Optional FP-MATCH-2 secondary role-fit explanation (offline fallback by default) |
| POST | `/candidate/jobs/<job_id>/apply` | Create one Pending application atomically |
| GET | `/candidate/applications` | Own applications with status and Asia/Shanghai interview window |
| GET | `/candidate/applications/<id>` | Own application and eligible employer-wide future slots |
| POST | `/candidate/applications/<id>/book/<slot_id>` | Atomic interview slot booking |

### Employer

| Method | Path | Purpose |
|---|---|---|
| GET | `/employer/` | Own jobs (with skills/counts) and employer availability slots |
| GET/POST | `/employer/company` | Own company profile |
| GET/POST | `/employer/jobs/new` | Create job |
| GET/POST | `/employer/jobs/<id>/edit` | Edit owned job |
| POST | `/employer/jobs/<id>/delete` | Delete owned dependency-free job |
| GET | `/employer/jobs/<id>/applicants` | Ranked applicants for owned job (with KPI summary and read-only applicant resume preview) |
| POST | `/employer/applications/<id>/status` | Set exact allowed status for owned job's application |
| POST | `/employer/applications/<id>/ai-insight` | Optional FP-MATCH-2 secondary screening note (never transmits private resume text) |
| POST | `/employer/slots` | Create future exact 30-minute slot |
| POST | `/employer/slots/<id>/delete` | Delete owned unbooked slot |

## User Interface and Workflows

### Candidate screens

1. Dashboard shows every current job with deterministic score, progress bar, matched/missing skill pills, next-skill score projection, title, company, required skills, description, application status badge, and detail link.
2. Profile edits display name and technical skills via a multi-line textarea supporting commas, semicolons, or newlines, and renders the normalized active skill list.
3. Resume stores/replaces private raw text.
4. Job detail shows company overview, deterministic score breakdown, apply button, and the optional FP-MATCH-2 Role Fit Advisor (with explicit opt-in checkbox to include own resume text).
5. Applications list shows job, employer, semantic status pill, and full start–end interview window with `(Asia/Shanghai)` timezone label.
6. Application detail lists the employer's free future slots across all its jobs (start–end in `Asia/Shanghai`) only while status is `Interviewing` and the application has no booking.
7. Search is separate from the all-jobs dashboard so the required dashboard remains complete.

### Employer screens

1. Dashboard shows owned jobs (with required skills, applicant count, and slot count), slot creation form with automatic +30-minute end-time helper, and slot table showing booked candidate and job.
2. Company profile edits name and description.
3. Job form creates/edits required title, description, and optional multi-line required skill list.
4. Applicants screen shows KPI summary strip, candidate identity, skills and matched/missing breakdown, deterministic score, status badge, interview window in `Asia/Shanghai`, collapsible read-only applicant resume preview (for the owning employer only), status controls, and optional FP-MATCH-2 screening insight button.

## Matching Algorithm and Skill Storage

The canonical implementation is `recruiting/matching.py::skill_match_score`.

Skill input is split on commas, semicolons, or newlines, trimmed, empty values removed, and duplicates removed using Python `.lower()`. Cleaned display strings are stored as JSON arrays. `skill_match_score` again normalizes with `.lower()` sets so storage formatting never alters the contract.

Let `C` be normalized candidate skills and `R` normalized required skills:

~~~text
score = 100                                      when |R| = 0
score = floor(100 * |C intersection R| / |R|)   otherwise
~~~

`recruiting/matching.py::explain_skill_match` wraps `skill_match_score` to compute `matched_skills`, `missing_skills`, `extra_skills`, and `next_skill_score` without changing the formula or tie-breaking order. Candidate ranking uses descending score, then case-insensitive job title, then job ID. Employer applicant ranking uses descending score, then case-insensitive candidate display name, then candidate ID. Both views call the same score implementation.

Resume text is never an input to the required score.

## Interview Scheduling and Booking Transaction

Form times are interpreted in **Asia/Shanghai** unless they already carry an explicit timezone offset. Invalid or out-of-range dates (including `OverflowError` on extreme boundary years) are rejected with HTTP `400`. All stored timestamps are UTC ISO 8601 strings truncated to whole seconds.

`services.create_slot` executes inside `BEGIN IMMEDIATE` and validates:

- owning employer exists and owns the selected job (404/403 before 400 date validation at the route layer);
- both times are timezone-aware and valid after UTC conversion;
- end is after start and duration is exactly 30 minutes before and after second-level normalization;
- normalized start is strictly in the future.

`services.apply_to_job` executes inside `BEGIN IMMEDIATE` to check job existence (404) and duplicate application (409) atomically against concurrent job deletion.

`services.book_slot` executes inside `BEGIN IMMEDIATE` and verifies:

1. application exists (`404`);
2. candidate owns the application (`403`);
3. slot exists (`404`) and belongs to the application's employer (`403`, source job may differ);
4. status is `Interviewing` (`409`);
5. slot remains in the future (`409`);
6. application has no booking (`409`);
7. slot has no booking (`409`, exact message `Slot already booked`).

The insert is additionally protected by `UNIQUE(application_id)` and `UNIQUE(slot_id)` in SQLite. A losing concurrent booking attempt returns HTTP `409` with the exact message **Slot already booked**. Updating an application status (`services.update_application_status`, also inside `BEGIN IMMEDIATE`) never removes an existing booking.

`services.delete_slot` and `services.delete_job` acquire `BEGIN IMMEDIATE` before checking ownership and dependent records so concurrent applications or bookings are observed under the write lock and return `409` while preserving existing records.

## Security Controls

### Object authorization

Server-side role and ownership checks protect resume, application, job, applicant, slot, and optional AI insight operations. Direct ID changes are rejected with `403`, and `/candidate/resume/<candidate_id>` checks ownership before database lookup so non-owners cannot enumerate candidate IDs.

### SQL injection and LIKE escaping

All database queries involving user-controlled values use bound SQLite `?` placeholders. `services.search_jobs` additionally escapes `\`, `%`, and `_` with `ESCAPE '\'` so wildcard metacharacters match literally. `demo_guard.py` uses static SQL query strings rather than interpolated table names.

### XSS and HTTP response headers

Jinja autoescaping remains enabled across all templates (including applicant resume previews and optional AI insight fields), and no template uses `|safe`. Every HTTP response includes:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'`

### Passwords, sessions, and server defaults

Passwords use Werkzeug `scrypt` hashes with an 8–128 character non-whitespace policy. Session cookies are `HttpOnly` and `SameSite=Lax` (plus `Secure` when `SESSION_COOKIE_SECURE=true`). `MAX_CONTENT_LENGTH` defaults to 2 MiB globally (`DEMO_MAX_REQUEST_BYTES=65536` in `DEMO_MODE`). `python -m recruiting` defaults to `debug=False` unless `FLASK_DEBUG=true` is set explicitly.

### Secrets and test isolation

The repository contains no real API key, credential, or personal data. `.env` and SQLite files are excluded in both `.gitignore` and `.dockerignore` (including nested `**/.env`). Pytest uses isolated temporary SQLite databases and disables real outbound LLM network calls.

## Optional AI Features and Data Flow (FP-MATCH-2)

TalentMatch implements an optional, sandboxed secondary explanation engine in `recruiting/ai_advisor.py`.

### Non-interference and offline fallback

- The optional advisor **never modifies** `skill_match_score`, `rank_jobs`, or `rank_applicants`.
- External LLM calls are **disabled by default** (`AI_ADVISOR_ENABLED=false`). When disabled, unconfigured, in automated tests (unless an in-process test double is injected via `AI_COMPLETION_PROVIDER`), or whenever an upstream call times out/fails/violates output guardrails, the advisor automatically returns an offline deterministic explanation built from `explain_skill_match`.

### Data flow and privacy boundary

```text
Candidate POST /candidate/jobs/<id>/ai-insight
  -> Server loads authenticated candidate's skills + job title/company/description/required skills
  -> Candidate's own resume_text is EXCLUDED by default and included ONLY when include_resume=1 is checked
  -> Untrusted text sanitized + truncated + JSON-encoded inside <UNTRUSTED_DATA_<random_nonce>>
  -> Optional HTTPS POST to validated LLM_API_BASE_URL/chat/completions (Bearer LLM_API_KEY in header only)
  -> Strict JSON schema + secret/prompt/canary leak guardrail -> Jinja autoescaped HTML

Employer POST /employer/applications/<id>/ai-insight
  -> Server verifies employer owns application's job (403/404 otherwise)
  -> Loads job info + applicant display_name/skills/status ONLY (private resume_text is NEVER passed to ai_advisor)
  -> Same untrusted-data isolation, SSRF validation, schema validation, and leak guardrail
```

### Security controls for optional LLM calls

1. **Untrusted-data isolation**: Control characters, zero-width characters, and Unicode BiDi override characters are stripped; lengths are bounded; user fields are serialized as JSON inside `<UNTRUSTED_DATA_<16-hex-nonce>>` blocks whose nonce changes per request and is scrubbed from serialized payloads.
2. **Prompt/secret leak guardrail**: The system prompt instructs the model to treat `<UNTRUSTED_DATA_...>` as passive data and return strict JSON (`summary`, `strengths`, `gaps`, `next_steps`). Before rendering, `_validate_structured_output` enforces exact schema/type/length allow-lists and blocks any output containing the per-request nonce/canary (`TM_GUARD_`), system-prompt markers, `LLM_API_KEY`, `SECRET_KEY`, or credential signatures (`sk-`, `Bearer `, `BEGIN PRIVATE KEY`), falling back to the deterministic engine.
3. **SSRF protection**: `validate_llm_base_url` requires `https://`, rejects embedded credentials/queries/fragments, blocks localhost/private/loopback/link-local/reserved hostnames and resolved IP addresses, disables HTTP 3xx redirects, caps response reads to 64 KiB, and enforces bounded timeouts (1–15s).
4. **Public demo protection**: In `DEMO_MODE`, AI insight endpoints have a dedicated per-IP rate limit (`DEMO_AI_PER_MINUTE`, default 6/min).

## Design Decisions and Limitations

- SQLite with `WAL` mode, `BEGIN IMMEDIATE`, and unique constraints was selected for clean reproducible local/Docker grading without external database servers.
- Jobs are hard-deleted only when no application or interview slot depends on them.
- Raw-text resume is implemented (plus inline read-only applicant resume preview for the owning employer per the Nov 24 course clarification); PDF extraction is optional and omitted.
- The application stays server-rendered with minimal external progressive enhancement (`app.js`) so authorization and business rules live strictly on the server under a strict Content-Security-Policy.
