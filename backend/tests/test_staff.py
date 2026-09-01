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
    # Real gap fix (ZL-COM-ENT-001): adding a team member now requires
    # team.members.enabled (Business+).
    from app.billing import service as billing_service

    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")
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
    # Commercial Billing Operating Standard doc P0 - every account is
    # classified from creation, not left "unclassified".
    assert match["billing_classification"] == "commercial_standalone"
    assert match["billing_source"] == "direct_zoiko_local"


def test_super_admin_can_update_an_accounts_billing_classification(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Reclassify Co", "account_type": "business",
            "email": "reclassifyowner@example.com", "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]

    _create_staff(db_session, "staffreclassify1@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "staffreclassify1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.put(
        f"/staff/accounts/{account_id}/billing-classification",
        json={"billing_classification": "demo", "billing_source": "direct_zoiko_local"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["billing_classification"] == "demo"

    listed = client.get("/staff/accounts", headers={"Authorization": f"Bearer {staff_token}"}).json()
    match = next(a for a in listed if a["id"] == account_id)
    assert match["billing_classification"] == "demo"


def test_non_super_admin_staff_cannot_update_billing_classification(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Reclassify Deny Co", "account_type": "business",
            "email": "reclassifydenyowner@example.com", "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]

    _create_staff(db_session, "staffreclassify2@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    staff_token = client.post(
        "/staff/login", json={"email": "staffreclassify2@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.put(
        f"/staff/accounts/{account_id}/billing-classification",
        json={"billing_classification": "DEMO", "billing_source": "DIRECT_ZOIKO_LOCAL"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403


def test_update_billing_classification_rejects_an_unknown_value(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Reclassify Invalid Co", "account_type": "business",
            "email": "reclassifyinvalidowner@example.com", "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]

    _create_staff(db_session, "staffreclassify3@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "staffreclassify3@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.put(
        f"/staff/accounts/{account_id}/billing-classification",
        json={"billing_classification": "NOT_A_REAL_CLASS", "billing_source": "DIRECT_ZOIKO_LOCAL"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 422


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


def test_super_admin_can_add_and_deactivate_a_staff_member(client, db_session):
    """Real gap fix: there was previously no way to add a staff member
    short of direct database/code access - bootstrap only ever creates
    the very first SUPER_ADMIN. Proves POST /staff/team works end-to-end
    for a real SUPER_ADMIN, and that the new account can log in."""
    _create_staff(db_session, "teamadmin1@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    admin_token = client.post(
        "/staff/login", json={"email": "teamadmin1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    create = client.post(
        "/staff/team",
        json={"email": "newteammate1@zoikolocal.com", "password": "supersecret123", "role": "support"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201, create.text
    assert create.json()["email"] == "newteammate1@zoikolocal.com"
    assert create.json()["role"] == "support"
    assert create.json()["is_active"] is True
    new_staff_id = create.json()["id"]

    new_login = client.post(
        "/staff/login", json={"email": "newteammate1@zoikolocal.com", "password": "supersecret123"}
    )
    assert new_login.status_code == 200

    members = client.get("/staff/team", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert any(m["id"] == new_staff_id for m in members)

    deactivate = client.put(
        f"/staff/team/{new_staff_id}/deactivate", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False

    blocked_login = client.post(
        "/staff/login", json={"email": "newteammate1@zoikolocal.com", "password": "supersecret123"}
    )
    assert blocked_login.status_code == 401

    reactivate = client.put(
        f"/staff/team/{new_staff_id}/reactivate", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["is_active"] is True


def test_support_cannot_add_a_staff_member(client, db_session):
    _create_staff(db_session, "teamsupport1@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    support_token = client.post(
        "/staff/login", json={"email": "teamsupport1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.post(
        "/staff/team",
        json={"email": "shouldnotexist1@zoikolocal.com", "password": "supersecret123", "role": "support"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_super_admin_cannot_deactivate_the_only_active_super_admin(client, db_session):
    from app.staff.models import PlatformStaff

    staff = _create_staff(db_session, "teamselfdeactivate1@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    admin_token = client.post(
        "/staff/login", json={"email": "teamselfdeactivate1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    # This file's other tests each create their own SUPER_ADMIN in the same
    # shared test DB - deactivate every other one first so this genuinely
    # exercises "the only active SUPER_ADMIN left", not an artifact of test
    # ordering.
    db_session.query(PlatformStaff).filter(
        PlatformStaff.role == PlatformStaffRole.SUPER_ADMIN, PlatformStaff.id != staff.id
    ).update({"is_active": False})
    db_session.commit()

    response = client.put(
        f"/staff/team/{staff.id}/deactivate", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 409


def test_creating_a_staff_member_with_a_duplicate_email_conflicts(client, db_session):
    _create_staff(db_session, "teamduplicate1@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    admin_token = client.post(
        "/staff/login", json={"email": "teamduplicate1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.post(
        "/staff/team",
        json={"email": "teamduplicate1@zoikolocal.com", "password": "supersecret123", "role": "support"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409


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


# --- Making the access matrix editable ---


def test_grant_capability_requires_matrix_management_capability(client, db_session):
    _create_staff(db_session, "grantauth1@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    token = client.post(
        "/staff/login", json={"email": "grantauth1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.put(
        "/staff/access-matrix/risk.manage_blocked_destinations/support",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_grant_capability_adds_a_role_to_an_existing_capability(client, db_session):
    admin_token = _create_staff_and_login_helper(client, db_session, "grantsuccess1@zoikolocal.com")

    response = client.put(
        "/staff/access-matrix/risk.manage_blocked_destinations/support",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    matrix = {
        row["capability"]: row["roles"]
        for row in client.get(
            "/staff/access-matrix", headers={"Authorization": f"Bearer {admin_token}"}
        ).json()
    }
    assert "support" in matrix["risk.manage_blocked_destinations"]


def test_grant_capability_is_idempotent(client, db_session):
    admin_token = _create_staff_and_login_helper(client, db_session, "grantidempotent1@zoikolocal.com")

    first = client.put(
        "/staff/access-matrix/risk.manage_blocked_destinations/support",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    second = client.put(
        "/staff/access-matrix/risk.manage_blocked_destinations/support",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert first.status_code == 204
    assert second.status_code == 204

    matrix = {
        row["capability"]: row["roles"]
        for row in client.get(
            "/staff/access-matrix", headers={"Authorization": f"Bearer {admin_token}"}
        ).json()
    }
    assert matrix["risk.manage_blocked_destinations"].count("support") == 1


def test_revoke_capability_removes_a_role(client, db_session):
    admin_token = _create_staff_and_login_helper(client, db_session, "revokesuccess1@zoikolocal.com")

    response = client.delete(
        "/staff/access-matrix/porting.review_request/support",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    matrix = {
        row["capability"]: row["roles"]
        for row in client.get(
            "/staff/access-matrix", headers={"Authorization": f"Bearer {admin_token}"}
        ).json()
    }
    assert "support" not in matrix["porting.review_request"]


def test_revoke_capability_refuses_to_remove_the_last_matrix_manager(client, db_session):
    admin_token = _create_staff_and_login_helper(client, db_session, "revokelockout1@zoikolocal.com")

    response = client.delete(
        "/staff/access-matrix/staff.manage_capabilities/super_admin",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409

    # Still able to manage the matrix afterward - proves the revoke was
    # genuinely rejected, not silently accepted then re-granted.
    matrix = {
        row["capability"]: row["roles"]
        for row in client.get(
            "/staff/access-matrix", headers={"Authorization": f"Bearer {admin_token}"}
        ).json()
    }
    assert "super_admin" in matrix["staff.manage_capabilities"]


def _create_staff_and_login_helper(client, db_session, email: str) -> str:
    _create_staff(db_session, email, role=PlatformStaffRole.SUPER_ADMIN)
    return client.post("/staff/login", json={"email": email, "password": "staffpass123"}).json()["access_token"]
