# Ayla VS Code Integration Report

Date: 2026-09-05  
Checkout: `develop` / `db3697c08607eb5c3b3e61b7447c82463c1032f6`  
Remote: `origin/develop`

## 1. Workspace audit

The workspace contains one Git checkout: `C:\Users\Abroba\ayla\ayla`.
It is the Ayla Discord bot, with `bot/`, `site-api/`, `migrations/`, `deploy/`,
and `tests/`. The parent directory contains no separate Git checkout.

The checkout contains Minecraft identity linking (Java/Bedrock) and Bingo,
but no AylaAuth project, Velocity plugin, migration engine, migration client,
gateway-lock implementation, presence authority, or Gradle project. The
available remote branches do not add those components; the migration-related
SQL files are only the three existing Ayla database migrations.

## 1.1 VPS discovery (read-only)

SSH alias `positivo` resolves to `192.168.0.128` as user `maycon`.
The discovered Ayla source is `/home/maycon/bots/meu-bot/Bot-teste`, a Git
checkout on `develop` at `fc9ccfc` with origin
`ssh://git@192.168.0.128:2222/Mounk/Bot-teste`.
The staging container is `meu-bot-staging`, currently stopped with exit code
137, and its only host bind is
`/home/maycon/bots/meu-bot/runtime-staging/data`.

The VPS scan found no source or artifact for Migration Engine, AylaAuth,
Velocity, Paper, `velocity-staging`, gateway lock, or presence authority in
`/opt`, `/home/maycon`, Docker containers/images, listening services, or the
available local Gitea repository search. The migration worktree at
`/home/maycon/migration-worktrees/ayla-github-sanitize-2026-08-27` is an older
Ayla bot worktree and contains no Minecraft migration integration.

No secrets, `.env` files, runtime data, build caches, or production backups
were copied to the workspace.

The staging bot logs also show temporary DNS failure resolving Discord Gateway
and stale slash-command registrations (`entrar`, `cancelar` not found). This
is an Ayla staging availability/deployment issue, separate from the absent
Minecraft integration components.

## 2. Branch and baseline

`develop` is aligned with `origin/develop` at `db3697c` (`adjustments`). The
canonical local suite is reproducible with:

```text
python -m unittest discover -s tests
Ran 55 tests ...
OK
```

The 55-test result is green. No 66-test or 68-test suite exists in this
checkout, so the claimed 66/68 divergence cannot be attributed to files here.
The 13 errors from the 68-test release cannot be reproduced or diagnosed
without that release's checkout and its dependency/fixture set.

## 3. Migration integration status

No migration engine/client contracts (`inspect`, `plan`, `status`, `execute`,
`rollback`) are present in this checkout. No public migration commands or
write flags were added in this audit. There is consequently no local code to
normalize for migration write protection or typed migration errors.

## 4. AylaAuth, Gateway Lock, and Velocity

No AylaAuth or Velocity source, Gradle build, tests, or deploy artifact is
present. Gateway lock live validation, persistent lock-cache behavior, and
Java/Bedrock runtime mapping cannot be verified from this checkout.

## 5. Presence authority

No runtime presence interface or authenticated presence endpoint exists in this
checkout. `ONLINE`, `OFFLINE_CONFIRMED`, and `UNKNOWN` cannot be implemented or
tested here without the authoritative Velocity/AylaAuth runtime component.

## 6. Paper bypass and staging

No Paper or Velocity configuration is present locally. No staging VPS command,
deployment, restart, live login test, or production action was performed in
this audit. The reported VPS state is treated as supplied context, not as
independently verified evidence.

## 7. Conclusion

`BLOCKED_FOR_MIGRATION_UX_PHASE`

Real blockers:

1. The required AylaAuth/Velocity/migration repositories or artifacts are not
   in the local workspace, available branches, or the discovered VPS paths.
2. The 66/68-test release needed to explain the reported divergence is absent.
3. The staging Minecraft runtime/services are absent from the discovered VPS;
   the only staging bot container is stopped (exit 137).
4. The source and runtime needed for Gateway Lock, presence, and Paper-bypass
   validation are unavailable.

Production was not modified, migration write/production flags were not
enabled, and no real migration was executed.
