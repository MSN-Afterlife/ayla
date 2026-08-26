# Integração Last.fm

A integração opcional usa `LastFmService`, `LastFmRepository` e `LastFmScrobbler`.
O vínculo começa em `/lastfm conectar`, usa um `state` aleatório armazenado
somente como SHA-256 e é concluído em `GET /auth/lastfm/callback`. O state expira
em 15 minutos e é consumido de forma atômica.

Configure `LASTFM_API_KEY`, `LASTFM_API_SECRET`, `LASTFM_CALLBACK_URL` e
`LASTFM_ENABLED=true`. Use a callback do ambiente correto:

- Produção: `https://api.msnafterlife.online/auth/lastfm/callback`
- Staging: `https://api.luciddreams.fun/auth/lastfm/callback`

O scrobble é feito para quem pediu a faixa e para os demais membros humanos
presentes no canal de voz quando ela começa (cada conta é processada
individualmente). A faixa precisa ter duração confiável acima de 30 segundos e ser
reproduzida por `min(duração / 2, 240s)`; pausas não contam. Livestreams e
metadados sem artista/título confiáveis são ignorados.

Administradores podem usar `/lastfm diagnostico [usuario]` para consultar a
última faixa registrada para um membro, o tempo considerado e o resultado (ou
o motivo da falha). O histórico é mantido em memória e é perdido ao reiniciar o bot.

Como o projeto não tinha mecanismo de criptografia, a session key fica isolada
em `LASTFM_DATABASE_PATH` e nunca vai para os logs. Proteja o arquivo por
permissões/volume secreto no ambiente de produção; não foi criada criptografia
caseira. A migration de referência é `migrations/001_lastfm.sql`.

Para testar: configure as variáveis, aplique/valide as tabelas iniciando o bot,
registre a callback no painel do Last.fm, vincule com `/lastfm conectar` e
reproduza uma faixa com duração conhecida. Para rollback, pare o bot, remova as
tabelas `lastfm_accounts` e `lastfm_auth_states` do banco configurado e remova
os arquivos da integração.
