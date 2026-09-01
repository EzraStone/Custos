from custos.api.auth import TokenStore, parse_bearer


def test_valid_token_resolves_to_its_account():
    store = TokenStore({"tok-abc": "447120043318"})
    principal = store.resolve("tok-abc")
    assert principal is not None
    assert principal.account_id == "447120043318"


def test_unknown_and_empty_tokens_resolve_to_nothing():
    store = TokenStore({"tok-abc": "1"})
    assert store.resolve("tok-wrong") is None
    assert store.resolve("") is None


def test_a_token_cannot_be_used_for_another_account():
    """The whole point of the token: it names one account and only that one."""
    store = TokenStore({"tok-a": "111111111111", "tok-b": "222222222222"})
    assert store.resolve("tok-a").account_id == "111111111111"
    assert store.resolve("tok-b").account_id == "222222222222"


def test_tokens_load_from_the_environment():
    store = TokenStore.from_env(
        lambda _: "447120043318:tok-abc, 999999999999:tok-xyz"
    )
    assert len(store) == 2
    assert store.resolve("tok-abc").account_id == "447120043318"
    assert store.resolve("tok-xyz").account_id == "999999999999"


def test_malformed_environment_entries_are_ignored_not_fatal():
    store = TokenStore.from_env(lambda _: ",,justanaccount,:onlytoken,acct:tok,")
    assert len(store) == 1
    assert store.resolve("tok").account_id == "acct"


def test_no_configuration_means_no_valid_tokens():
    """An unconfigured API authenticates nobody rather than everybody."""
    store = TokenStore.from_env(lambda _: None)
    assert len(store) == 0
    assert store.resolve("anything") is None


def test_bearer_parsing():
    assert parse_bearer("Bearer tok-abc") == "tok-abc"
    assert parse_bearer("bearer tok-abc") == "tok-abc"
    assert parse_bearer("Basic dXNlcjpwYXNz") == ""
    assert parse_bearer(None) == ""
    assert parse_bearer("") == ""
    assert parse_bearer("Bearer  padded  ") == "padded"


# --- multi-account tokens -----------------------------------------------------

# One customer in the target profile — 80 to 400 engineers — routinely runs
# five to fifty AWS accounts. A token per account means fifty collectors, fifty
# secrets to rotate, and fifty registers that cannot be read together.
def test_a_token_can_cover_several_accounts():
    store = TokenStore.from_env(
        lambda _: "111111111111:tok-acme, 222222222222:tok-acme, 333333333333:tok-other"
    )
    acme = store.resolve("tok-acme")
    assert acme.accounts == frozenset({"111111111111", "222222222222"})
    assert acme.covers("111111111111") and acme.covers("222222222222")


# The isolation boundary moved from one account to a named set. It did not
# disappear.
def test_a_multi_account_token_still_cannot_reach_an_account_it_lacks():
    store = TokenStore.from_env(lambda _: "111111111111:tok-acme,222222222222:tok-acme")
    acme = store.resolve("tok-acme")
    assert not acme.covers("999999999999")


def test_the_single_account_shortcut_refuses_to_guess():
    """Code assuming one account must fail loudly rather than pick the first,
    which would attribute one account's findings to another."""
    import pytest

    from custos.api.auth import Principal

    single = Principal(accounts=frozenset({"111111111111"}))
    assert single.account_id == "111111111111"

    several = Principal(accounts=frozenset({"111111111111", "222222222222"}))
    with pytest.raises(ValueError, match="covers 2 accounts"):
        _ = several.account_id
