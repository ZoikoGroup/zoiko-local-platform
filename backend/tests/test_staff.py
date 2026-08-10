from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _create_staff(db_session, email: str, password: str = "staffpass123", role=PlatformStaffRole.SUPER_ADMIN):
    return staff_service.create_staff(db_session, email=email, password=password, role=role)


def test_staff_login_succeeds_with_correct_credentials(client, db_session):
    _create_staff(db_session, "staff1@zoikolocal.com")
    response = client.post(
        "/staff/login", json={"email": "staff1@zoikolocal.com", "password": "staffpass123"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_staff_login_fails_with_wrong_password(client, db_session):
    _create_staff(db_session, "staff2@zoikolocal.com")
    response = client.post(
        "/staff/login", json={"email": "staff2@zoikolocal.com", "password": "wrong"}
    )
    assert response.status_code == 401


def test_staff_login_is_rate_limited_after_repeated_attempts(client, db_session):
    _create_staff(db_session, "staffratelimited@zoikolocal.com")
    responses = [
        client.post(
            "/staff/login", json={"email": "staffratelimited@zoikolocal.com", "password": "wrong-on-purpose"}
        )
        for _ in range(6)
    ]
    statuses = [r.status_code for r in responses]
    assert statuses[:5] == [401, 401, 401, 401, 401]
    assert statuses[5] == 429


def test_there_is_no_public_staff_signup_endpoint(client):
    response = client.post(
        "/staff/signup", json={"email": "hacker@example.com", "password": "whatever123"}
    )
    assert response.status_code == 404


def test_customer_token_cannot_be_used_as_a_staff_token(client):
    """A customer logging in must never be treated as staff, even if they
    happen to be an account Owner."""
    client.post(
        "/auth/signup",
        json={
            "account_name": "Not Staff Co",
            "account_type": "individual",
            "email": "notstaff@example.com",
            "password": "supersecret123",
        },
    )
    login_response = client.post(
        "/auth/login", json={"email": "notstaff@example.com", "password": "supersecret123"}
    )
    customer_token = login_response.json()["access_token"]

    response = client.get("/audit/events", headers={"Authorization": f"Bearer {customer_token}"})
    assert response.status_code == 401


def test_staff_token_cannot_be_used_as_a_customer_token(client, db_session):
    """Symmetric check: a staff login must never work on customer-only
    endpoints either."""
    _create_staff(db_session, "staff3@zoikolocal.com")
    login_response = client.post(
        "/staff/login", json={"email": "staff3@zoikolocal.com", "password": "staffpass123"}
    )
    staff_token = login_response.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 401


def test_list_accounts_requires_staff_auth(client):
    response = client.get("/staff/accounts")
    assert response.status_code == 401


def test_customer_cannot_list_accounts(client):
    client.post(
        "/auth/signup",
        json={
            "account_name": "Overview Test Co",
            "account_type": "business",
            "email": "overviewcustomer@example.com",
            "password": "supersecret123",
        },
    )
    login = client.post(
        "/auth/login", json={"email": "overviewcustomer@example.com", "password": "supersecret123"}
    )
    customer_token = login.json()["access_token"]

    response = client.get("/staff/accounts", headers={"Authorization": f"Bearer {customer_token}"})
    assert response.status_code == 401


def test_staff_can_list_accounts_with_owner_and_counts(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Overview Owner Co",
            "account_type": "business",
            "email": "overviewowner@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    owner_token = client.post(
        "/auth/login", json={"email": "overviewowner@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    client.post(
        "/team/members",
        json={"email": "overviewteammate@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    _create_staff(db_session, "staff4@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "staff4@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get("/staff/accounts", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200
    match = next(a for a in response.json() if a["id"] == account_id)
    assert match["owner_email"] == "overviewowner@example.com"
    assert match["member_count"] == 2
    assert match["number_count"] == 0


def test_search_numbers_requires_staff_auth(client):
    response = client.get("/staff/numbers/search", params={"q": "5550000000"})
    assert response.status_code == 401


def test_search_numbers_finds_a_number_by_partial_e164(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Number Search Co",
            "account_type": "business",
            "email": "numsearchowner@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    number = PhoneNumber(
        e164="+15556667777", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        provider_sid="PNsearchtest0000000000000000000",
    )
    db_session.add(number)
    db_session.commit()

    _create_staff(db_session, "numsearchstaff1@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "numsearchstaff1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get(
        "/staff/numbers/search", params={"q": "5556667777"}, headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["e164"] == "+15556667777"
    assert results[0]["account_name"] == "Number Search Co"
    assert results[0]["account_owner_email"] == "numsearchowner@example.com"


def test_search_numbers_finds_a_number_by_provider_sid(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Number Search Co 2",
            "account_type": "business",
            "email": "numsearchowner2@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    number = PhoneNumber(
        e164="+15556668888", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        provider_sid="PNuniquesidforsearch0000000000000",
    )
    db_session.add(number)
    db_session.commit()

    _create_staff(db_session, "numsearchstaff2@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "numsearchstaff2@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get(
        "/staff/numbers/search", params={"q": "uniquesidforsearch"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 200
    results = response.json()
    assert any(r["e164"] == "+15556668888" for r in results)


def test_search_numbers_with_blank_query_returns_empty(client, db_session):
    _create_staff(db_session, "numsearchstaff3@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "numsearchstaff3@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get(
        "/staff/numbers/search", params={"q": "   "}, headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_search_numbers_returns_empty_for_no_match(client, db_session):
    _create_staff(db_session, "numsearchstaff4@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "numsearchstaff4@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get(
        "/staff/numbers/search", params={"q": "+19999999999999"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


# --- Access matrix (Commercial Billing Operating Standard doc's "formal RBAC/segregation-of-duties matrix") ---


def test_access_matrix_route_returns_seeded_grants(client, db_session):
    _create_staff(db_session, "accessmatrix1@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    staff_token = client.post(
        "/staff/login", json={"email": "accessmatrix1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get("/staff/access-matrix", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200
    # Roles are exposed as the enum's .value (lowercase), same convention
    # as every other enum in this API's JSON responses - the migration's
    # own seed literals are .name (uppercase), matching how this
    # codebase's Postgres enum columns store their values (see
    # PlatformStaffRole/StaffCapabilityGrant's docstrings).
    by_capability = {row["capability"]: row["roles"] for row in response.json()}
    assert by_capability["billing.simulate_payment_event"] == ["super_admin"]
    assert sorted(by_capability["compliance.review_case"]) == ["compliance_officer", "super_admin"]
    assert sorted(by_capability["porting.review_request"]) == ["super_admin", "support"]


def test_access_matrix_route_requires_staff_auth(client):
    response = client.get("/staff/access-matrix")
    assert response.status_code == 401


def test_require_capability_denies_a_role_not_in_the_grant(db_session):
    """Direct unit test of the dependency itself, not just its effect on
    one route - proves the mechanism denies a role genuinely absent from
    the grant, independent of which specific endpoint is wired to it."""
    from app.core.deps import require_capability

    staff = _create_staff(db_session, "capabilitydeny1@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    dependency = require_capability("billing.simulate_payment_event")  # SUPPORT is not granted this

    try:
        dependency(staff=staff, db=db_session)
        assert False, "expected a 403"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


def test_require_capability_fails_closed_for_an_unconfigured_capability(db_session):
    """A capability with zero grant rows (a seeding gap, or a brand new
    route nobody granted yet) must deny every role, including
    SUPER_ADMIN - the opposite failure mode (fail open) would turn a
    missing seed row into an unintended privilege escalation."""
    from app.core.deps import require_capability

    staff = _create_staff(db_session, "capabilitydeny2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    dependency = require_capability("nonexistent.capability")

    try:
        dependency(staff=staff, db=db_session)
        assert False, "expected a 403"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


def test_require_capability_allows_a_granted_role(db_session):
    from app.core.deps import require_capability

    staff = _create_staff(db_session, "capabilityallow1@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    dependency = require_capability("billing.simulate_payment_event")

    result = dependency(staff=staff, db=db_session)
    assert result is staff
