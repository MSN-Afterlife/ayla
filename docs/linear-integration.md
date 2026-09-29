# Integração Linear V2

## Arquitetura

O context menu `📋 Criar tarefa` continua sendo a única entrada. Discord coleta a mensagem e mantém RBAC/UX; `TaskAIService` faz a triagem; `TaskDraft` em `bot/services/task_provider.py` é o modelo neutro; `LinearService` ou `ClickUpService` é escolhido pelo provider configurado. A origem Discord é contexto (`source_content`, URL e metadados), nunca evidência temática.

## TaskDraft

Além de título, descrição, prioridade, tipo, área e ambiente, o draft pode carregar status, estimate, assignee, due date, cycle, milestone, parent, subtasks, confiança, risco, destino/manual triage e metadados de origem. O ClickUp ignora os campos que não possui. O Linear só envia propriedades avançadas quando a feature flag correspondente está habilitada.

Campos possuem confiança independente em `field_confidence`. `manual_triage` indica que ainda há aspectos para decisão humana; não apaga campos independentes com evidência alta ou média. Campos sem confiança suficiente ficam unset.

## Routing e taxonomia

Team e Project são dimensões diferentes. A rota atual é `Software/Ayla`, `Software/Site`, `Operations/Minecraft Server`, `Operations/Discord Server` e `Operations/Infraestrutura`, mas project só é selecionado por área/conteúdo explícito. `manual_triage` ou confiança baixa deixa project, assignee, cycle e milestone sem valor. Texto enriquecido com canal, servidor, autor ou URL do Discord não é analisado para routing. Tokens são delimitados; não há busca ingênua por substring.

Labels são descobertas por metadata e aplicadas por grupo `Type`, `Area` e `Environment`. `infrastructure` roteia para o componente `Infraestrutura`, mas não é convertido automaticamente em label `DevOps`; `DevOps` só é usado quando essa é a área explícita. Label ausente é omitida e registrada como aviso, sem impedir uma issue básica. `production-change` nunca é automática.

Priority usa a escala nativa do Linear: urgent=1, high=2, normal=3, low=4 e ausência=0. Risk não aumenta priority sozinho. Status é resolvido pelo nome no Team; manual triage prefere `Backlog` se existir, com fallback determinístico para `Todo`.

Para prioridade, produção indisponível, segurança ativa, perda/corrupção de dados ou bloqueio operacional imediato podem justificar `urgent`. Erros 500 em staging, mesmo com prazo próximo, ficam no máximo em `high` pela regra determinística. Datas relativas são interpretadas com o timestamp da mensagem, timezone `America/Sao_Paulo` e contexto explícito enviado ao prompt; datas ambíguas ficam unset.

## Metadata e propriedades nativas

Teams, projects, labels, workflow states e milestones são descobertos e cacheados por `LINEAR_CATALOG_CACHE_SECONDS`; `invalidate_metadata()` permite refresh explícito. Members/users e cycles são consultados somente quando o recurso está habilitado. IDs nunca são configurados no código ou `.env`. Estimate só é enviado quando a metadata fornece uma escala permitida. Assignee exige correspondência exata de id, nome, displayName ou email. Due date exige uma data ISO já validada pela triagem. Cycle e milestone exigem correspondência exata; milestone também precisa pertencer ao project selecionado.

## Sub-issues

Com `LINEAR_CREATE_SUBISSUES=true`, a parent é criada primeiro e cada subtarefa é criada com `parentId`, limitada por `LINEAR_MAX_SUBISSUES` (máximo absoluto 10). A descrição não duplica a lista quando esse modo está ativo. Falhas de children não removem a parent: o resultado contém `subissues` e `subissue_failures`. A operação não faz retry automático nem garante idempotência entre invocações separadas; um retry externo deve ser controlado para não duplicar children.

## Configuração

```dotenv
LINEAR_ENABLED=false
LINEAR_API_KEY=
LINEAR_TIMEOUT_SECONDS=15
LINEAR_CATALOG_CACHE_SECONDS=600
LINEAR_CREATE_SUBISSUES=false
LINEAR_MAX_SUBISSUES=5
LINEAR_USE_ESTIMATES=false
LINEAR_USE_ASSIGNEE=false
LINEAR_USE_DUE_DATE=false
LINEAR_USE_CYCLES=false
LINEAR_USE_MILESTONES=false
TASK_PROVIDER=clickup
```

Gere uma Personal API key em Linear em Settings → API → Personal API keys e configure-a como secret no ambiente correto. Nunca registre ou cole a chave em código, testes, logs ou mensagens. O smoke test deve usar somente staging, depois do próximo deploy de staging; esta implementação não faz deploy.

## Limitações e decisões abertas

Estimates dependem da escala exposta pela metadata real do Team; sem essa informação são omitidos. O schema atual não fornece uma forma segura de deduplicar uma parent/child criada em uma execução anterior sem uma chave de origem persistida, por isso não há retry automático. Projects existentes ainda funcionam como componentes/produtos; não foi feita migração destrutiva para separar esses conceitos de iniciativas temporárias.

## Auditoria e segurança

Arquivos principais: `bot/commands/clickup.py`, `bot/services/task_ai_service.py`, `bot/services/task_provider.py`, `bot/services/linear_service.py`, `bot/services/clickup_service.py`, `bot/config.py`, `.env.example` e os testes correspondentes. GraphQL usa variables, timeout e tratamento isolado de autenticação, rate limit, timeout, JSON inválido e GraphQL errors. O token não é incluído em payloads de teste, logs ou mensagens de erro.
