# TalentMatch — CSC4801 Team Zzz

TalentMatch is a locally runnable full-stack recruiting and candidate-matching platform for CSC4801. Candidates maintain a private profile and resume, receive deterministic skill-overlap job recommendations with transparent matched/missing skill breakdowns, apply to jobs, book interviews atomically, and can optionally request sandboxed secondary AI/deterministic role-fit insights. Employers maintain a company profile, own job postings, rank applicants with the same score, inspect read-only applicant resumes on their owned postings, update application status, and publish 30-minute interview availability.

Group name: **Zzz** (Group 7)

Project repository: https://github.com/Arvin-Chaser/CSC4801_TeamZzz

Live demo: https://csc4801.wushenyu.com

The public demo uses synthetic data, preserves normal demo activity across restarts, and can be manually reset to the deterministic seed before a presentation. Do not enter real personal data. The graded artifact remains the GitHub commit and its reproducible Docker workflow.

## Architecture Summary

- Browser UI: server-rendered Jinja templates with automatic HTML escaping, strict Content-Security-Policy, and a small external script (`recruiting/static/app.js`) for +30-minute slot end-time convenience.
- Backend: Flask 3.1 application split into authentication, candidate, employer, matching, optional sandboxed AI advisor, scheduling/business services, demo guard, and database modules.
- Database: SQLite relational database with foreign keys, WAL mode, checks, uniqueness constraints, and explicit `BEGIN IMMEDIATE` transactions.
- Authentication: Flask session cookie (`HttpOnly`, `SameSite=Lax`) plus Werkzeug `scrypt` password hashing.
- Matching: one shared deterministic implementation in `recruiting/matching.py` (`skill_match_score` and `explain_skill_match`).
- Optional AI (`FP-MATCH-2`): `recruiting/ai_advisor.py` provides opt-in secondary explanations with random-nonce untrusted-data isolation, SSRF validation, strict JSON schema + prompt/secret leak guardrails, and automatic offline deterministic fallback. Core workflows and automated tests require no external LLM or API key.
- Interview booking: `BEGIN IMMEDIATE` transaction plus unique database constraints on both application and slot.
- Deployment: one `Dockerfile`, Waitress WSGI server, writable `/data` database path.

## Quick Start

See `INSTALL.md` for clean-checkout, local, and Docker instructions.

Fast local flow after installing dependencies:

~~~bash
flask --app recruiting reset-seed
python -m recruiting
~~~

Open http://127.0.0.1:8080.

## Demo Accounts

All seeded accounts use the local/demo password **DemoPass123!**.

| Role | Username | Purpose |
|---|---|---|
| Candidate | alice | Python + SQL; 100% match to Backend Engineer |
| Candidate | bob | Python; 50% match; Interviewing |
| Candidate | carol | Rust; 0% match |
| Candidate | dave | Go; second Interviewing candidate for booking conflict demo |
| Employer | acme | Owns Backend Engineer and shared race slot |
| Employer | beta | Owns Open Skills Role |

These credentials are intentionally non-production demo data.

## Running Unit Tests

~~~bash
python -m pytest -q
~~~

The complete suite is non-interactive and exits nonzero on failure. It covers every mandatory functional and security requirement plus optional `FP-MATCH-2` fallback, privacy boundaries, SSRF blocking, prompt-injection/secret-leak guardrails, same-employer cross-job booking, deletion races using independent SQLite connections, and a synchronized two-client booking race. CI builds the Docker image, starts it, seeds its isolated database, runs the suite inside it, and checks HTTP health.

## Requirement–Test Mapping

| Requirement ID | Behavior / evidence | Implementation file(s) | Unit test(s) / verification evidence |
|---|---|---|---|
| FP-ARCH-1 | Browser UI + backend rules + persistent relational DB + offline runnable without paid LLM | `recruiting/`, `schema.sql`, `templates/`, `Dockerfile` | Clean local/Docker smoke flow in `INSTALL.md`; `test_fp_match_2_offline_deterministic_fallback_and_score_invariance` |
| FP-AUTH-1 | Candidate/Employer registration, unique username, login/logout | `recruiting/auth.py`, `recruiting/schema.sql` | `test_fp_auth_1_registration_and_session_lifecycle`; `test_fp_auth_1_invalid_registration` |
| FP-AUTH-2 | Scrypt password hash; no password returned/rendered | `recruiting/auth.py` | `test_fp_auth_2_salted_hash_and_password_verification` |
| FP-AUTH-3 | Unauthenticated redirect, role/owner 403, allowed missing object 404 | `recruiting/auth.py` decorators + candidate/employer routes | `test_fp_auth_3_authorization_and_missing_object`; `test_fp_auth_3_ownership_precedes_status_validation`; `test_fp_auth_3_slot_owner_precedes_date_validation`; `test_fp_sec_1_resume_never_enumerates_missing_or_employer_ids` |
| FP-CAN-1 | Own profile/skills and private raw-text resume (403 to all non-owners on direct URL) | `recruiting/candidate.py`, `recruiting/matching.py` | `test_fp_can_1_profile_and_private_resume_rules`; `test_fp_sec_1_resume_never_enumerates_missing_or_employer_ids` |
| FP-CAN-2 | All jobs ranked by required score with required display fields | `recruiting/candidate.py`, `rank_jobs` | `test_fp_can_2_recommendations_preserve_fields_and_rank_all_jobs` |
| FP-CAN-3 | Single Pending application; atomic job check; own application views only | `recruiting/candidate.py`, `services.apply_to_job`, DB unique constraint | `test_fp_can_3_pending_duplicate_and_application_owner`; `test_fp_can_3_concurrent_job_delete_before_apply_returns_404` |
| FP-EMP-1 | Own company name/description editing | `recruiting/employer.py` | `test_fp_emp_1_company_profile` |
| FP-EMP-2 | Own job CRUD, validation, ownership, 409 dependent deletion | `recruiting/employer.py`, `services.delete_job` | `test_fp_emp_2_job_validation_and_deletion`; `test_fp_emp_2_application_wins_against_job_deletion` |
| FP-EMP-3 | Ranked applicant fields + exact status transitions + role ownership + optional inline applicant resume preview | `rank_applicants`, `recruiting/employer.py` | `test_fp_emp_3_applicant_fields_order_status_and_role`; `test_fp_emp_3_applicant_resume_visible_only_to_owning_employer` |
| FP-MATCH-1 | Normalized skill-overlap formula, breakdown helper, and both tie-break contracts | `recruiting/matching.py` | `test_fp_match_1_examples_normalization_and_floor`; `test_fp_match_1_both_tie_breakers`; `test_fp_match_1_lowercase_does_not_merge_distinct_unicode_skills`; `test_fp_match_1_explain_skill_match_consistency` |
| FP-MATCH-2 | Optional sandboxed AI explanation with untrusted-data isolation, SSRF check, leak guardrail, and offline deterministic fallback | `recruiting/ai_advisor.py`, `recruiting/candidate.py`, `recruiting/employer.py` | `test_fp_match_2_offline_deterministic_fallback_and_score_invariance`; `test_fp_match_2_llm_provider_isolation_and_privacy_boundary`; `test_fp_match_2_prompt_injection_and_secret_leak_guardrails`; `test_fp_match_2_ssrf_url_validation` |
| FP-SCHED-1 | Future exact 30-minute employer-owned slots with source job; overflow/invalid rejection; booked slot cannot delete | `services.create_slot`, `services.delete_slot` | `test_fp_sched_1_slot_creation_timezones_and_deletion`; `test_fp_sched_1_invalid_slots`; `test_fp_sched_1_overflow_datetime_returns_400_not_500`; `test_fp_sched_1_booking_wins_against_slot_deletion`; `test_fp_sched_1_concurrent_job_delete_before_slot_create_returns_404` |
| FP-SCHED-2 | Ownership, same employer across jobs, Interviewing, future, free slot, one booking, visibility | `services.book_slot`, candidate/employer views | `test_fp_sched_2_eligible_booking`; `test_fp_sched_2_ineligible_booking`; `test_fp_sched_2_booking_is_visible_and_booked_slot_is_hidden`; `test_fp_sched_2_same_employer_different_job_is_eligible`; `test_fp_sched_2_same_employer_cross_job_service`; `test_fp_sched_2_ineligible_status_leaves_database_unchanged`; `test_fp_sched_2_expired_slot_leaves_database_unchanged` |
| FP-SCHED-3 | Atomic booking conflict; exactly one booking; exact conflict message | `services.book_slot`, unique DB constraints | `test_fp_sched_3_conflict_contract`; `test_fp_sched_3_independent_connections_have_one_winner`; `BEGIN IMMEDIATE` and unique constraints |
| FP-SEC-1 | Representative direct-object authorization boundaries and no ID enumeration | auth decorators + candidate/employer/service checks | `test_fp_sec_1_object_access_boundaries`; `test_fp_sec_1_resume_never_enumerates_missing_or_employer_ids` |
| FP-SEC-2 | Bound SQL parameters and escaped LIKE wildcards for user-controlled values | all database modules, `services.search_jobs`, `demo_guard.py` | `test_fp_sec_2_sql_metacharacters_are_bound`; `test_fp_sec_2_search_escapes_like_wildcards` |
| FP-SEC-3 | Jinja autoescape across profile, resume, company, job, search, and AI outputs + hardened CSP headers | all templates; `recruiting/__init__.py` | `test_fp_sec_3_xss_is_escaped`; `test_security_headers_and_csp`; `test_fp_match_2_prompt_injection_and_secret_leak_guardrails` |
| FP-SEC-4 | No real secrets/personal data; isolated temp test DB; no third-party test calls | `.env.example`, `.gitignore`, `.dockerignore`, pytest fixtures | `test_fp_sec_4_secrets_and_test_isolation`; `test_fp_match_2_llm_provider_isolation_and_privacy_boundary` |
| FP-PROC-1 | Living architecture/data/auth/routes/UI/matching/booking/security/AI data-flow spec | `SPEC.md` | Document review against graded commit |
| FP-DOC-1 | Exact prerequisites/install/env/DB/seed/run/test/Docker/troubleshooting | `INSTALL.md` | Clean-checkout verification |
| FP-DOC-2 | Overview, architecture, demo accounts, test command, docs, mapping, limitations | `README.md` | Document review |
| FP-DOC-3 | Deterministic reset+seed with required actors/examples/statuses/race fixture | `recruiting/seed.py`, CLI command | Seed data exercised across unit suite |
| FP-TEST-1 | Student-written unit tests for every functional requirement plus security, conflict, and FP-MATCH-2 cases | `tests/unit/` | `python -m pytest -q` |
| FP-SUB-1 | Permanent main-branch commit URL + Docker-buildable graded commit | `Dockerfile`, `INSTALL.md`, repository `main` branch | Verified via clean Docker build, seed, pytest, and `/healthz` check |

## Document Index

- `REQUIREMENTS.md` — normative course requirements.
- `SPEC.md` — living implementation specification (including FP-MATCH-2 AI data flow and security controls).
- `INSTALL.md` — clean setup, seed, run, test, and Docker commands.
- `PEER_REVIEW.md` — course peer-audit procedure.
- `DOCKER.md` — course Docker expectations.
- `TEAM_GUIDE.md` — code tour and common safe modifications.
- `deploy/demo/README.md` — isolated public-demo deployment and safety model.
- `.github/ISSUE_TEMPLATE/audit_bug_report.yml` — required audit issue form.
- `.github/labels.yml` — required audit label definitions.

## Known Limitations

- PDF resume upload/extraction is not implemented; the required raw-text resume workflow is complete.
- External LLM explanations (`FP-MATCH-2`) are disabled by default and require explicit runtime configuration (`AI_ADVISOR_ENABLED=true`, `LLM_API_BASE_URL`, `LLM_API_KEY`); without credentials or during tests, the application uses its offline deterministic explanation fallback.
- User-entered interview times without an explicit timezone offset are interpreted as `Asia/Shanghai` and stored in UTC.
- SQLite (with WAL mode, `BEGIN IMMEDIATE`, and unique constraints) is selected deliberately for reproducible local/Docker grading; production-scale multi-host deployments would use a server database.
