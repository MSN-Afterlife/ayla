package com.msnafterlife.aylaauth;

import com.velocitypowered.api.util.GameProfile;
import org.geysermc.floodgate.api.FloodgateApi;
import org.geysermc.floodgate.api.player.FloodgatePlayer;
import org.junit.jupiter.api.Test;

import java.util.UUID;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class FloodgateIdentityResolverTest {
    private static final UUID FLOODGATE_UUID = UUID.fromString("00000000-0000-0000-0009-01f659c7dc87");
    private static final UUID JAVA_UUID = UUID.fromString("12345678-1234-4234-8234-123456789abc");

    @Test
    void detectsJavaAndUsesMojangUuid() {
        FloodgateApi api = mock(FloodgateApi.class);
        when(api.isFloodgatePlayer(JAVA_UUID)).thenReturn(false);
        var identity = new FloodgateIdentityResolver(api).resolve(profile(JAVA_UUID, "Mounkass"));
        assertEquals(AccountPlatform.JAVA, identity.platform());
        assertEquals(JAVA_UUID.toString(), identity.externalId());
        verify(api, never()).getPlayer(any());
    }

    @Test
    void detectsBedrockAndUsesRealXuidNotFloodgateUuid() {
        FloodgateApi api = mock(FloodgateApi.class);
        FloodgatePlayer player = mock(FloodgatePlayer.class);
        when(api.isFloodgatePlayer(FLOODGATE_UUID)).thenReturn(true);
        when(api.getPlayer(FLOODGATE_UUID)).thenReturn(player);
        when(player.getXuid()).thenReturn("2533274791234567");
        when(player.getUsername()).thenReturn("TesteBedrock");
        var identity = new FloodgateIdentityResolver(api).resolve(profile(FLOODGATE_UUID, ".TesteBedrock"));
        assertEquals(AccountPlatform.BEDROCK, identity.platform());
        assertEquals("2533274791234567", identity.externalId());
        assertNotEquals(FLOODGATE_UUID.toString(), identity.externalId());
        assertEquals("TesteBedrock", identity.username());
    }

    @Test
    void detectsOfflineVelocityProfileFromFloodgateRegistryWhenUuidDiffers() {
        FloodgateApi api = mock(FloodgateApi.class);
        FloodgatePlayer player = mock(FloodgatePlayer.class);
        when(api.isFloodgatePlayer(JAVA_UUID)).thenReturn(false);
        when(player.getCorrectUsername()).thenReturn(".Mounkass");
        when(player.getJavaUsername()).thenReturn("Mounkass");
        when(player.getXuid()).thenReturn("2533274791234567");
        when(player.getUsername()).thenReturn("Mounkass");
        when(api.getPlayers()).thenReturn(List.of(player));

        var identity = new FloodgateIdentityResolver(api).resolve(profile(JAVA_UUID, ".Mounkass"));

        assertEquals(AccountPlatform.BEDROCK, identity.platform());
        assertEquals("2533274791234567", identity.externalId());
        verify(api, never()).getPlayer(JAVA_UUID);
    }

    @Test
    void resolvesXuidAsynchronouslyWhenRegistryIsNotReadyAtProfileEvent() {
        FloodgateApi api = mock(FloodgateApi.class);
        when(api.isFloodgatePlayer(JAVA_UUID)).thenReturn(false);
        when(api.getPlayers()).thenReturn(List.of());
        when(api.getPlayerPrefix()).thenReturn(".");
        when(api.getXuidFor("Mounkass"))
            .thenReturn(java.util.concurrent.CompletableFuture.completedFuture(2533274791234567L));

        var identity = new FloodgateIdentityResolver(api)
            .resolveAsync(profile(JAVA_UUID, ".Mounkass")).join();

        assertEquals(AccountPlatform.BEDROCK, identity.platform());
        assertEquals("2533274791234567", identity.externalId());
        assertEquals("Mounkass", identity.username());
    }

    @Test
    void onlineJavaNeverInvokesFloodgateXuidResolution() {
        FloodgateApi api = mock(FloodgateApi.class);
        when(api.getPlayers()).thenReturn(List.of());
        when(api.getPlayerPrefix()).thenReturn(".");
        when(api.getXuidFor(anyString())).thenThrow(new AssertionError("Java must not query Floodgate XUID"));
        var resolver = new FloodgateIdentityResolver(api);

        var identity = ConnectionIdentityClassifier.resolve(profile(JAVA_UUID, "Mounkass"), true, resolver).join();

        assertEquals(AccountPlatform.JAVA, identity.platform());
        assertEquals(JAVA_UUID.toString(), identity.externalId());
        verify(api, never()).getXuidFor(anyString());
    }

    @Test
    void markedPlayerMissingFromFloodgateFailsClosed() {
        FloodgateApi api = mock(FloodgateApi.class);
        when(api.isFloodgatePlayer(FLOODGATE_UUID)).thenReturn(true);
        when(api.getPlayer(FLOODGATE_UUID)).thenReturn(null);
        assertThrows(ApiException.class,
            () -> new FloodgateIdentityResolver(api).resolve(profile(FLOODGATE_UUID, ".Missing")));
    }

    @Test
    void missingOrInvalidXuidFailsClosed() {
        for (String bad : new String[]{null, "", "abc", "-1", "0", "18446744073709551616"}) {
            assertThrows(ApiException.class, () -> FloodgateIdentityResolver.normalizeXuid(bad));
        }
    }

    private static GameProfile profile(UUID uuid, String name) {
        return new GameProfile(uuid, name, List.of());
    }
}
