# Migration Engine (Fase 5B)

Engine Python para migração individual e auditável de UUID legado para identidade canônica. O modo padrão é somente leitura: `prepare`, `dry-run` e `status`. `snapshot`, `apply` e `rollback` recusam-se sem `--allow-write` e continuam bloqueados nesta versão enquanto adapters críticos não forem transacionais.

## Uso

```bash
./bin/migrationctl prepare --legacy-uuid UUID --canonical-uuid UUID --canonical-name Nome
./bin/migrationctl dry-run --run-id RUN_ID
./bin/migrationctl status
```

Cada execução cria `runs/<run_id>/manifest.json` e `dry-run.json`; o histórico append-only fica em `state/history.log`. Nenhum comando de dry-run escreve no servidor Minecraft.

## Política

Colisão source+target é bloqueada; não há overwrite/merge. UUID canônico deve ser fornecido explicitamente. Duplicatas da auditoria permanecem bloqueadas. Apply futuro exigirá preconditions (servidor/jogador offline, snapshot consistente, adapters suportados) e revisão humana.

Adapters de arquivo/SQLite e coordenador transacional são implementados e testados em fixtures; a CLI de produção ainda mantém AuraSkills, EliteMobs, Quests, HuskHomes, UltimateTeams, SimplePets, Waypoints e ImageFrame como inspect-only até validação específica de cada schema. SimpleLogin, LuckPerms e MarriageMaster permanecem bloqueados até API/transação e janela de manutenção.

`lib/adapters.py` contém implementações transacionais exercitadas apenas em fixtures. `docs/phase-5c.md`, `docs/maintenance-window.md` e `docs/duplicate-resolution.md` descrevem o coordenador, janela de manutenção e revisão de duplicatas. Nenhum adapter de fixture é conectado ao caminho de produção pela CLI.
