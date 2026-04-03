import ast
import inspect
import textwrap
from telethon.network.mtprotoplainsender import MTProtoPlainSender

def test_plain_sender_send_has_no_assert():
    source = textwrap.dedent(inspect.getsource(MTProtoPlainSender.send))
    tree = ast.parse(source)
    asserts = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert len(asserts) == 0, (
        f"MTProtoPlainSender.send still uses {len(asserts)} assert statement(s)."
    )
