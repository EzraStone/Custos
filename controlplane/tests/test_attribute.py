from custos.attribute import Method, PrincipalFacts, resolve, role_name

ARN = "arn:aws:iam::447120043318:role/payments/billing-sync-task"


def test_resource_tags_win_over_everything():
    a = resolve(PrincipalFacts(
        principal=ARN,
        resource_tags={"team": "payments-platform", "contact": "pay@x.com"},
        role_tags={"team": "legacy"},
        iam_path="/other/",
    ))
    assert a.method is Method.RESOURCE_TAG
    assert (a.team, a.contact) == ("payments-platform", "pay@x.com")
    assert a.confidence > 0.9


def test_role_tags_are_used_when_resource_tags_are_absent():
    a = resolve(PrincipalFacts(principal=ARN, role_tags={"Owner": "growth"}))
    assert a.method is Method.ROLE_TAG
    assert a.team == "growth"


def test_iam_path_is_a_real_convention_and_is_used():
    a = resolve(PrincipalFacts(principal=ARN, iam_path="/payments/service-role/"))
    assert a.method is Method.PATH_CONVENTION
    assert a.team == "payments"
    assert 0.5 < a.confidence < 0.8


def test_service_role_path_is_not_mistaken_for_a_team():
    a = resolve(PrincipalFacts(
        principal="arn:aws:iam::1:role/service-role/plain", iam_path="/service-role/"
    ))
    assert a.method is not Method.PATH_CONVENTION


def test_name_heuristic_is_reported_as_weak():
    a = resolve(PrincipalFacts(principal="arn:aws:iam::1:role/checkout-worker"))
    assert a.method is Method.NAME_HEURISTIC
    assert a.team == "checkout"
    assert a.confidence < 0.5, "a guess must never be presented as a fact"


def test_unresolvable_principals_return_unresolved():
    a = resolve(PrincipalFacts(principal="arn:aws:iam::1:role/svc0001"))
    assert not a.resolved
    assert a.method is Method.NONE


def test_blank_tags_do_not_count_as_attribution():
    a = resolve(PrincipalFacts(principal="arn:aws:iam::1:role/svc0001",
                               role_tags={"team": "   "}))
    assert not a.resolved


def test_role_name_extraction_handles_paths():
    assert role_name(ARN) == "billing-sync-task"
    assert role_name("arn:aws:iam::1:role/plain") == "plain"
