package com.msnafterlife.aylaauth;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.Duration;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

class MigrationLockClientTest {
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) server.stop(0);
    }

    @Test
    void normalUnlockedLoginIsAllowed() throws Exception {
        var client = client(false);
        assertFalse(client.isLocked(javaIdentity(), canonical()).join());
    }

    @Test
    void lockedJavaLoginIsDeniedAndCachedAcrossBackendOutage() throws Exception {
        var cache = Files.createTempFile("aylaauth-lock", ".json");
        var client = client(true, cache);
        assertTrue(client.isLocked(javaIdentity(), canonical()).join());
        server.stop(0);
        server = null;
        var down = new MigrationLockClient("http://127.0.0.1:9", "token", 250, cache, java.net.http.HttpClient.newHttpClient());
        assertTrue(down.isLocked(javaIdentity(), canonical()).join());
    }

    @Test
    void backendDownForNonLockedIdentityDoesNotCreateGlobalMigrationDeny() throws Exception {
        var cache = Files.createTempFile("aylaauth-lock", ".json");
        var down = new MigrationLockClient("http://127.0.0.1:9", "token", 250, cache, java.net.http.HttpClient.newHttpClient());
        assertFalse(down.isLocked(javaIdentity(), canonical()).join());
    }

    @Test
    void lockedThenUnlockedInvalidatesCache() throws Exception {
        var cache = Files.createTempFile("aylaauth-lock", ".json");
        var client = client(true, cache);
        assertTrue(client.isLocked(javaIdentity(), canonical()).join());
        server.stop(0);
        client = client(false, cache);
        assertFalse(client.isLocked(javaIdentity(), canonical()).join());
    }

    @Test
    void bedrockIdentityUsesCanonicalLockDecision() throws Exception {
        var bedrock = new ExternalIdentity(AccountPlatform.BEDROCK, "2533274791234567", "BedrockOne",
            UUID.fromString("00000000-0000-0000-0009-01f659c7dc87"));
        assertTrue(client(true).isLocked(bedrock, canonical()).join());
    }

    private MigrationLockClient client(boolean locked) throws IOException {
        return client(locked, Files.createTempFile("aylaauth-lock", ".json"));
    }

    private MigrationLockClient client(boolean locked, java.nio.file.Path cache) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            assertEquals("Bearer token", exchange.getRequestHeaders().getFirst("Authorization"));
            respond(exchange, "{\"locked\":" + locked + "}");
        });
        server.start();
        return new MigrationLockClient(
            "http://127.0.0.1:" + server.getAddress().getPort(),
            "token",
            (int) Duration.ofSeconds(1).toMillis(),
            cache,
            java.net.http.HttpClient.newHttpClient()
        );
    }

    private static ExternalIdentity javaIdentity() {
        return ExternalIdentity.java(UUID.fromString("12345678-1234-1234-1234-123456789abc"), "PlayerOne");
    }

    private static CanonicalIdentity canonical() {
        return new CanonicalIdentity(UUID.fromString("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"), "Abroba");
    }

    private static void respond(HttpExchange exchange, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}
