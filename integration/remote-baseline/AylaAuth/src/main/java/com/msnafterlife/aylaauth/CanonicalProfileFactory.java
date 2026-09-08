package com.msnafterlife.aylaauth;

import com.velocitypowered.api.util.GameProfile;

final class CanonicalProfileFactory {
    private CanonicalProfileFactory() {}

    static GameProfile create(GameProfile original, CanonicalIdentity identity) {
        if (identity == null) throw new ApiException("Authorized response has no canonical identity");
        return new GameProfile(identity.uuid(), identity.name(), original.getProperties());
    }
}
