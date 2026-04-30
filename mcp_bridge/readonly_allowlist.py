"""
Read-only mode allow-list of permitted MTProto TL request classes.

Names are stored as ``module.ClassName`` (e.g. ``messages.GetHistoryRequest``)
where ``module`` is the sub-module under ``telethon.tl.functions``. Anything
not in this set is blocked by ``readonly_guard``.

Curated, fail-closed. To add a new read RPC, add the fully qualified name
here and add a test in test_readonly_allowlist.py if it requires special
handling.
"""
from __future__ import annotations

import importlib
from typing import Type

READONLY_ALLOWLIST: frozenset[str] = frozenset({
    # Auth / login (login allowed; AcceptLoginToken, LogOut, ExportLoginToken NOT included)
    "auth.SendCodeRequest",
    "auth.ResendCodeRequest",
    "auth.SignInRequest",
    "auth.SignUpRequest",
    "auth.CheckPasswordRequest",
    "auth.ImportLoginTokenRequest",
    "auth.ExportAuthorizationRequest",
    "auth.ImportAuthorizationRequest",
    "account.GetPasswordRequest",

    # Bootstrap / housekeeping (bare names = top-level telethon.tl.functions)
    "PingRequest",
    "PingDelayDisconnectRequest",
    "help.GetConfigRequest",
    "help.GetNearestDcRequest",
    "updates.GetStateRequest",
    "updates.GetDifferenceRequest",
    "updates.GetChannelDifferenceRequest",

    # Reading messages and chats
    "messages.GetHistoryRequest",
    "messages.GetMessagesRequest",
    "messages.SearchRequest",
    "messages.SearchGlobalRequest",
    "messages.GetDialogsRequest",
    "messages.GetPeerDialogsRequest",
    "messages.GetStickerSetRequest",
    "channels.GetMessagesRequest",
    "channels.GetFullChannelRequest",
    "channels.GetChannelsRequest",
    "channels.GetParticipantRequest",
    "channels.GetParticipantsRequest",

    # Entity resolution
    "contacts.ResolveUsernameRequest",
    "contacts.GetContactsRequest",
    "users.GetUsersRequest",
    "users.GetFullUserRequest",
    "photos.GetUserPhotosRequest",

    # Self-introspection (read-only)
    "account.GetAuthorizationsRequest",

    # Downloads
    "upload.GetFileRequest",
    "upload.GetCdnFileRequest",
    "upload.ReuploadCdnFileRequest",
    "upload.GetCdnFileHashesRequest",
    "upload.GetWebFileRequest",
})


def resolve_class(fq_name: str) -> Type:
    """Resolve ``module.ClassName`` (or bare ``ClassName``) to the actual class
    under ``telethon.tl.functions``.

    If ``fq_name`` contains no dot, the class is looked up directly in
    ``telethon.tl.functions`` (e.g. ``"PingRequest"``).  Otherwise the dotted
    prefix is treated as a sub-module (e.g. ``"messages.GetHistoryRequest"``).

    Raises ImportError or AttributeError if the class doesn't exist.
    """
    if "." not in fq_name:
        module = importlib.import_module("telethon.tl.functions")
        return getattr(module, fq_name)
    module_part, class_name = fq_name.rsplit(".", 1)
    module = importlib.import_module(f"telethon.tl.functions.{module_part}")
    return getattr(module, class_name)


# Pre-computed set of bare class names (last component) for the hot path in
# readonly_guard. Comparing ``type(req).__name__`` against strings is cheaper
# than reflecting back to fully qualified names on every RPC.
ALLOWED_CLASS_NAMES: frozenset[str] = frozenset(
    name.rsplit(".", 1)[-1] for name in READONLY_ALLOWLIST
)
