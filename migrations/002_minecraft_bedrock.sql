CREATE UNIQUE INDEX IF NOT EXISTS minecraft_accounts_one_bedrock_per_identity
    ON minecraft_accounts(identity_id) WHERE platform = 'bedrock';
