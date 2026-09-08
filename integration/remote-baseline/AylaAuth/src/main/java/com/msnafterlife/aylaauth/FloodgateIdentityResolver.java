package com.msnafterlife.aylaauth;

import com.velocitypowered.api.util.GameProfile;
import org.geysermc.floodgate.api.FloodgateApi;
import org.geysermc.floodgate.api.player.FloodgatePlayer;

import java.math.BigInteger;
import java.util.concurrent.CompletableFuture;

final class FloodgateIdentityResolver implements ExternalIdentityResolver {
    private static final BigInteger MAX_UNSIGNED_LONG = new BigInteger("18446744073709551615");
    private final FloodgateApi floodgateApi;

    FloodgateIdentityResolver(FloodgateApi floodgateApi) {
        if (floodgateApi == null) throw new ApiException("Floodgate API is unavailable");
        this.floodgateApi = floodgateApi;
    }

    @Override
    public ExternalIdentity resolve(GameProfile original) {
        FloodgatePlayer player = findPlayer(original);
        if (player == null) {
            return ExternalIdentity.java(original.getId(), original.getName());
        }
        return identityFrom(player, original);
    }

    @Override
    public CompletableFuture<ExternalIdentity> resolveAsync(GameProfile original) {
        FloodgatePlayer player = findPlayer(original);
        if (player != null) return CompletableFuture.completedFuture(identityFrom(player, original));
        String gamertag = gamertagForLookup(original);
        if (gamertag == null) return CompletableFuture.completedFuture(ExternalIdentity.java(original.getId(), original.getName()));
        return floodgateApi.getXuidFor(gamertag).thenApply(xuid ->
            new ExternalIdentity(AccountPlatform.BEDROCK,
                normalizeXuid(xuid == null ? null : Long.toUnsignedString(xuid)),
                gamertag, original.getId()));
    }

    private ExternalIdentity identityFrom(FloodgatePlayer player, GameProfile original) {
        String xuid = normalizeXuid(player.getXuid());
        String username = player.getUsername();
        if (username == null || username.isBlank()) throw new ApiException("Floodgate player has no Bedrock username");
        return new ExternalIdentity(AccountPlatform.BEDROCK, xuid, username, original.getId());
    }

    private String gamertagForLookup(GameProfile original) {
        String name = original.getName();
        String prefix = floodgateApi.getPlayerPrefix();
        if (name == null || name.isBlank()) return null;
        if (prefix != null && !prefix.isEmpty() && name.startsWith(prefix)) name = name.substring(prefix.length());
        return name.isBlank() ? null : name;
    }

    private FloodgatePlayer findPlayer(GameProfile original) {
        if (floodgateApi.isFloodgatePlayer(original.getId())) {
            FloodgatePlayer player = floodgateApi.getPlayer(original.getId());
            if (player == null) throw new ApiException("Floodgate marked player is missing from its API");
            return player;
        }
        // Geyser's proxy integration may expose an offline Velocity profile whose
        // UUID is not the Floodgate UUID yet. The Floodgate registry is the
        // authoritative source; match its public profile names, never a prefix
        // or username format by itself.
        for (FloodgatePlayer player : floodgateApi.getPlayers()) {
            if (same(original.getName(), player.getCorrectUsername())
                || same(original.getName(), player.getJavaUsername())) {
                return player;
            }
        }
        return null;
    }

    private static boolean same(String left, String right) {
        return left != null && right != null && left.equals(right);
    }

    static String normalizeXuid(String value) {
        if (value == null || !value.matches("[0-9]{1,20}")) throw new ApiException("Invalid Bedrock XUID");
        BigInteger parsed = new BigInteger(value);
        if (parsed.signum() <= 0 || parsed.compareTo(MAX_UNSIGNED_LONG) > 0) {
            throw new ApiException("Invalid Bedrock XUID");
        }
        return parsed.toString();
    }
}
