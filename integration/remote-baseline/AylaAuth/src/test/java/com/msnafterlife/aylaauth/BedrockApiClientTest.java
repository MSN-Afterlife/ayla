package com.msnafterlife.aylaauth;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

class BedrockApiClientTest {
    private HttpServer server;

    @AfterEach
    void stop() {
        if (server != null) server.stop(0);
    }

    @Test
    void bedrockLookupAndLinkUsePlatformAndXuid() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            if (exchange.getRequestMethod().equals("GET")) {
                assertEquals("/internal/minecraft/account/bedrock/2533274791234567",
                    exchange.getRequestURI().getPath());
                respond(exchange, "{\"linked\":false}");
            } else {
                String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                assertTrue(body.contains("\"platform\":\"bedrock\""));
                assertTrue(body.contains("\"external_id\":\"2533274791234567\""));
                assertFalse(body.contains("00000000-0000-0000-0009-01f659c7dc87"));
                respond(exchange, "{\"linked\":false,\"code\":\"ABC123\",\"expires_in\":600}");
            }
        });
        server.start();
        var config = new AylaAuthConfig("http://127.0.0.1:" + server.getAddress().getPort(), "token", 1000, false);
        var client = new AylaApiClient(config);
        var external = new ExternalIdentity(AccountPlatform.BEDROCK, "2533274791234567", "TesteBedrock",
            UUID.fromString("00000000-0000-0000-0009-01f659c7dc87"));
        AuthDecision decision = client.authorize(external).join();
        assertEquals(AuthDecision.Type.UNLINKED, decision.type());
        assertEquals("ABC123", decision.code());
    }

    @Test
    void linkedJavaAndBedrockProduceSameCanonicalProfile() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> respond(exchange,
            "{\"linked\":true,\"enabled\":true,\"identity\":{" +
                "\"canonical_uuid\":\"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\"," +
                "\"canonical_name\":\"Abroba\"}}"));
        server.start();
        var client = new AylaApiClient(new AylaAuthConfig(
            "http://127.0.0.1:" + server.getAddress().getPort(), "token", 1000, false));
        var java = ExternalIdentity.java(UUID.fromString("12345678-1234-4234-8234-123456789abc"), "Mounkass");
        var bedrock = new ExternalIdentity(AccountPlatform.BEDROCK, "2533274791234567", "TesteBedrock",
            UUID.fromString("00000000-0000-0000-0009-01f659c7dc87"));
        assertEquals(client.authorize(java).join().identity(), client.authorize(bedrock).join().identity());
    }

    @Test
    void disabledBedrockDoesNotRequestLinkCode() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            assertEquals("GET", exchange.getRequestMethod());
            respond(exchange, "{\"linked\":true,\"enabled\":false}");
        });
        server.start();
        var client = new AylaApiClient(new AylaAuthConfig(
            "http://127.0.0.1:" + server.getAddress().getPort(), "token", 1000, false));
        var bedrock = new ExternalIdentity(AccountPlatform.BEDROCK, "2533274791234567", "TesteBedrock",
            UUID.randomUUID());
        assertEquals(AuthDecision.Type.DISABLED, client.authorize(bedrock).join().type());
    }

    private static void respond(HttpExchange exchange, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}
