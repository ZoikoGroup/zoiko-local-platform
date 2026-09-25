"""Zoiko_Local_Careers_Dynamic_Jobs_Engineering_Standard doc (ZL-ENG-
CAREERS-001) - real tests for the new careers/requisition API, confirming
the doc's own P0 acceptance conditions actually hold: nothing hard-coded,
role count matches the filtered dataset, lifecycle changes propagate
without a frontend deploy, and a closed/filled role can never stay
publicly applyable."""

from app.careers import service
from app.careers.models import CareerEmploymentType, CareerJobStatus, CareerWorkModel


def _create_staff_and_login(client, db_session, email: str, role=None) -> str:
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role or PlatformStaffRole.SUPER_ADMIN)
    return client.post("/staff/login", json={"email": email, "password": "staffpass123"}).json()["access_token"]


def _create_open_job(db_session, *, slug: str, title: str = "Backend Engineer", team: str = "Engineering",
                      work_model: CareerWorkModel = CareerWorkModel.REMOTE, job_locations: str = "India",
                      actor: str = "test-staff") -> str:
    job = service.create_requisition(
        db_session, slug=slug, title=title, hiring_org="Zoiko Local", team=team,
        employment_type=CareerEmploymentType.FULL_TIME, work_model=work_model, job_locations=job_locations,
        apply_url="https://example.com/apply", actor=actor,
    )
    service.transition_status(db_session, job.id, new_status=CareerJobStatus.APPROVED, actor=actor)
    service.transition_status(db_session, job.id, new_status=CareerJobStatus.OPEN, actor=actor)
    return job.id


def test_public_list_only_returns_open_jobs(client, db_session):
    open_id = _create_open_job(db_session, slug="open-role-1")
    draft = service.create_requisition(
        db_session, slug="draft-role-1", title="Still Drafting", hiring_org="Zoiko Local", team="Engineering",
        employment_type=CareerEmploymentType.FULL_TIME, work_model=CareerWorkModel.REMOTE, job_locations="India",
        apply_url="https://example.com/apply", actor="test-staff",
    )

    response = client.get("/api/v1/careers/jobs")
    assert response.status_code == 200
    body = response.json()
    job_ids = {j["job_id"] for j in body["jobs"]}
    assert open_id in job_ids
    assert draft.id not in job_ids


def test_public_role_count_matches_the_filtered_dataset(client, db_session):
    """Doc's own acceptance condition: "The role count always matches the
    current filtered result dataset" - not a separately-computed number
    that could disagree with what's actually shown."""
    _create_open_job(db_session, slug="eng-role-1", team="Engineering")
    _create_open_job(db_session, slug="eng-role-2", team="Engineering")
    _create_open_job(db_session, slug="sales-role-1", team="Sales")

    response = client.get("/api/v1/careers/jobs", params={"team": "Engineering"})
    body = response.json()
    assert body["total"] == 2
    assert len(body["jobs"]) == 2
    assert all(j["team"] == "Engineering" for j in body["jobs"])


def test_public_job_detail_404s_for_a_closed_role(client, db_session):
    """Doc Table 8: FILLED/CLOSED/CANCELED must be removed from search and
    detail immediately - "A closed or filled role cannot remain publicly
    applyable because of stale cache" (doc §6.1 definition of done)."""
    job_id = _create_open_job(db_session, slug="soon-filled-role")
    assert client.get(f"/api/v1/careers/jobs/{job_id}").status_code == 200

    service.transition_status(db_session, job_id, new_status=CareerJobStatus.FILLED, actor="test-staff")

    assert client.get(f"/api/v1/careers/jobs/{job_id}").status_code == 404
    listed_ids = {j["job_id"] for j in client.get("/api/v1/careers/jobs").json()["jobs"]}
    assert job_id not in listed_ids


def test_public_job_detail_works_by_slug_too(client, db_session):
    job_id = _create_open_job(db_session, slug="slug-lookup-role")
    response = client.get("/api/v1/careers/jobs/slug-lookup-role")
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id


def test_public_filters_are_derived_from_real_open_jobs_not_hard_coded(client, db_session):
    _create_open_job(db_session, slug="filters-role-1", team="Design", work_model=CareerWorkModel.HYBRID, job_locations="Bengaluru")

    response = client.get("/api/v1/careers/filters")
    body = response.json()
    assert "Design" in body["teams"]
    assert "Bengaluru" in body["locations"]
    assert "hybrid" in body["work_models"]


def test_lifecycle_status_change_propagates_to_public_api_immediately(client, db_session):
    """Doc §6.1: "Changing a requisition status in the authoritative
    backend changes the website without a frontend code deployment" -
    this is the cache-invalidation guarantee, exercised end-to-end."""
    job = service.create_requisition(
        db_session, slug="lifecycle-role", title="Support Engineer", hiring_org="Zoiko Local", team="Support",
        employment_type=CareerEmploymentType.FULL_TIME, work_model=CareerWorkModel.ON_SITE, job_locations="Remote Eligible",
        apply_url="https://example.com/apply", actor="test-staff",
    )
    assert job.id not in {j["job_id"] for j in client.get("/api/v1/careers/jobs").json()["jobs"]}

    service.transition_status(db_session, job.id, new_status=CareerJobStatus.APPROVED, actor="test-staff")
    service.transition_status(db_session, job.id, new_status=CareerJobStatus.OPEN, actor="test-staff")
    assert job.id in {j["job_id"] for j in client.get("/api/v1/careers/jobs").json()["jobs"]}

    service.transition_status(db_session, job.id, new_status=CareerJobStatus.CLOSED, actor="test-staff")
    assert job.id not in {j["job_id"] for j in client.get("/api/v1/careers/jobs").json()["jobs"]}


def test_remote_role_requires_real_location(db_session):
    """Doc §4 "Remote" rule - never label a role fully remote without
    stating real eligible geography."""
    try:
        service.create_requisition(
            db_session, slug="bad-remote-role", title="X", hiring_org="Zoiko Local", team="Engineering",
            employment_type=CareerEmploymentType.FULL_TIME, work_model=CareerWorkModel.REMOTE, job_locations="   ",
            apply_url="https://example.com/apply", actor="test-staff",
        )
        assert False, "expected RemoteLocationRequiredError"
    except service.RemoteLocationRequiredError:
        pass


def test_invalid_status_transition_is_rejected(db_session):
    """Doc lifecycle table - FILLED/CLOSED/CANCELED are terminal; a role
    can't be silently resurrected back to OPEN."""
    job = service.create_requisition(
        db_session, slug="terminal-role", title="X", hiring_org="Zoiko Local", team="Engineering",
        employment_type=CareerEmploymentType.FULL_TIME, work_model=CareerWorkModel.REMOTE, job_locations="India",
        apply_url="https://example.com/apply", actor="test-staff",
    )
    service.transition_status(db_session, job.id, new_status=CareerJobStatus.CANCELED, actor="test-staff")
    try:
        service.transition_status(db_session, job.id, new_status=CareerJobStatus.OPEN, actor="test-staff")
        assert False, "expected InvalidStatusTransitionError"
    except service.InvalidStatusTransitionError:
        pass


def test_staff_without_capability_cannot_manage_requisitions(client, db_session):
    from app.staff.models import PlatformStaffRole

    token = _create_staff_and_login(client, db_session, "careerssupport1@example.com", role=PlatformStaffRole.SUPPORT)
    response = client.post(
        "/staff/careers",
        json={
            "slug": "no-access-role", "title": "X", "team": "Engineering", "employment_type": "full_time",
            "work_model": "remote", "job_locations": "India", "apply_url": "https://example.com/apply",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_super_admin_can_create_and_publish_a_requisition_via_the_api(client, db_session):
    token = _create_staff_and_login(client, db_session, "careersadmin1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post(
        "/staff/careers",
        json={
            "slug": "api-created-role", "title": "Platform Engineer", "team": "Engineering",
            "employment_type": "full_time", "work_model": "hybrid", "job_locations": "Bengaluru",
            "apply_url": "https://example.com/apply",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["id"]
    assert create_response.json()["status"] == "draft"

    approve = client.post(f"/staff/careers/{job_id}/status", json={"status": "approved"}, headers=headers)
    assert approve.status_code == 200
    open_response = client.post(f"/staff/careers/{job_id}/status", json={"status": "open"}, headers=headers)
    assert open_response.status_code == 200

    public = client.get(f"/api/v1/careers/jobs/{job_id}")
    assert public.status_code == 200
    assert public.json()["title"] == "Platform Engineer"


def test_compensation_is_only_shown_publicly_when_disclosed(client, db_session):
    """Doc §4 "Compensation" rule - visible copy must derive from the
    Compensation Registry state, never invented, never leaked when
    withheld."""
    job = service.create_requisition(
        db_session, slug="comp-hidden-role", title="X", hiring_org="Zoiko Local", team="Engineering",
        employment_type=CareerEmploymentType.FULL_TIME, work_model=CareerWorkModel.REMOTE, job_locations="India",
        apply_url="https://example.com/apply", actor="test-staff",
        compensation_min_minor_units=5000000, compensation_max_minor_units=8000000, compensation_currency="USD",
    )
    service.transition_status(db_session, job.id, new_status=CareerJobStatus.APPROVED, actor="test-staff")
    service.transition_status(db_session, job.id, new_status=CareerJobStatus.OPEN, actor="test-staff")

    body = client.get(f"/api/v1/careers/jobs/{job.id}").json()
    assert body["compensation_state"] == "not_required"
    assert body["compensation"] is None
