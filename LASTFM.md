# Integração Last.fm

A integração opcional usa `LastFmService`, `LastFmRepository` e `LastFmScrobbler`.
O vínculo começa em `/lastfm conectar`, usa um `state` aleatório armazenado
somente como SHA-256 e é concluído em `GET /auth/lastfm/callback`. O state expira
em 15 minutos e é consumido de forma atômica.

Configure `LASTFM_API_KEY`, `LASTFM_API_SECRET`, `LASTFM_CALLBACK_URL`,
`LASTFM_ENCRYPTION_KEY` e `LASTFM_ENABLED=true`. A chave de criptografia deve
ser uma chave aleatória de 32 bytes codificada em Base64 URL-safe e armazenada
no gerenciador de segredos do host, separada do SQLite. Nunca a inclua em uma
imagem, neste repositório ou no backup do banco. Sem essa chave, a integração
permanece desativada e session keys antigas não são usadas.

Ao abrir uma base existente com a chave configurada, o repositório migra as
session keys legadas em uma transação SQLite; qualquer falha reverte a
transação. Faça e valide um backup restrito antes da primeira inicialização,
confirme que a leitura e o scrobble funcionam e então remova com segurança a
cópia legada em texto puro.

Use uma callback do ambiente correto:

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

A Ayla também publica um anúncio público no formato `Started playing ... by ...`
para facilitar leitores externos de “Now playing”, como o `.fmbot`. O `.fmbot`,
porém, mantém uma lista própria de bots suportados; se ele ainda não reconhecer
a Ayla, somente a integração direta da Ayla com Last.fm será utilizada.

As session keys são cifradas com AES-256-GCM e vinculadas ao ID Discord como
dados autenticados. Restringa as permissões do SQLite e dos backups; mantenha
uma cópia de segurança da chave somente no cofre de segredos. Para rollback de
uma migração em falha, a transação preserva os registros existentes. Se for
necessário voltar a uma versão antiga do bot, desligue Last.fm e restaure o
backup pré-migração em volume restrito; não copie a chave para junto do backup.

Para testar: configure as variáveis, aplique/valide as tabelas iniciando o bot,
registre a callback no painel do Last.fm, vincule com `/lastfm conectar` e
reproduza uma faixa com duração conhecida. Para rollback, pare o bot, remova as
tabelas `lastfm_accounts` e `lastfm_auth_states` do banco configurado e remova
os arquivos da integração.
