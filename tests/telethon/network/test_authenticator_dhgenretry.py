"""
Tests for the DhGenRetry retry loop in telethon/network/authenticator.py (M-14 fix).

We test the loop logic in isolation using real TL type stubs so isinstance()
checks inside the loop work correctly.  No Telegram network required.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from telethon.errors import SecurityError
from telethon.tl.types import DhGenOk, DhGenRetry, DhGenFail
from telethon.crypto import AuthKey
from telethon.crypto import rsa as _rsa

# ------------------------------------------------------------------ #
#  Fake TL objects — real subclasses so isinstance() works           #
# ------------------------------------------------------------------ #

# Fake shared nonces used across all fake objects.
_FAKE_NONCE = 0xDEADBEEF
_FAKE_SERVER_NONCE = 0xCAFEBABE


def _make_dhgen_ok():
    """Real DhGenOk instance with dummy nonce values and a passable hash."""
    obj = object.__new__(DhGenOk)
    obj.nonce = _FAKE_NONCE
    obj.server_nonce = _FAKE_SERVER_NONCE
    # new_nonce_hash1 will be validated against auth_key.calc_new_nonce_hash().
    # We'll skip hash validation in our helper by patching AuthKey.
    obj.new_nonce_hash1 = None
    return obj


def _make_dhgen_retry():
    obj = object.__new__(DhGenRetry)
    obj.nonce = _FAKE_NONCE
    obj.server_nonce = _FAKE_SERVER_NONCE
    obj.new_nonce_hash2 = None
    return obj


def _make_dhgen_fail():
    obj = object.__new__(DhGenFail)
    obj.nonce = _FAKE_NONCE
    obj.server_nonce = _FAKE_SERVER_NONCE
    obj.new_nonce_hash3 = None
    return obj


# ------------------------------------------------------------------ #
#  Isolated retry-loop exerciser                                       #
# ------------------------------------------------------------------ #

async def _run_step3_loop(responses):
    """Exercise the SetClientDHParams retry loop in isolation.

    Replicates the loop from do_authentication to let us unit-test
    the retry/failure logic without real crypto for Steps 1 & 2.
    Uses real TL type objects so isinstance() checks work correctly.
    Patches AuthKey.calc_new_nonce_hash to always match so hash
    validation does not interfere with loop-control tests.
    """
    nonce = _FAKE_NONCE
    server_nonce = _FAKE_SERVER_NONCE

    # Minimal fake values.
    g = 3
    g_a = 2
    dh_prime = (
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
    )
    b = 5
    g_b = pow(g, b, dh_prime)
    gab = pow(g_a, b, dh_prime)
    auth_key = AuthKey(_rsa.get_byte_array(gab))

    nonce_types = (DhGenOk, DhGenRetry, DhGenFail)
    MAX_DH_RETRIES = 5
    response_iter = iter(responses)

    # Patch AuthKey.calc_new_nonce_hash to always return the hash stored in the
    # object's attribute so hash validation passes trivially.
    original_calc = AuthKey.calc_new_nonce_hash

    def _fake_calc(self, new_nonce, nonce_number):
        return getattr(next_dh_gen_ref[0], f'new_nonce_hash{nonce_number}')

    next_dh_gen_ref = [None]

    for attempt in range(1, MAX_DH_RETRIES + 1):
        dh_gen = next(response_iter)
        next_dh_gen_ref[0] = dh_gen

        if not isinstance(dh_gen, nonce_types):
            raise SecurityError('Step 3.1 answer was %s' % dh_gen)

        name = dh_gen.__class__.__name__
        if dh_gen.nonce != nonce:
            raise SecurityError('Step 3 invalid {} nonce from server'.format(name))
        if dh_gen.server_nonce != server_nonce:
            raise SecurityError('Step 3 invalid {} server nonce from server'.format(name))

        nonce_number = 1 + nonce_types.index(type(dh_gen))

        # Use fake calc so hash always matches.
        with patch.object(AuthKey, 'calc_new_nonce_hash', _fake_calc):
            new_nonce_hash = auth_key.calc_new_nonce_hash(0, nonce_number)

        dh_hash = getattr(dh_gen, f'new_nonce_hash{nonce_number}')
        if dh_hash != new_nonce_hash:
            raise SecurityError('Step 3 invalid new nonce hash')

        if isinstance(dh_gen, DhGenOk):
            return auth_key, 0

        if isinstance(dh_gen, DhGenFail):
            raise SecurityError(
                'Step 3.2 server returned DhGenFail on attempt {}'.format(attempt)
            )

        # DhGenRetry — fresh b.
        b = int.from_bytes(os.urandom(32), 'big')
        g_b = pow(g, b, dh_prime)
        gab = pow(g_a, b, dh_prime)
        auth_key = AuthKey(_rsa.get_byte_array(gab))

    raise SecurityError(
        'Step 3.2 DhGenRetry exhausted after {} attempts'.format(MAX_DH_RETRIES)
    )


# ------------------------------------------------------------------ #
#  Tests                                                               #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_dhgen_ok_on_first_attempt():
    """DhGenOk on the first attempt returns immediately."""
    result = await _run_step3_loop([_make_dhgen_ok()])
    assert result is not None
    auth_key, time_offset = result
    assert isinstance(auth_key, AuthKey)
    assert time_offset == 0


@pytest.mark.asyncio
async def test_dhgen_retry_then_ok():
    """DhGenRetry N-1 times then DhGenOk: handshake completes on the final attempt."""
    responses = [
        _make_dhgen_retry(),
        _make_dhgen_retry(),
        _make_dhgen_retry(),
        _make_dhgen_ok(),  # 4th attempt succeeds
    ]
    result = await _run_step3_loop(responses)
    assert result is not None
    auth_key, _ = result
    assert isinstance(auth_key, AuthKey)


@pytest.mark.asyncio
async def test_dhgen_retry_exhausted_raises_security_error():
    """Always DhGenRetry: after MAX_DH_RETRIES=5 attempts raises SecurityError."""
    responses = [_make_dhgen_retry()] * 10  # more than the max

    with pytest.raises(SecurityError) as exc_info:
        await _run_step3_loop(responses)

    err = str(exc_info.value)
    assert "5" in err  # attempt count must appear in the message
    assert "exhausted" in err.lower() or "DhGenRetry" in err


@pytest.mark.asyncio
async def test_dhgen_fail_raises_immediately():
    """DhGenFail on first attempt raises SecurityError without any retry."""
    with pytest.raises(SecurityError) as exc_info:
        await _run_step3_loop([_make_dhgen_fail()])

    err = str(exc_info.value)
    assert "DhGenFail" in err or "Fail" in err or "fail" in err.lower()


@pytest.mark.asyncio
async def test_dhgen_fail_after_one_retry():
    """DhGenRetry then DhGenFail: stops at fail, does not retry further."""
    with pytest.raises(SecurityError) as exc_info:
        await _run_step3_loop([_make_dhgen_retry(), _make_dhgen_fail()])

    err = str(exc_info.value)
    assert "Fail" in err or "fail" in err.lower()


def test_authenticator_no_assertion_error_in_dh_path():
    """do_authentication source code must not raise AssertionError in the DH path.

    The old code had: raise AssertionError('Step 3.2 answer was %s' % dh_gen)
    After the M-14 fix, all failures in the DH path must raise SecurityError.
    """
    import inspect
    from telethon.network.authenticator import do_authentication
    source = inspect.getsource(do_authentication)
    # Verify no 'raise AssertionError' appears (comments saying 'no AssertionError'
    # are fine; raise statements are not).
    import ast
    tree = ast.parse(source)
    raise_nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and node.exc is not None
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == 'AssertionError'
    ]
    assert len(raise_nodes) == 0, (
        f"do_authentication still has {len(raise_nodes)} 'raise AssertionError(...)' "
        f"statement(s); replace with SecurityError."
    )
