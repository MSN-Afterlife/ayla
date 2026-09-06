package com.msnafterlife.aylaauth;

import com.velocitypowered.api.util.GameProfile;

import java.util.concurrent.CompletableFuture;

/** Classifies the connection using Velocity's authentication mode before any
 * username-based Floodgate lookup can run. */
final class ConnectionIdentityClassifier {
    private ConnectionIdentityClassifier() {}

    static CompletableFuture<ExternalIdentity> resolve(GameProfile original,
                                                        boolean onlineMode,
                                                        ExternalIdentityResolver resolver) {
        if (onlineMode) {
            return CompletableFuture.completedFuture(
                ExternalIdentity.java(original.getId(), original.getName()));
        }
        return resolver.resolveAsync(original).thenApply(identity -> {
            if (identity.platform() != AccountPlatform.BEDROCK) {
                throw new ApiException("Offline profile is not a Floodgate player");
            }
            return identity;
        });
    }
}
