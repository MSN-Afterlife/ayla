# Arquitetura

`migrationctl` cria um manifesto imutável por execução, adquire locks por UUID e separa prepare/dry-run de qualquer mutação. O coordinator de fixtures executa adapters em ordem determinística, registra mudanças e reverte adapters já aplicados em caso de falha. Identidades vêm de Ayla/manifest explícito; nomes nunca são autoridade.

No ambiente real, o caminho de escrita permanece desligado. Bancos abertos exigem janela de manutenção, snapshot consistente e verificação pós-apply.
