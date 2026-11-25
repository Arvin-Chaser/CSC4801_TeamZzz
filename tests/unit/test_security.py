from pathlib import Path

from recruiting import create_app
from recruiting.db import transaction
from recruiting.services import search_jobs


def test_fp_sec_1_object_access_boundaries(client, login, ids):
    login("bob")
    assert client.get(f"/candidate/resume/{ids['alice']}").status_code == 403
    assert client.post(
        f"/candidate/resume/{ids['alice']}",
        data={"resume_text": "attempted overwrite"},
    ).status_code == 403
    assert client.get(f"/candidate/applications/{ids['alice_backend']}").status_code == 403
    assert client.post(
        f"/candidate/applications/{ids['alice_backend']}/book/1"
    ).status_code == 403

    client.post("/auth/logout")
    login("beta")
    assert client.get(f"/employer/jobs/{ids['backend']}/edit").status_code == 403
    assert client.post(
        f"/employer/jobs/{ids['backend']}/edit",
        data={"title": "tampered", "description": "tampered", "required_skills": ""},
    ).status_code == 403
    assert client.get(f"/employer/jobs/{ids['backend']}/applicants").status_code == 403


def test_fp_sec_1_resume_never_enumerates_missing_or_employer_ids(client, login, ids):
    login("bob")
    # Candidate cannot probe whether another user ID exists or belongs to an employer.
    assert client.get(f"/candidate/resume/{ids['alice']}").status_code == 403
    assert client.get(f"/candidate/resume/{ids['acme']}").status_code == 403
    assert client.get("/candidate/resume/999999").status_code == 403
    assert client.post("/candidate/resume/999999", data={"resume_text": "nope"}).status_code == 403

    client.post("/auth/logout")
    login("acme")
    assert client.get(f"/candidate/resume/{ids['alice']}").status_code == 403
    assert client.get("/candidate/resume/999999").status_code == 403


def test_fp_sec_2_sql_metacharacters_are_bound(db):
    before = db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    payload = "' OR 1=1; DROP TABLE jobs; --"
    results = search_jobs(db, payload)
    assert results == []
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == before

    with transaction(db):
        db.execute(
            "UPDATE candidate_profiles SET display_name = ? WHERE user_id = (SELECT id FROM users WHERE username='alice')",
            (payload,),
        )
    assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] >= 1


def test_fp_sec_2_search_escapes_like_wildcards(db, ids):
    assert search_jobs(db, "%") == []
    assert search_jobs(db, "_") == []
    with transaction(db):
        db.execute(
            "INSERT INTO jobs(employer_id, title, description, required_skills_json, created_at) "
            "VALUES (?, '100% Reliability_Eng', 'Handles literal % and _', '[]', '2026-01-01T00:00:00+00:00')",
            (ids["acme"],),
        )
    assert [row["title"] for row in search_jobs(db, "%")] == ["100% Reliability_Eng"]
    assert [row["title"] for row in search_jobs(db, "_")] == ["100% Reliability_Eng"]


def test_fp_sec_3_xss_is_escaped(client, login, db, ids):
    payload = "<script>alert(1)</script>"
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"

    # 1) Job title, description, and required skills + company profile
    login("acme")
    assert client.post(
        "/employer/company",
        data={"company_name": f"Acme {payload}", "description": f"Desc {payload}"},
    ).status_code == 302
    company_html = client.get("/employer/company").get_data(as_text=True)
    assert payload not in company_html
    assert escaped in company_html

    response = client.post(
        "/employer/jobs/new",
        data={"title": payload, "description": payload, "required_skills": f"Python, {payload}"},
    )
    assert response.status_code == 302
    job_id = db.execute("SELECT id FROM jobs WHERE title = ?", (payload,)).fetchone()["id"]

    # 2) Candidate profile, resume, job detail, and search query
    client.post("/auth/logout")
    login("alice")
    assert client.post(
        "/candidate/profile",
        data={"display_name": f"Alice {payload}", "skills": f"Python, {payload}"},
    ).status_code == 302
    profile_html = client.get("/candidate/profile").get_data(as_text=True)
    assert payload not in profile_html
    assert escaped in profile_html

    assert client.post(
        f"/candidate/resume/{ids['alice']}",
        data={"resume_text": f"Resume {payload}"},
    ).status_code == 302
    resume_html = client.get(f"/candidate/resume/{ids['alice']}").get_data(as_text=True)
    assert payload not in resume_html
    assert escaped in resume_html

    job_html = client.get(f"/candidate/jobs/{job_id}").get_data(as_text=True)
    assert payload not in job_html
    assert escaped in job_html

    search_html = client.get("/candidate/search", query_string={"q": payload}).get_data(as_text=True)
    assert payload not in search_html
    assert escaped in search_html

    # 3) Employer applicant view also escapes candidate display_name, skills, and resume preview
    client.post("/auth/logout")
    login("acme")
    applicants_html = client.get(f"/employer/jobs/{ids['backend']}/applicants").get_data(as_text=True)
    assert payload not in applicants_html
    assert escaped in applicants_html


def test_security_headers_and_csp(client):
    response = client.get("/")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    csp = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_fp_sec_4_secrets_and_test_isolation(app):
    database = Path(app.config["DATABASE"])
    assert database.name == "test.sqlite3"
    assert app.config["TESTING"] is True

    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "Bearer " not in text


def test_public_demo_banner_and_secure_session_cookie(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "demo.sqlite3"),
            "SECRET_KEY": "demo-test-secret",
            "DEMO_MODE": True,
            "SESSION_COOKIE_SECURE": True,
        }
    )
    client = app.test_client()

    homepage = client.get("/")
    assert "Public course demo" in homepage.get_data(as_text=True)

    insecure = client.get(
        "/",
        headers={"Host": "csc4801.wushenyu.com", "X-Forwarded-Proto": "http"},
    )
    assert insecure.status_code == 308
    assert insecure.headers["Location"] == "https://csc4801.wushenyu.com/"

    cloudflare_http = client.get(
        "/",
        headers={"Host": "csc4801.wushenyu.com", "CF-Visitor": '{"scheme":"http"}'},
    )
    assert cloudflare_http.status_code == 308
    assert cloudflare_http.headers["Location"] == "https://csc4801.wushenyu.com/"

    response = client.post(
        "/auth/register",
        data={"username": "secure-cookie", "password": "StrongPass123!", "role": "Candidate"},
    )
    assert response.status_code == 302
    assert "Secure" in response.headers.get("Set-Cookie", "")
