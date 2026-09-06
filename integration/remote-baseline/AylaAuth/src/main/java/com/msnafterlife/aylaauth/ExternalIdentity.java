package com.msnafterlife.aylaauth;

import java.util.UUID;

record ExternalIdentity(AccountPlatform platform, String externalId, String username, UUID originalProfileUuid) {
    static ExternalIdentity java(UUID uuid, String username) {
        return new ExternalIdentity(AccountPlatform.JAVA,
            uuid.toString().toLowerCase(java.util.Locale.ROOT), username, uuid);
    }
}
