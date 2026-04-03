import inspect
import importlib
import sys


def _load_connection_module():
    """Load the connection module directly, bypassing the broken package imports."""
    import types

    # Create stub modules to satisfy imports in the connection module's dependency chain
    for mod_name in [
        'telethon',
        'telethon.errors',
        'telethon.helpers',
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    # Stub out the specific symbols the connection module needs
    errors_mod = sys.modules['telethon.errors']
    if not hasattr(errors_mod, 'InvalidChecksumError'):
        errors_mod.InvalidChecksumError = type('InvalidChecksumError', (Exception,), {})
    if not hasattr(errors_mod, 'InvalidBufferError'):
        errors_mod.InvalidBufferError = type('InvalidBufferError', (Exception,), {})

    helpers_mod = sys.modules['telethon.helpers']
    if not hasattr(helpers_mod, 'get_running_loop'):
        import asyncio
        helpers_mod.get_running_loop = asyncio.get_event_loop

    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        'telethon.network.connection.connection',
        str(pathlib.Path(__file__).parent.parent.parent.parent /
            'telethon' / 'network' / 'connection' / 'connection.py')
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_conn_mod = _load_connection_module()
Connection = _conn_mod.Connection


def test_no_adh_cipher():
    source = inspect.getsource(Connection._wrap_socket_ssl)
    assert 'ADH' not in source, "_wrap_socket_ssl must not use ADH ciphers"


def test_no_protocol_sslv23():
    source = inspect.getsource(Connection._wrap_socket_ssl)
    assert 'PROTOCOL_SSLv23' not in source, "_wrap_socket_ssl must not use deprecated PROTOCOL_SSLv23"
