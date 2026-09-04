# Identidade Minecraft — Fase 1

A Ayla usa Python, `discord.py`, aiohttp e SQLite direto em
`LEVELS_DATABASE_PATH`; não havia ORM ou migrations. Esta fase adiciona a
migration `migrations/001_minecraft_identity.sql`, aplicada no startup e
registrada em `schema_migrations`, usando a mesma base persistida do bot.

## API interna

Os endpoints ficam na API aiohttp do bot, na porta já configurada por
`SITE_API_PORT`, e exigem `Authorization: Bearer <AYLA_MINECRAFT_INTERNAL_TOKEN>`:

```http
POST /internal/minecraft/link/request
Content-Type: application/json

{"platform":"java","external_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","username":"PlayerTeste"}
```

Nova conta: `{"linked":false,"code":"K7F2Q9","expires_in":600}`.
Conta existente retorna `linked=true` e a identidade. O lookup
`GET /internal/minecraft/account/{platform}/{external_id}` retorna
`{"linked":false}` ou `linked`, `enabled`, `identity` e `minecraft_account`.
As plataformas aceitas são somente `java` e `bedrock`. A rota Java antiga
`/internal/minecraft/account/java/{external_id}` continua disponível.

O token usa comparação constante, nunca é logado e não deve ser commitado.

## Fluxo e política

`a!minecraft link CODIGO` (e `/minecraft link`) consome o código numa transação
SQLite. `a!minecraft status` também está disponível. Códigos têm seis
caracteres sem `O/0` e `I/1`, expiram em dez minutos, são single-use e têm
limites por conta/IP. Conflitos de conta ou Discord são bloqueados.

Java usa UUID Mojang normalizado. Bedrock usa XUID decimal positivo, validado
como unsigned 64-bit e persistido em decimal canônico; não é convertido para
UUID. Uma identidade pode ter no máximo uma conta de cada plataforma.

`canonical_uuid` é UUID aleatório persistido. `canonical_name` é derivado do
display name, mantém apenas caracteres Minecraft-safe até 16 posições e não
translitera caracteres incompatíveis; colisões usam sufixo determinístico.
`discord_name_original` é preservado. Mudanças futuras no Discord não alteram
automaticamente UUID ou nome canônico.

Ainda não há plugin Velocity, substituição de UUID/nickname, autorização no
login, integração Geyser/Floodgate, unlink ou migração de playerdata.
