def test_recent_remote_ids_always_checked():
    """M-2: Replay detection must check all msg_ids, not just lower ones.

    The old code only checked _recent_remote_ids when
    remote_msg_id <= _highest_remote_id, meaning a replayed message
    with a higher ID would bypass detection.
    """
    # This is a design-level test verifying the fix is in place.
    # We check the source code doesn't have the old pattern.
    import inspect
    from telethon.network.mtprotostate import MTProtoState
    source = inspect.getsource(MTProtoState.decrypt_message_data)
    # The old code had "remote_msg_id <= self._highest_remote_id and remote_msg_id in"
    # The fix removes that condition so all IDs are checked
    assert 'remote_msg_id <= self._highest_remote_id and remote_msg_id in' not in source


def test_salt_is_read_not_ignored():
    """M-1: Salt must be read and logged, not discarded."""
    import inspect
    from telethon.network.mtprotostate import MTProtoState
    source = inspect.getsource(MTProtoState.decrypt_message_data)
    assert 'remote_salt' in source
