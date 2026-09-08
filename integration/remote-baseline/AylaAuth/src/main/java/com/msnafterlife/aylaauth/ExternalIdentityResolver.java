package com.msnafterlife.aylaauth;

import com.velocitypowered.api.util.GameProfile;
import java.util.concurrent.CompletableFuture;

interface ExternalIdentityResolver {
    ExternalIdentity resolve(GameProfile original);

    default CompletableFuture<ExternalIdentity> resolveAsync(GameProfile original) {
        return CompletableFuture.completedFuture(resolve(original));
    }
}
