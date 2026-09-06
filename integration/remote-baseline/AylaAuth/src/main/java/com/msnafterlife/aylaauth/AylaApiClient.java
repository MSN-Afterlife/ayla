package com.msnafterlife.aylaauth;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;

final class AylaApiClient {
    private static final String USER_AGENT = "AylaAuth/1.0";
    private final AylaAuthConfig config;
    private final HttpClient httpClient;
    private final Gson gson;
    private final MigrationLockClient migrationLockClient;

    AylaApiClient(AylaAuthConfig config) {
        this(config, HttpClient.newBuilder()
            .connectTimeout(Duration.ofMillis(config.requestTimeoutMs()))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build(), new Gson(), new MigrationLockClient());
    }

    AylaApiClient(AylaAuthConfig config, HttpClient httpClient, Gson gson) {
        this(config, httpClient, gson, new MigrationLockClient());
    }

    AylaApiClient(AylaAuthConfig config, HttpClient httpClient, Gson gson, MigrationLockClient migrationLockClient) {
        this.config = config;
        this.httpClient = httpClient;
        this.gson = gson;
        this.migrationLockClient = migrationLockClient;
    }

    CompletableFuture<AuthDecision> authorize(ExternalIdentity identity) {
        return lookup(identity.platform(), identity.externalId()).thenCompose(decision -> switch (decision.type()) {
            case AUTHORIZED -> migrationLockClient.isLocked(identity, decision.identity())
                .thenApply(locked -> locked ? AuthDecision.migrationLocked(decision.identity()) : decision);
            case DISABLED, MIGRATION_LOCKED -> CompletableFuture.completedFuture(decision);
            case UNLINKED -> requestLink(identity.platform(), identity.externalId(), identity.username());
            case TECHNICAL_FAILURE -> throw new IllegalStateException("Unexpected internal decision");
        });
    }

    private CompletableFuture<AuthDecision> lookup(AccountPlatform platform, String externalId) {
        String encoded = URLEncoder.encode(externalId, StandardCharsets.UTF_8);
        HttpRequest request = baseRequest(config.apiBaseUrl() + "/internal/minecraft/account/"
            + platform.apiValue() + "/" + encoded).GET().build();
        return send(request).thenApply(response -> {
            requireOk(response, "lookup");
            JsonObject json = parseObject(response.body(), "lookup");
            boolean linked = requiredBoolean(json, "linked", "lookup");
            if (!linked) return AuthDecision.unlinked(null, 0);
            return requiredBoolean(json, "enabled", "lookup")
                ? AuthDecision.authorized(CanonicalIdentity.fromResponse(json, "lookup")) : AuthDecision.disabled();
        });
    }

    private CompletableFuture<AuthDecision> requestLink(AccountPlatform platform, String externalId, String username) {
        JsonObject body = new JsonObject();
        body.addProperty("platform", platform.apiValue());
        body.addProperty("external_id", externalId);
        body.addProperty("username", username);
        HttpRequest request = baseRequest(config.apiBaseUrl() + "/internal/minecraft/link/request")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(gson.toJson(body), StandardCharsets.UTF_8))
            .build();
        return send(request).thenApply(response -> {
            requireOk(response, "link request");
            JsonObject json = parseObject(response.body(), "link request");
            boolean linked = requiredBoolean(json, "linked", "link request");
            if (linked) return AuthDecision.authorized(CanonicalIdentity.fromResponse(json, "link request"));
            String code = requiredString(json, "code", "link request");
            int expiresIn = requiredInt(json, "expires_in", "link request");
            if (expiresIn <= 0) throw new ApiException("Invalid expires_in in link request response");
            return AuthDecision.unlinked(code, expiresIn);
        });
    }

    private HttpRequest.Builder baseRequest(String url) {
        return HttpRequest.newBuilder(URI.create(url))
            .timeout(Duration.ofMillis(config.requestTimeoutMs()))
            .header("Authorization", "Bearer " + config.apiToken())
            .header("Accept", "application/json")
            .header("User-Agent", USER_AGENT);
    }

    private CompletableFuture<HttpResponse<String>> send(HttpRequest request) {
        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
            .orTimeout(config.requestTimeoutMs(), java.util.concurrent.TimeUnit.MILLISECONDS)
            .exceptionally(exception -> {
                Throwable cause = exception instanceof CompletionException && exception.getCause() != null
                    ? exception.getCause() : exception;
                throw new CompletionException(new ApiException(classifyFailure(cause), cause));
            });
    }

    private static String classifyFailure(Throwable cause) {
        if (cause instanceof java.net.http.HttpTimeoutException || cause instanceof java.util.concurrent.TimeoutException) {
            return "timeout";
        }
        if (cause instanceof java.net.UnknownHostException) return "DNS failure";
        if (cause instanceof java.net.ConnectException) return "connection failure";
        return "transport failure (" + cause.getClass().getSimpleName() + ")";
    }

    private static void requireOk(HttpResponse<String> response, String operation) {
        if (response.statusCode() != 200) {
            throw new ApiException(operation + " returned HTTP " + response.statusCode());
        }
    }

    private static JsonObject parseObject(String body, String operation) {
        try {
            var element = com.google.gson.JsonParser.parseString(body);
            if (!element.isJsonObject()) throw new ApiException("Invalid JSON object in " + operation + " response");
            return element.getAsJsonObject();
        } catch (JsonParseException | IllegalStateException exception) {
            throw new ApiException("Invalid JSON in " + operation + " response", exception);
        }
    }

    private static boolean requiredBoolean(JsonObject json, String field, String operation) {
        try {
            if (!json.has(field)) throw new ApiException("Missing " + field + " in " + operation + " response");
            return json.get(field).getAsBoolean();
        } catch (UnsupportedOperationException | IllegalStateException exception) {
            throw new ApiException("Invalid " + field + " in " + operation + " response", exception);
        }
    }

    private static String requiredString(JsonObject json, String field, String operation) {
        try {
            String value = json.get(field).getAsString();
            if (value.isBlank()) throw new ApiException("Empty " + field + " in " + operation + " response");
            return value;
        } catch (NullPointerException | UnsupportedOperationException | IllegalStateException exception) {
            throw new ApiException("Invalid " + field + " in " + operation + " response", exception);
        }
    }

    private static int requiredInt(JsonObject json, String field, String operation) {
        try {
            return json.get(field).getAsInt();
        } catch (NullPointerException | UnsupportedOperationException | NumberFormatException | IllegalStateException exception) {
            throw new ApiException("Invalid " + field + " in " + operation + " response", exception);
        }
    }
}
