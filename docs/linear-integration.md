# Integração Linear

O context menu existente `📋 Criar tarefa` mantém a coleta e a triagem da Ayla. Quando o provider global é Linear, o resultado da triagem é convertido para `TaskDraft` e enviado ao `LinearService`; o caminho ClickUp permanece o padrão e não foi removido.

## Configuração

```dotenv
LINEAR_ENABLED=false
LINEAR_API_KEY=
TASK_PROVIDER=clickup
```

Para habilitar no ambiente desejado, gere uma API key em Linear em **Settings → API → Personal API keys**, configure-a como segredo do ambiente e use `LINEAR_ENABLED=true` e `TASK_PROVIDER=linear`. A chave nunca deve entrar no repositório, logs ou mensagens do Discord.

O provider descobre e cacheia teams, projects, labels e workflow states pelo nome. Os nomes de projeto/team são roteados para a estrutura atual da Ayla; IDs são usados somente depois da descoberta pela API.

## Auditoria resumida

- Discord: `bot/commands/clickup.py`, context menu, RBAC e resposta ephemeral.
- Triagem: `bot/services/task_ai_service.py`, com normalização, prioridade determinística, limites e subtarefas.
- ClickUp: `bot/services/clickup_service.py`, catálogo, campos, tags, retries e compatibilidade Free plan.
- Configuração: `bot/config.py` e `.env.example`.
- Testes: `tests/test_clickup_triage.py` e `tests/test_linear_service.py`.

O acoplamento anterior era principalmente `destination`/catálogo ClickUp e campos customizados. O Linear não reutiliza esses campos; usa labels nativas e mantém `environment` ausente quando a evidência é insuficiente.
