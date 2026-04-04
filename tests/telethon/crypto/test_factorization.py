from telethon.crypto.factorization import Factorization


def test_factorize_known_pq():
    """L-1: Factorization must work after switching to secrets module."""
    pq = 0x17ED48941A08F981
    p, q = Factorization.factorize(pq)
    assert p * q == pq
    assert 1 < p < pq
    assert 1 < q < pq


def test_factorize_does_not_use_random_module():
    """L-1: Must use secrets, not random."""
    import inspect
    source = inspect.getsource(Factorization)
    assert 'randint' not in source
