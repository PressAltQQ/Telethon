# Upstream Sync Procedure

This fork diverges from upstream Telethon **only by security posture**. No
intentional feature divergence is introduced. When upstream releases security
fixes or breaking changes relevant to the MTProto layer, they should be
cherry-picked into this fork.

**Upstream:** `codeberg.org/Lonami/Telethon`
**This fork:** `github.com/PressAltQQ/Telethon`, main branch `v1`

---

## When to sync

- A new upstream release contains security fixes (check upstream changelog).
- An upstream commit closes a CVE or fixes an auth/crypto/session bug.
- An upstream commit changes the TL layer (new API layer, new `.tl` schema)
  that the fork needs for compatibility.
- Quarterly health check: review the upstream `main` log since last sync date.

Do **not** cherry-pick upstream commits that:
- Add features outside the security posture scope.
- Revert security hardening already in this fork.
- Touch generated files (`telethon/tl/**`, `telethon/errors/rpcerrorlist.py`)
  without a corresponding change to `telethon_generator/data/*.tl` or
  `data/errors.csv`. Generated files must be regenerated via `python setup.py gen`.

---

## Procedure

### 1. Add the upstream remote (one-time)

```bash
git remote add upstream https://codeberg.org/Lonami/Telethon.git
git fetch upstream
```

Verify:

```bash
git remote -v
# upstream  https://codeberg.org/Lonami/Telethon.git (fetch)
# upstream  https://codeberg.org/Lonami/Telethon.git (push)
```

### 2. Fetch upstream and inspect commits since last sync

```bash
git fetch upstream
git log upstream/main --oneline --since="YYYY-MM-DD"
# Replace YYYY-MM-DD with the date of the last sync (see git log on v1)
```

Identify commits to cherry-pick. Group by concern: crypto, session,
network, TL update. Review each diff:

```bash
git show <upstream-commit-sha> --stat
git show <upstream-commit-sha>
```

Look for:
- Fixes to `telethon/crypto/`, `telethon/network/authenticator.py`,
  `telethon/sessions/`, `telethon/_updates/`.
- Changes that conflict with our hardening patches (e.g. if upstream
  reverts an assertion we promoted to `SecurityError`).

### 3. Cut a sync branch

```bash
git checkout v1
git checkout -b security/upstream-sync-$(date +%Y-%m-%d)
```

### 4. Cherry-pick the selected commits

Cherry-pick one commit at a time, in chronological order (oldest first):

```bash
git cherry-pick -x <upstream-commit-sha>
```

The `-x` flag appends `(cherry picked from commit <sha>)` to the commit
message, preserving upstream traceability.

If a conflict arises:

```bash
# Resolve conflicts manually
git add <resolved-files>
git cherry-pick --continue
```

For commits that touch generated TL files, **do not cherry-pick the
generated output directly**. Instead:
1. Cherry-pick the change to `telethon_generator/data/*.tl` or
   `data/errors.csv`.
2. Regenerate: `python setup.py gen`
3. Commit the regenerated output separately.

### 5. Tag each commit with the appropriate finding ID

Follow the fork's commit tagging convention. If the cherry-picked commit
fixes a known upstream CVE or relates to an existing finding, add the ID:

```
security: <upstream description> (upstream-sync-YYYY-MM-DD, C-# or H-# or M-#)
```

If it is a routine upstream improvement with no finding ID, use:

```
chore(upstream-sync): <upstream description> (cherry picked from <sha>)
```

### 6. Run the full test suite

```bash
gtimeout 180 .venv/bin/python -m pytest tests/ -q
```

All tests must pass before proceeding. A test failure after a cherry-pick
is a conflict signal — investigate before continuing.

Also run ruff:

```bash
.venv/bin/ruff check .
```

If the upstream commit introduced style violations (rare — upstream uses
flake8), fix them inline.

### 7. Open a PR to v1

```bash
git push -u origin security/upstream-sync-$(date +%Y-%m-%d)
gh pr create \
  --base v1 \
  --title "security: upstream sync $(date +%Y-%m-%d)" \
  --body "Cherry-picks from upstream Telethon (codeberg.org/Lonami/Telethon).

Commits included:
- <sha1>: <description>
- <sha2>: <description>

Tests: all pass (paste pytest output).
Ruff: clean.
"
```

Merge only after both PAR reviewers approve (standard governance).

### 8. Update the last-sync marker

After merging, record the date and last upstream commit SHA in a comment
on the PR or in `.superflow-state.json` under `context.upstream_last_sync`:

```json
{
  "context": {
    "upstream_last_sync": {
      "date": "YYYY-MM-DD",
      "last_sha": "<upstream-sha>"
    }
  }
}
```

---

## Conflict resolution guidance

| Scenario | Action |
|---|---|
| Upstream reverts an assertion we promoted to `SecurityError` | Keep our `SecurityError`; do not apply the upstream revert |
| Upstream changes generated TL files | Cherry-pick only the `.tl` source change; regenerate via `setup.py gen` |
| Upstream changes `telethon/crypto/aes.py` without our pyaes gate | Apply upstream logic inside our gate; preserve `TELETHON_ALLOW_PYAES` guard |
| Upstream bumps `python_requires` below our `>=3.11` | Keep our `>=3.11` |
| Genuine conflict: upstream logic is incompatible | Resolve by keeping the more security-conservative behavior; document in PR |

---

## Notes

- This fork does not track upstream's `master` branch — upstream uses `main`
  on Codeberg. Adjust remote refs accordingly.
- The `mcp_bridge/` package is not part of upstream and will never conflict.
- Generated files (`telethon/tl/**`, `telethon/errors/rpcerrorlist.py`) should
  never be edited by hand. If an upstream sync requires TL changes, always
  regenerate via `python setup.py gen`.
