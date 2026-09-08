# Maintenance window futura

1. Confirmar Paper/Crafty corretos e jogador offline; bloquear login daquele jogador.
2. Flush/safe-stop apenas se aprovado pela operação; não é executado automaticamente.
3. Snapshot de todos os arquivos e bancos com checksums.
4. Export lógico via API LuckPerms e snapshots SQLite; validar integridade.
5. Aplicar adapters, verificar contagens/ownership/UUIDs e registrar manifest.
6. Em qualquer falha, rollback pelo snapshot e verify; só então liberar login.

SimpleLogin, LuckPerms e MarriageMaster exigem esta janela. Nunca copiar MVStore/SQLite ativo cegamente.
