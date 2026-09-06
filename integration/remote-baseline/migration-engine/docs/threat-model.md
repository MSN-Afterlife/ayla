# Threat model

Riscos tratados: overwrite de target, merge indevido de duplicatas, corrupção de banco aberto, execução concorrente, vazamento de hashes/tokens e estado parcialmente migrado. Controles: collision BLOCKED, locks O_EXCL, snapshots/checksums, redaction, transação SQL, escrita atômica e rollback coordenado. Riscos residuais: adapters dependentes de API/plugin, jogador online e relações bidirecionais; todos bloqueiam apply até revisão.
