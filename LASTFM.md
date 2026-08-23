# Integração Last.fm

A integração opcional usa `LastFmService`, `LastFmRepository` e `LastFmScrobbler`.
O vínculo começa em `/lastfm conectar`, usa um `state` aleatório armazenado
somente como SHA-256 e é concluído em `GET /auth/lastfm/callback`. O state expira
em 15 minutos e é consumido de forma atômica.

Configure `LASTFM_API_KEY`, `LASTFM_API_SECRET`, `LASTFM_CALLBACK_URL` e
`LASTFM_ENABLED=true`. Use a callback do ambiente correto:

- Produção: `https://api.msnafterlife.online/auth/lastfm/callback`
- Staging: `https://api.luciddreams.fun/auth/lastfm/callback`

O modo inicial é `requested`: somente quem pediu a faixa recebe Now Playing e
scrobble. A faixa precisa ter duração confiável acima de 30 segundos e ser
reproduzida por `min(duração / 2, 240s)`; pausas não contam. Livestreams e
metadados sem artista/título confiáveis são ignorados.

Como o projeto não tinha mecanismo de criptografia, a session key fica isolada
em `LASTFM_DATABASE_PATH` e nunca vai para os logs. Proteja o arquivo por
permissões/volume secreto no ambiente de produção; não foi criada criptografia
caseira. A migration de referência é `migrations/001_lastfm.sql`.

Para testar: configure as variáveis, aplique/valide as tabelas iniciando o bot,
registre a callback no painel do Last.fm, vincule com `/lastfm conectar` e
reproduza uma faixa com duração conhecida. Para rollback, pare o bot, remova as
tabelas `lastfm_accounts` e `lastfm_auth_states` do banco configurado e remova
os arquivos da integração.
