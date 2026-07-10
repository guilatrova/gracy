"""Config-layer unit tests: UNSET/None semantics, precedence chain, retry policy math,
duration parsing, status policies, on= map validation, and frozen dataclass guarantees.
"""

from __future__ import annotations

import dataclasses

import pytest

from gracy import (
    UNSET,
    Backoff,
    Concurrency,
    GracyConfig,
    GracyConfigError,
    GracyRequestFailed,
    LogEvent,
    LogLevel,
    Retry,
    Unset,
    allow,
    raises,
    status,
    strict,
)
from gracy.config import (
    DEFAULT_STATUS_POLICY,
    LIBRARY_DEFAULTS,
    Queue,
    StatusSet,
    apply_url_overrides,
    parse_duration,
    resolve_chain,
    validate_on_map,
)
from gracy.endpoints import EndpointMethod, get


# --------------------------------------------------------------------------- UNSET sentinel


class TestUnsetSentinel:
    def test_unset_is_singleton(self):
        assert Unset() is UNSET

    def test_unset_is_falsy_and_distinct_from_none(self):
        assert not UNSET
        assert UNSET is not None
        assert repr(UNSET) == "UNSET"


# --------------------------------------------------------------------------- UNSET vs None semantics


class TestUnsetVsNone:
    def test_unset_inherits_retry_from_outer_layer(self):
        client = GracyConfig(retry=Retry(on=status(500), attempts=5))
        endpoint = GracyConfig()  # retry=UNSET -> inherit
        resolved = resolve_chain(client, endpoint)
        assert isinstance(resolved.retry, Retry)
        assert resolved.retry.attempts == 5

    def test_none_disables_inherited_retry(self):
        client = GracyConfig(retry=Retry(on=status(500), attempts=5))
        endpoint = GracyConfig(retry=None)  # explicit None -> disabled
        resolved = resolve_chain(client, endpoint)
        assert resolved.retry is None

    def test_none_at_inner_layer_beats_outer_value_for_any_field(self):
        client = GracyConfig(timeout=9.0, log_request=LogEvent(LogLevel.INFO))
        endpoint = GracyConfig(timeout=None, log_request=None)
        resolved = resolve_chain(client, endpoint)
        assert resolved.timeout is None
        assert resolved.log_request is None

    def test_merged_under_self_wins_only_for_set_fields(self):
        outer = GracyConfig(timeout=1.0, retry=Retry(on=status(503)))
        inner = GracyConfig(timeout=2.0)  # retry stays UNSET
        merged = inner.merged_under(outer)
        assert merged.timeout == 2.0
        assert merged.retry == outer.retry  # inherited


# --------------------------------------------------------------------------- resolve_chain precedence


class TestResolveChainPrecedence:
    def test_innermost_layer_wins(self):
        client = GracyConfig(timeout=1.0)
        namespace = GracyConfig(timeout=2.0)
        endpoint = GracyConfig(timeout=3.0)
        options = GracyConfig(timeout=4.0)
        assert resolve_chain(client, namespace, endpoint, options).timeout == 4.0
        assert resolve_chain(client, namespace, endpoint).timeout == 3.0
        assert resolve_chain(client, namespace).timeout == 2.0
        assert resolve_chain(client).timeout == 1.0

    def test_each_field_resolves_independently(self):
        client = GracyConfig(timeout=1.0, retry=Retry(on=status(500), attempts=2))
        namespace = GracyConfig(log_response=LogEvent(LogLevel.DEBUG))
        endpoint = GracyConfig(timeout=7.0)
        resolved = resolve_chain(client, namespace, endpoint)
        assert resolved.timeout == 7.0  # endpoint
        assert resolved.log_response == LogEvent(LogLevel.DEBUG)  # namespace
        assert isinstance(resolved.retry, Retry) and resolved.retry.attempts == 2  # client

    def test_none_layers_are_skipped(self):
        client = GracyConfig(timeout=5.0)
        resolved = resolve_chain(client, None, None)
        assert resolved.timeout == 5.0

    def test_no_layers_yields_library_defaults(self):
        assert resolve_chain() == LIBRARY_DEFAULTS

    def test_resolved_config_has_no_unset_fields(self):
        resolved = resolve_chain(GracyConfig(retry=Retry(on=status(500))))
        for f in dataclasses.fields(resolved):
            assert getattr(resolved, f.name) is not UNSET, f.name


# --------------------------------------------------------------------------- library defaults survive partial configs


class TestLibraryDefaults:
    def test_partial_config_keeps_default_error_logging(self):
        user = GracyConfig(retry=Retry(on=status(500)))
        resolved = resolve_chain(user)
        assert resolved.log_errors == LogEvent(LogLevel.ERROR)

    def test_partial_config_keeps_other_defaults(self):
        user = GracyConfig(retry=Retry(on=status(500)))
        resolved = resolve_chain(user)
        assert resolved.timeout == 30.0
        assert resolved.status_policy is DEFAULT_STATUS_POLICY
        assert resolved.queue == Queue()
        assert resolved.throttle is None
        assert resolved.concurrency is None
        assert resolved.log_request is None
        assert resolved.log_response is None

    def test_user_can_still_disable_error_logging_explicitly(self):
        resolved = resolve_chain(GracyConfig(log_errors=None))
        assert resolved.log_errors is None

    def test_library_defaults_fully_set(self):
        for f in dataclasses.fields(LIBRARY_DEFAULTS):
            assert getattr(LIBRARY_DEFAULTS, f.name) is not UNSET, f.name


# --------------------------------------------------------------------------- URL-glob overrides


class TestApplyUrlOverrides:
    def test_matching_glob_overrides_field(self):
        cfg = resolve_chain(
            GracyConfig(
                timeout=30.0,
                overrides={"https://api.example.com/users/*": GracyConfig(timeout=5.0)},
            )
        )
        out = apply_url_overrides(cfg, "https://api.example.com/users/42")
        assert out.timeout == 5.0

    def test_non_matching_glob_leaves_config_untouched(self):
        cfg = resolve_chain(
            GracyConfig(
                timeout=30.0,
                overrides={"https://api.example.com/users/*": GracyConfig(timeout=5.0)},
            )
        )
        out = apply_url_overrides(cfg, "https://api.example.com/posts/1")
        assert out.timeout == 30.0

    def test_partial_override_inherits_unset_fields(self):
        base_retry = Retry(on=status(503), attempts=4)
        cfg = resolve_chain(
            GracyConfig(
                retry=base_retry,
                overrides={"*/slow/*": GracyConfig(timeout=60.0)},
            )
        )
        out = apply_url_overrides(cfg, "https://x/slow/thing")
        assert out.timeout == 60.0
        assert out.retry == base_retry  # not clobbered by the partial override

    def test_multiple_matching_patterns_apply_later_on_top(self):
        overrides = {
            "*example.com*": GracyConfig(timeout=10.0, log_request=LogEvent(LogLevel.INFO)),
            "*example.com/admin/*": GracyConfig(timeout=1.0),
        }
        cfg = resolve_chain(GracyConfig(overrides=overrides))
        out = apply_url_overrides(cfg, "https://example.com/admin/panel")
        assert out.timeout == 1.0  # later pattern wins
        assert out.log_request == LogEvent(LogLevel.INFO)  # earlier pattern still contributes

    def test_no_overrides_returns_config_unchanged(self):
        cfg = resolve_chain(GracyConfig(timeout=3.0))
        assert apply_url_overrides(cfg, "https://anything") is cfg

    def test_unset_overrides_returns_config_unchanged(self):
        cfg = GracyConfig(timeout=3.0)  # overrides is UNSET
        assert apply_url_overrides(cfg, "https://anything") is cfg


# --------------------------------------------------------------------------- endpoint decorator config


class TestEndpointDecoratorConfig:
    def test_kwargs_produce_gracy_config_fields(self):
        retry = Retry(on=status(500), attempts=2)

        @get("/pokemon/{name}", retry=retry, timeout=9.0)
        async def get_pokemon(self, name: str) -> dict: ...

        assert isinstance(get_pokemon, EndpointMethod)
        cfg = get_pokemon.config
        assert isinstance(cfg, GracyConfig)
        assert cfg.retry == retry
        assert cfg.timeout == 9.0
        # untouched knobs stay UNSET (inherit)
        assert cfg.log_errors is UNSET
        assert cfg.status_policy is UNSET
        assert cfg.throttle is UNSET

    def test_no_kwargs_means_no_config_layer(self):
        @get("/pokemon/{name}")
        async def get_pokemon(self, name: str) -> dict: ...

        assert get_pokemon.config is None

    def test_explicit_none_kwarg_is_a_disable_layer(self):
        @get("/pokemon/{name}", retry=None)
        async def get_pokemon(self, name: str) -> dict: ...

        cfg = get_pokemon.config
        assert cfg is not None
        assert cfg.retry is None
        assert cfg.timeout is UNSET

    def test_unknown_kwarg_raises_config_error(self):
        with pytest.raises(GracyConfigError, match="Unknown @get"):

            @get("/x", retriez=3)
            async def bad(self) -> dict: ...

    def test_endpoint_config_layers_under_client_config(self):
        client = GracyConfig(timeout=30.0, retry=Retry(on=status(503), attempts=9))

        @get("/x", timeout=2.0)
        async def ep(self) -> dict: ...

        resolved = resolve_chain(client, ep.config)
        assert resolved.timeout == 2.0
        assert isinstance(resolved.retry, Retry) and resolved.retry.attempts == 9


# --------------------------------------------------------------------------- Retry.matches


class TestRetryMatches:
    def test_status_set_matches_listed_code(self):
        retry = Retry(on=status(429, 503))
        assert retry.matches(503, None) is True
        assert retry.matches(429, None) is True

    def test_status_set_rejects_other_codes_and_none(self):
        retry = Retry(on=status(429, 503))
        assert retry.matches(500, None) is False
        assert retry.matches(None, None) is False

    def test_exception_class_matches_instance_and_subclass(self):
        retry = Retry(on=ConnectionError)
        assert retry.matches(None, ConnectionError("boom")) is True
        assert retry.matches(None, ConnectionResetError("boom")) is True  # subclass
        assert retry.matches(None, ValueError("nope")) is False

    def test_request_failed_wrapper_is_unwrapped_to_original_exc(self):
        retry = Retry(on=ConnectionError)
        wrapped = GracyRequestFailed("https://x", ConnectionError("refused"))
        assert retry.matches(None, wrapped) is True

    def test_wrapper_itself_matches_when_on_targets_wrapper_class(self):
        retry = Retry(on=GracyRequestFailed)
        wrapped = GracyRequestFailed("https://x", ValueError("anything"))
        assert retry.matches(None, wrapped) is True

    def test_wrapper_with_non_matching_original_does_not_match(self):
        retry = Retry(on=TimeoutError)
        wrapped = GracyRequestFailed("https://x", ValueError("boom"))
        assert retry.matches(None, wrapped) is False

    def test_tuple_of_mixed_matchers(self):
        retry = Retry(on=(status(500), TimeoutError))
        assert retry.matches(500, None) is True
        assert retry.matches(None, TimeoutError()) is True
        assert retry.matches(None, GracyRequestFailed("u", TimeoutError())) is True
        assert retry.matches(404, None) is False
        assert retry.matches(None, ValueError()) is False

    def test_attempts_below_one_rejected(self):
        with pytest.raises(GracyConfigError, match="attempts"):
            Retry(on=status(500), attempts=0)


# --------------------------------------------------------------------------- Retry.delay_for


class TestRetryDelayFor:
    def test_flat_wait_is_constant_across_attempts(self):
        retry = Retry(on=status(500), wait=2.5)
        assert retry.delay_for(1, 500) == 2.5
        assert retry.delay_for(3, 500) == 2.5

    def test_backoff_wait_grows_with_attempt(self):
        retry = Retry(on=status(500), wait=Backoff(initial=0.5, multiplier=2.0))
        assert retry.delay_for(1, 500) == 0.5
        assert retry.delay_for(2, 500) == 1.0
        assert retry.delay_for(3, 500) == 2.0

    def test_backoff_max_caps_delay(self):
        retry = Retry(on=status(500), wait=Backoff(initial=1.0, multiplier=10.0, max=3.0))
        assert retry.delay_for(1, 500) == 1.0
        assert retry.delay_for(2, 500) == 3.0
        assert retry.delay_for(5, 500) == 3.0

    def test_per_status_override_beats_wait(self):
        retry = Retry(
            on=status(429, 500),
            wait=Backoff(initial=1.0, multiplier=2.0),
            overrides={429: 7.5},
        )
        assert retry.delay_for(1, 429) == 7.5
        assert retry.delay_for(4, 429) == 7.5  # override ignores attempt/backoff
        assert retry.delay_for(2, 500) == 2.0  # non-overridden status uses backoff

    def test_override_ignored_when_status_is_none(self):
        retry = Retry(on=(status(429), TimeoutError), wait=1.5, overrides={429: 9.0})
        assert retry.delay_for(1, None) == 1.5

    def test_jitter_delay_stays_within_bounds(self):
        retry = Retry(on=status(500), wait=Backoff(initial=2.0, jitter=True))
        samples = [retry.delay_for(1, 500) for _ in range(300)]
        assert all(1.0 <= s <= 3.0 for s in samples)  # [0.5x, 1.5x]
        assert len(set(samples)) > 1  # actually jittering


# --------------------------------------------------------------------------- Backoff.compute


class TestBackoffCompute:
    def test_sequence_without_multiplier_is_flat(self):
        b = Backoff(initial=1.5)
        assert [b.compute(a) for a in (1, 2, 3)] == [1.5, 1.5, 1.5]

    def test_exponential_sequence(self):
        b = Backoff(initial=0.5, multiplier=2.0)
        assert [b.compute(a) for a in (1, 2, 3, 4)] == [0.5, 1.0, 2.0, 4.0]

    def test_max_caps_the_sequence(self):
        b = Backoff(initial=0.5, multiplier=2.0, max=1.7)
        assert [b.compute(a) for a in (1, 2, 3, 4)] == [0.5, 1.0, 1.7, 1.7]

    def test_jitter_within_half_to_one_and_a_half(self):
        b = Backoff(initial=4.0, jitter=True)
        samples = [b.compute(1) for _ in range(300)]
        assert all(2.0 <= s <= 6.0 for s in samples)
        assert len(set(samples)) > 1

    def test_jitter_applied_after_max_cap(self):
        b = Backoff(initial=100.0, multiplier=1.0, max=2.0, jitter=True)
        samples = [b.compute(1) for _ in range(300)]
        assert all(1.0 <= s <= 3.0 for s in samples)  # jitter of capped 2.0, not of 100


# --------------------------------------------------------------------------- parse_duration


class TestParseDuration:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("500ms", 0.5),
            ("1s", 1.0),
            ("2m", 120.0),
            ("1h", 3600.0),
            ("1.5s", 1.5),
            (2, 2.0),
            (0.25, 0.25),
        ],
    )
    def test_valid_durations(self, value, expected):
        result = parse_duration(value)
        assert result == pytest.approx(expected)
        assert isinstance(result, float)

    @pytest.mark.parametrize("bad", ["1 day", "abc", "s", "-1s", "", "10x"])
    def test_invalid_durations_raise(self, bad):
        with pytest.raises(GracyConfigError, match="Invalid duration"):
            parse_duration(bad)


# --------------------------------------------------------------------------- strict / allow / raises / on=


class TestStatusPolicyHelpers:
    def test_strict_builds_policy(self):
        p = strict(200, 201)
        assert p.kind == "strict"
        assert p.codes == (200, 201)

    def test_allow_builds_policy(self):
        p = allow(404)
        assert p.kind == "allow"
        assert p.codes == (404,)

    def test_strict_without_codes_errors(self):
        with pytest.raises(GracyConfigError, match="at least one status code"):
            strict()

    def test_allow_without_codes_errors(self):
        with pytest.raises(GracyConfigError, match="at least one status code"):
            allow()


class TestRaisesHelper:
    def test_raises_wraps_exception_class(self):
        class NotFoundError(Exception):
            pass

        action = raises(NotFoundError)
        assert action.exc is NotFoundError

    @pytest.mark.parametrize("bad", [42, "boom", ValueError("instance"), int, None])
    def test_raises_rejects_non_exception_classes(self, bad):
        with pytest.raises(GracyConfigError, match="expects an exception class"):
            raises(bad)


class TestValidateOnMap:
    def test_accepts_int_keys_default_key_and_valid_actions(self):
        class NotFound(Exception):
            pass

        validate_on_map(
            {
                404: raises(NotFound),
                204: None,
                500: lambda r: r,
                "default": "fallback-literal",
            }
        )  # no exception

    def test_rejects_bare_exception_class_with_helpful_message(self):
        class NotFoundError(Exception):
            pass

        with pytest.raises(GracyConfigError) as ei:
            validate_on_map({404: NotFoundError})
        msg = str(ei.value)
        assert "bare exception classes" in msg
        assert "raises(NotFoundError)" in msg  # tells the user the fix

    def test_rejects_non_int_non_default_keys(self):
        with pytest.raises(GracyConfigError, match="int status codes or 'default'"):
            validate_on_map({"404": None})

    def test_gracy_config_validates_on_map_at_construction(self):
        class Boom(Exception):
            pass

        with pytest.raises(GracyConfigError, match="raises\\(Boom\\)"):
            GracyConfig(on={500: Boom})


# --------------------------------------------------------------------------- concurrency shorthand


class TestConcurrencyShorthand:
    def test_int_becomes_concurrency_object(self):
        cfg = GracyConfig(concurrency=5)
        assert isinstance(cfg.concurrency, Concurrency)
        assert cfg.concurrency.limit == 5
        assert cfg.concurrency.match is None
        assert cfg.concurrency.per_uurl is False

    def test_explicit_concurrency_object_passes_through(self):
        conc = Concurrency(limit=2, per_uurl=True)
        cfg = GracyConfig(concurrency=conc)
        assert cfg.concurrency is conc

    def test_invalid_int_shorthand_rejected_by_concurrency_validation(self):
        with pytest.raises(GracyConfigError, match="limit must be >= 1"):
            GracyConfig(concurrency=0)

    def test_shorthand_survives_merge(self):
        resolved = resolve_chain(GracyConfig(concurrency=3))
        assert isinstance(resolved.concurrency, Concurrency)
        assert resolved.concurrency.limit == 3


# --------------------------------------------------------------------------- frozen-ness


class TestFrozenConfigs:
    def test_gracy_config_is_frozen(self):
        cfg = GracyConfig(timeout=1.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.timeout = 99.0

    def test_retry_is_frozen(self):
        retry = Retry(on=status(500))
        with pytest.raises(dataclasses.FrozenInstanceError):
            retry.attempts = 10

    def test_backoff_is_frozen(self):
        b = Backoff()
        with pytest.raises(dataclasses.FrozenInstanceError):
            b.initial = 5.0

    def test_status_set_and_log_event_are_frozen(self):
        s = StatusSet((200,))
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.codes = (500,)
        ev = LogEvent(LogLevel.INFO)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.level = LogLevel.ERROR

    def test_merge_returns_new_object_without_mutating_inputs(self):
        outer = GracyConfig(timeout=1.0)
        inner = GracyConfig(timeout=2.0)
        merged = inner.merged_under(outer)
        assert merged is not inner and merged is not outer
        assert outer.timeout == 1.0
        assert inner.timeout == 2.0
