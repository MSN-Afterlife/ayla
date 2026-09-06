# Fase 5C — adapters e transação

Os adapters em `lib/adapters.py` implementam planejamento, snapshot, escrita atômica, verificação e rollback para fixtures. `AtomicFileAdapter` cobre playerdata/JSON (incluindo MVI, AuraSkills e Quests quando o formato permitir); `SQLiteKeyAdapter` demonstra atualização transacional para storages como SimpleLogin, EliteMobs, HuskHomes, UltimateTeams, SimplePets e Waypoints. Nenhum desses métodos é chamado pela CLI contra produção.

LuckPerms permanece dependente da API/export oficial (H2 MVStore), MarriageMaster exige integridade bidirecional e SimpleLogin exige janela de manutenção para backup consistente. Esses itens continuam bloqueando apply real.

O coordenador executa adapters em ordem determinística, interrompe no primeiro erro e reverte os adapters já aplicados. Snapshots devem ser completos e verificados antes de qualquer escrita; banco aberto em produção requer maintenance window.
