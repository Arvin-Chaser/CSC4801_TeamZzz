import json

import pytest

from recruiting.ai_advisor import validate_llm_base_url
from recruiting.matching import rank_applicants, rank_jobs


def test_fp_match_2_offline_deterministic_fallback_and_score_invariance(client, login, db, ids):
    before_jobs = [(j["id"], j["match_score"]) for j in rank_jobs(db, ids["bob"])]
    before_applicants = [(a["candidate_id"], a["match_score"]) for a in rank_applicants(db, ids["backend"])]

    # Unauthenticated access redirects to login.
    unauth = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert unauth.status_code == 302
    assert "/auth/login" in unauth.headers["Location"]

    login("bob")
    assert client.post("/candidate/jobs/999999/ai-insight").status_code == 404
    assert client.post(f"/employer/applications/{ids['bob_backend']}/ai-insight").status_code == 403

    response = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Offline deterministic fallback" in html
    assert "50%" in html
    assert "SQL" in html

    client.post("/auth/logout")
    login("beta")
    assert client.post(f"/employer/applications/{ids['bob_backend']}/ai-insight").status_code == 403
    assert client.post("/employer/applications/999999/ai-insight").status_code == 404

    client.post("/auth/logout")
    login("acme")
    emp_resp = client.post(f"/employer/applications/{ids['bob_backend']}/ai-insight")
    assert emp_resp.status_code == 200
    emp_html = emp_resp.get_data(as_text=True)
    assert "Offline deterministic fallback" in emp_html
    assert "Bob Python" in emp_html

    # Required scores and rankings must remain identical.
    assert [(j["id"], j["match_score"]) for j in rank_jobs(db, ids["bob"])] == before_jobs
    assert [(a["candidate_id"], a["match_score"]) for a in rank_applicants(db, ids["backend"])] == before_applicants


def test_fp_match_2_llm_provider_isolation_and_privacy_boundary(app, client, login, db, ids):
    captured = []

    def fake_provider(*, messages, model, timeout_seconds):
        captured.append({"messages": messages, "model": model, "timeout": timeout_seconds})
        return json.dumps(
            {
                "summary": "Structured LLM match summary.",
                "strengths": ["Strong Python foundation."],
                "gaps": ["Add SQL experience to reach 100%."],
                "next_steps": ["Prepare SQL schema design examples."],
            }
        )

    app.config["AI_ADVISOR_ENABLED"] = True
    app.config["LLM_API_KEY"] = "test-placeholder-secret-value-1234"
    app.config["LLM_MODEL"] = "mimo-v2.6-pro"
    app.config["AI_COMPLETION_PROVIDER"] = fake_provider

    secret_resume_marker = "PRIVATE_RESUME_SECRET_PHRASE_9876"
    login("bob")
    assert client.post(
        f"/candidate/resume/{ids['bob']}",
        data={"resume_text": secret_resume_marker},
    ).status_code == 302

    # 1) Candidate opt-out (default): resume is NOT transmitted.
    r_no_resume = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert r_no_resume.status_code == 200
    assert "Structured LLM match summary." in r_no_resume.get_data(as_text=True)
    assert "LLM · mimo-v2.6-pro" in r_no_resume.get_data(as_text=True)
    assert secret_resume_marker not in json.dumps(captured[-1]["messages"])
    assert "<UNTRUSTED_DATA_" in captured[-1]["messages"][1]["content"]

    # 2) Candidate explicit opt-in: own resume IS wrapped in untrusted nonce block.
    r_with_resume = client.post(
        f"/candidate/jobs/{ids['backend']}/ai-insight",
        data={"include_resume": "1"},
    )
    assert r_with_resume.status_code == 200
    assert secret_resume_marker in captured[-1]["messages"][1]["content"]
    assert "<UNTRUSTED_DATA_" in captured[-1]["messages"][1]["content"]

    # 3) Employer insight NEVER transmits candidate private resume text.
    client.post("/auth/logout")
    login("acme")
    r_emp = client.post(f"/employer/applications/{ids['bob_backend']}/ai-insight")
    assert r_emp.status_code == 200
    assert "Structured LLM match summary." in r_emp.get_data(as_text=True)
    assert secret_resume_marker not in json.dumps(captured[-1]["messages"])


def test_fp_match_2_prompt_injection_and_secret_leak_guardrails(app, client, login, ids):
    api_key_sentinel = "fake-llm-secret-canary-998877"
    app.config["AI_ADVISOR_ENABLED"] = True
    app.config["LLM_API_KEY"] = api_key_sentinel

    login("bob")

    # 1) Provider attempts to leak LLM_API_KEY -> blocked & falls back safely.
    app.config["AI_COMPLETION_PROVIDER"] = lambda **_: json.dumps(
        {
            "summary": f"Leaked key: {api_key_sentinel}",
            "strengths": ["ok"],
            "gaps": [],
            "next_steps": [],
        }
    )
    resp_key = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert resp_key.status_code == 200
    html_key = resp_key.get_data(as_text=True)
    assert api_key_sentinel not in html_key
    assert "Offline deterministic fallback" in html_key
    assert "safety/schema validation" in html_key

    # 2) Provider attempts to echo system prompt / canary / untrusted boundary -> blocked.
    def echo_system(*, messages, model, timeout_seconds):
        return json.dumps(
            {
                "summary": messages[0]["content"][:180],
                "strengths": [],
                "gaps": [],
                "next_steps": [],
            }
        )

    app.config["AI_COMPLETION_PROVIDER"] = echo_system
    resp_sys = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert resp_sys.status_code == 200
    html_sys = resp_sys.get_data(as_text=True)
    assert "TM_GUARD_" not in html_sys
    assert "Offline deterministic fallback" in html_sys

    # 3) Provider returns XSS payload in JSON -> escaped in HTML.
    app.config["AI_COMPLETION_PROVIDER"] = lambda **_: json.dumps(
        {
            "summary": "<script>alert(1)</script> Safe summary",
            "strengths": ["<b>Python</b>"],
            "gaps": [],
            "next_steps": [],
        }
    )
    resp_xss = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert resp_xss.status_code == 200
    html_xss = resp_xss.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html_xss
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_xss

    # 4) Provider returns malformed output or raises error -> graceful fallback, no 500 or secret echo.
    def crash_provider(**_):
        raise RuntimeError(f"upstream failed with {api_key_sentinel}")

    app.config["AI_COMPLETION_PROVIDER"] = crash_provider
    resp_err = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert resp_err.status_code == 200
    html_err = resp_err.get_data(as_text=True)
    assert api_key_sentinel not in html_err
    assert "Offline deterministic fallback" in html_err

    app.config["AI_COMPLETION_PROVIDER"] = lambda **_: "not valid json at all"
    resp_bad_json = client.post(f"/candidate/jobs/{ids['backend']}/ai-insight")
    assert resp_bad_json.status_code == 200
    assert "Offline deterministic fallback" in resp_bad_json.get_data(as_text=True)


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "http://api.example.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/v1",
        "https://192.168.1.10/v1",
        "https://[::1]/v1",
        "https://user:pass@api.example.com/v1",
        "https://api.example.com/v1?foo=bar",
        "https://internal.local/v1",
    ],
)
def test_fp_match_2_ssrf_url_validation(bad_url):
    with pytest.raises(ValueError):
        validate_llm_base_url(bad_url, resolve_dns=False)
