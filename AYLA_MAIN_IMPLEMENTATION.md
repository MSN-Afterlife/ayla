# Ayla — alterações integradas na `main`

Este documento registra as alterações funcionais e técnicas que foram integradas à branch `main` durante a implementação da triagem automática de chamados, da integração com o ClickUp e dos ajustes de produção.

## Commits de referência

- `bef43a8` — integração da implementação de triagem automática na `main`.
- `93597bf` — correção da resolução da triagem manual pelo catálogo do ClickUp.

A implementação foi validada com **27 testes automatizados passando**.

## 1. Criação de tarefas pelo Discord

Foi adicionado o comando de contexto de mensagem **“Criar tarefa”**. Usuários autorizados podem selecionar uma mensagem do Discord e gerar uma tarefa no ClickUp.

O fluxo:

1. valida a permissão do usuário;
2. busca o catálogo de Workspaces, Spaces, Folders e Lists pela API do ClickUp;
3. envia a mensagem para análise da IA;
4. escolhe o destino mais adequado;
5. cria a tarefa com título, descrição, prioridade, estimativa, tags e subtarefas;
6. responde com o link da tarefa criada.

Falhas de IA não deixam o comando quebrado: o sistema usa título, descrição e prioridade de fallback e registra o erro nos logs.

## 2. Catálogo dinâmico do ClickUp

Os destinos não precisam mais ser definidos exclusivamente por conversões fixas de palavras. O serviço consulta a API para descobrir:

- Workspace autorizado;
- Spaces;
- Folders;
- Lists;
- nome, caminho completo, ID e Space de cada lista.

O catálogo é armazenado em cache pelo período definido em `CLICKUP_CATALOG_CACHE_SECONDS`.

A IA recebe os destinos descobertos, mas o código valida o resultado antes de criar a tarefa. IDs inexistentes não são enviados ao ClickUp.

## 3. Correção da triagem manual

Foi corrigido o problema em que a IA retornava um caminho como:

```text
MSN Afterlife Tasks > Backlog > Triage Manual
```

O código agora:

- aceita ID, nome ou caminho descoberto pela API;
- converte nome/caminho para o `list_id` real;
- procura automaticamente a lista configurada para triagem manual;
- aceita caminhos com o Workspace antes do trecho `Backlog > Triage Manual`;
- só usa `CLICKUP_LIST_ID` como último fallback legado.

Configuração usada:

```env
CLICKUP_MANUAL_TRIAGE_QUERY=Backlog > Triage Manual
```

Assim, tickets ambíguos ou incompatíveis com as categorias existentes podem ser enviados para o Workspace/Backlog de triagem manual, sem depender de um ID fixo no código.

## 4. Análise da IA para tickets

Foi criado um serviço dedicado de análise de tarefas. A IA gera e normaliza:

- título;
- descrição/resumo;
- categoria;
- área afetada;
- ambiente;
- prioridade;
- risco;
- confiança;
- possível causa;
- possível solução;
- critérios de aceite;
- informações ausentes;
- estimativa de tempo;
- confiança da estimativa;
- base da estimativa;
- pontos sugeridos;
- tags;
- navegador e sistema operacional;
- etapas de reprodução;
- prazo de resolução;
- subtarefas de investigação, implementação e validação.

As respostas são exigidas em JSON e passam por validação antes de serem usadas.

## 5. Prioridade e evidências

Além do resultado da IA, existem regras determinísticas para impedir classificações claramente incoerentes. Por exemplo, mensagens com termos de urgência, exposição de credenciais, indisponibilidade ou impacto grave podem elevar a prioridade automaticamente.

Também são extraídas evidências diretamente da mensagem. Exemplos:

- `testei no Chrome 128` → navegador;
- `usando Windows 11` → sistema operacional;
- menções a produção, staging ou urgência → contexto e prioridade.

Isso reduz a dependência de campos preenchidos por chute.

## 6. Compatibilidade com o ClickUp Free

Foi adicionado o modo seguro:

```env
CLICKUP_FREE_MODE=true
```

Quando ativo, a Ayla:

- cria tarefas padrão (`Task`), usando `custom_item_id=0`;
- não cria tarefas do tipo personalizado `Bug`;
- não envia Custom Fields;
- usa prioridade nativa do ClickUp;
- mantém tags, descrição Markdown, estimativa e subtarefas;
- mantém as informações que não possuem campo nativo no Markdown.

Isso evita o limite de uso de Custom Task Types do plano gratuito e impede que uma lista configurada com tipo padrão personalizado provoque o erro `ITEM_246`.

O modo pode ser desativado futuramente com `CLICKUP_FREE_MODE=false`, caso o Workspace tenha plano e cota suficientes.

## 7. Tags automáticas

As tags são geradas pela IA e complementadas com categoria, área e ambiente quando aplicável.

Se a tag ainda não existir no Space, a Ayla tenta criá-la pela API antes de associá-la à tarefa.

Falhas na criação ou associação de tags não impedem a criação da tarefa principal.

## 8. Estimativa, pontos e subtarefas

A IA pode retornar estimativa em minutos, prazo de resolução e pontos de Sprint.

O serviço converte a estimativa para milissegundos, formato esperado pela API do ClickUp.

Se o ClickUp rejeitar pontos por o ClickApp Sprint Points não estar habilitado (`ITEM_227`), a Ayla repete a criação sem os pontos.

Subtarefas são criadas como tarefas padrão. Se o ClickUp informar limite de tipos personalizados (`ITEM_246`), a criação das subtarefas restantes é interrompida sem derrubar a tarefa pai.

## 9. Campos personalizados via API

O código possui suporte para consultar Custom Fields reais da lista pela API, incluindo:

- IDs reais dos campos;
- tipos dos campos;
- opções reais de dropdown;
- IDs reais das opções;
- validação dos valores antes do envio.

Esse suporte permanece preparado para Workspaces pagos, mas fica desativado por padrão no modo Free. A IA não inventa IDs de campos ou opções.

## 10. Robustez da chamada de IA

Foram corrigidos problemas de timeout e compatibilidade com modelos recentes:

- timeout padrão de análise: `120` segundos;
- modelo de tarefas configurável por `TASK_AI_MODEL`;
- modelo padrão de tarefas: `gpt-5.4-mini`;
- uso de `max_completion_tokens`, compatível com o modelo configurado;
- retries da API do OpenAI desativados para que o próprio fluxo controle o timeout;
- tratamento para resposta vazia, JSON inválido e análise retornando `None`.

Configuração:

```env
TASK_AI_MODEL=gpt-5.4-mini
TASK_AI_TIMEOUT_SECONDS=120
```

## 11. Configurações ClickUp adicionadas

```env
CLICKUP_API_TOKEN=
CLICKUP_LIST_ID=
CLICKUP_ALLOWED_ROLE_IDS=
CLICKUP_DESTINATIONS={}
CLICKUP_CUSTOM_FIELD_IDS={}
CLICKUP_FREE_MODE=true
CLICKUP_WORKSPACE_ID=
CLICKUP_CATALOG_CACHE_SECONDS=600
CLICKUP_MANUAL_TRIAGE_QUERY=Backlog > Triage Manual
```

`CLICKUP_LIST_ID` continua disponível somente como fallback legado. O roteamento preferencial usa o catálogo obtido pela API.

## 12. Testes adicionados

Os testes cobrem:

- descoberta do catálogo;
- resolução de destinos;
- rejeição de IDs inválidos;
- conversão de caminho para ID;
- seleção automática da triagem manual;
- prioridades nativas;
- regras determinísticas de prioridade;
- extração de navegador e sistema operacional;
- validação de Custom Fields e dropdowns;
- fallback de tipo personalizado;
- modo Free sem Custom Fields;
- fallback de Sprint Points;
- criação e falha de subtarefas;
- validação de respostas inválidas da IA.

## 13. Alterações adicionais incluídas na integração

Também foram integrados ajustes de produção no cliente, eventos, ajuda, prompts e configuração geral da Ayla. O fluxo antigo de memória afetiva foi removido da implementação integrada, junto com os módulos antigos correspondentes, para manter o comportamento alinhado à versão de produção vigente.

## 14. Estado atual

- código integrado na `main`;
- correção da triagem manual implementada;
- modo ClickUp Free ativado por padrão;
- testes passando;
- Jira ainda não implementado, mas a lógica de triagem permanece separada do serviço do ClickUp para permitir um adaptador futuro;
- o arquivo `.env` continua sendo configuração local e não deve ser usado para transportar credenciais em commits.
