package com.msnafterlife.aylaauth;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletionException;

import static org.junit.jupiter.api.Assertions.*;

class AylaApiClientTest {
    private static final UUID UUID_VALUE = UUID.fromString("12345678-1234-1234-1234-123456789abc");
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) server.stop(0);
    }

    @Test
    void linkedAndEnabledIsAuthorized() throws Exception {
        client(exchange -> respond(exchange, 200, linkedResponse()));
        AuthDecision decision = authorize();
        assertEquals(AuthDecision.Type.AUTHORIZED, decision.type());
        assertEquals(UUID.fromString("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"), decision.identity().uuid());
        assertEquals("Abroba", decision.identity().name());
    }

    @Test
    void linkedAndDisabledIsDeniedEvenWithoutLinkRequest() throws Exception {
        client(exchange -> respond(exchange, 200, "{\"linked\":true,\"enabled\":false}"));
        assertEquals(AuthDecision.Type.DISABLED, authorize().type());
    }

    @Test
    void unlinkedRequestsAndReturnsCode() throws Exception {
        client(exchange -> {
            if (exchange.getRequestURI().getPath().endsWith("/link/request")) {
                assertEquals("POST", exchange.getRequestMethod());
                String request = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                assertTrue(request.contains("\"external_id\":\"12345678-1234-1234-1234-123456789abc\""));
                assertTrue(request.contains("\"username\":\"PlayerTeste\""));
                assertEquals("Bearer secret-test-token", exchange.getRequestHeaders().getFirst("Authorization"));
                assertEquals("AylaAuth/1.0", exchange.getRequestHeaders().getFirst("User-Agent"));
                respond(exchange, 200, "{\"linked\":false,\"code\":\"K7F2Q9\",\"expires_in\":600}");
            } else {
                respond(exchange, 200, "{\"linked\":false}");
            }
        });
        AuthDecision decision = authorize();
        assertEquals(AuthDecision.Type.UNLINKED, decision.type());
        assertEquals("K7F2Q9", decision.code());
        assertEquals(600, decision.expiresIn());
    }

    @Test
    void raceWhereLinkRequestReportsLinkedIsAuthorized() throws Exception {
        client(exchange -> respond(exchange, 200,
            exchange.getRequestURI().getPath().endsWith("/link/request")
                ? "{\"linked\":true,\"identity\":{\"canonical_uuid\":\"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\",\"canonical_name\":\"Abroba\"}}"
                : "{\"linked\":false}"));
        assertEquals(AuthDecision.Type.AUTHORIZED, authorize().type());
    }

    @Test
    void invalidCanonicalUuidIsTechnicalFailure() throws Exception {
        client(exchange -> respond(exchange, 200,
            "{\"linked\":true,\"enabled\":true,\"identity\":{\"canonical_uuid\":\"bad\",\"canonical_name\":\"Abroba\"}}"));
        assertApiFailure("Invalid canonical_uuid");
    }

    @Test
    void invalidCanonicalNameIsTechnicalFailure() throws Exception {
        client(exchange -> respond(exchange, 200,
            "{\"linked\":true,\"enabled\":true,\"identity\":{\"canonical_uuid\":\"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\",\"canonical_name\":\"nome inválido!\"}}"));
        assertApiFailure("Invalid canonical_name");
    }

    @Test
    void lookupAlwaysUsesOriginalMojangUuidNotCanonicalUuid() throws Exception {
        client(exchange -> {
            assertTrue(exchange.getRequestURI().getPath().endsWith("/12345678-1234-1234-1234-123456789abc"));
            assertFalse(exchange.getRequestURI().getPath().contains("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"));
            respond(exchange, 200, linkedResponse());
        });
        assertEquals(AuthDecision.Type.AUTHORIZED, authorize().type());
    }

    @Test
    void timeoutIsTechnicalFailure() throws Exception {
        client(exchange -> {
            try { Thread.sleep(600); } catch (InterruptedException exception) { Thread.currentThread().interrupt(); }
            respond(exchange, 200, "{\"linked\":true,\"enabled\":true}");
        });
        assertApiFailure("timeout");
    }

    @Test
    void unauthorizedIsTechnicalFailure() throws Exception {
        client(exchange -> respond(exchange, 401, "{}"));
        assertApiFailure("HTTP 401");
    }

    @Test
    void serverErrorIsTechnicalFailure() throws Exception {
        client(exchange -> respond(exchange, 500, "{}"));
        assertApiFailure("HTTP 500");
    }

    @Test
    void notFoundIsTechnicalFailure() throws Exception {
        client(exchange -> respond(exchange, 404, "{}"));
        assertApiFailure("HTTP 404");
    }

    @Test
    void invalidJsonIsTechnicalFailure() throws Exception {
        client(exchange -> respond(exchange, 200, "not-json"));
        assertApiFailure("Invalid JSON");
    }

    private AylaApiClient apiClient;

    private void client(com.sun.net.httpserver.HttpHandler handler) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", handler);
        server.start();
        var config = new AylaAuthConfig("http://127.0.0.1:" + server.getAddress().getPort(),
            "secret-test-token", 250, false);
        apiClient = new AylaApiClient(config);
    }

    private AuthDecision authorize() {
        return apiClient.authorize(ExternalIdentity.java(UUID_VALUE, "PlayerTeste")).join();
    }

    private void assertApiFailure(String messagePart) {
        CompletionException exception = assertThrows(CompletionException.class, this::authorize);
        Throwable cause = exception.getCause();
        while (cause.getCause() != null && !(cause instanceof ApiException)) cause = cause.getCause();
        assertInstanceOf(ApiException.class, cause);
        assertTrue(cause.getMessage().contains(messagePart), cause.getMessage());
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static String linkedResponse() {
        return "{\"linked\":true,\"enabled\":true,\"identity\":{" +
            "\"canonical_uuid\":\"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\"," +
            "\"canonical_name\":\"Abroba\"}}";
    }
}
