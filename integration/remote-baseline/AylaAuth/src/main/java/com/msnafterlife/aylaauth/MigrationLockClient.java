package com.msnafterlife.aylaauth;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.TimeUnit;

final class MigrationLockClient {
    private final String baseUrl;
    private final String token;
    private final int timeoutMs;
    private final Path cachePath;
    private final HttpClient httpClient;
    private final Gson gson = new Gson();

    MigrationLockClient() {
        this(
            System.getenv("AYLAAUTH_MIGRATION_LOCK_URL"),
            System.getenv("AYLAAUTH_MIGRATION_LOCK_TOKEN"),
            parseTimeout(System.getenv("AYLAAUTH_MIGRATION_LOCK_TIMEOUT_MS")),
            Path.of(Optional.ofNullable(System.getenv("AYLAAUTH_MIGRATION_LOCK_CACHE")).orElse("aylaauth-migration-lock-cache.json")),
            HttpClient.newBuilder().connectTimeout(Duration.ofMillis(parseTimeout(System.getenv("AYLAAUTH_MIGRATION_LOCK_TIMEOUT_MS")))).build()
        );
    }

    MigrationLockClient(String baseUrl, String token, int timeoutMs, Path cachePath, HttpClient httpClient) {
        this.baseUrl = baseUrl == null || baseUrl.isBlank() ? "" : stripSlash(baseUrl);
        this.token = token == null ? "" : token;
        this.timeoutMs = timeoutMs;
        this.cachePath = cachePath;
        this.httpClient = httpClient;
    }

    boolean enabled() {
        return !baseUrl.isBlank() && !token.isBlank();
    }

    CompletableFuture<Boolean> isLocked(ExternalIdentity external, CanonicalIdentity canonical) {
        if (!enabled()) return CompletableFuture.completedFuture(false);
        String key = canonical.uuid().toString().toLowerCase(java.util.Locale.ROOT);
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + "/api/v1/gateway/lock?key=" + encode(key)))
            .timeout(Duration.ofMillis(timeoutMs))
            .header("Authorization", "Bearer " + token)
            .header("Accept", "application/json")
            .header("User-Agent", "AylaAuth/1.0")
            .GET()
            .build();
        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
            .orTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .thenApply(response -> parseLocked(response, key, external))
            .exceptionally(error -> cachedLocked(key, external));
    }

    private boolean parseLocked(HttpResponse<String> response, String canonicalKey, ExternalIdentity external) {
        if (response.statusCode() != 200) return cachedLocked(canonicalKey, external);
        JsonObject json = JsonParser.parseString(response.body()).getAsJsonObject();
        boolean locked = json.has("locked") && json.get("locked").getAsBoolean();
        updateCache(canonicalKey, external, locked);
        return locked;
    }

    private boolean cachedLocked(String canonicalKey, ExternalIdentity external) {
        Map<String, CacheEntry> cache = load();
        long now = Instant.now().getEpochSecond();
        for (String key : keys(canonicalKey, external)) {
            CacheEntry entry = cache.get(key);
            if (entry != null && entry.locked && entry.expiresAt >= now) return true;
        }
        return false;
    }

    private void updateCache(String canonicalKey, ExternalIdentity external, boolean locked) {
        Map<String, CacheEntry> cache = load();
        long expires = Instant.now().plusSeconds(locked ? 86400 : 60).getEpochSecond();
        for (String key : keys(canonicalKey, external)) cache.put(key, new CacheEntry(locked, expires));
        save(cache);
    }

    private java.util.Set<String> keys(String canonicalKey, ExternalIdentity external) {
        return java.util.Set.of(
            "canonical:" + canonicalKey.toLowerCase(java.util.Locale.ROOT),
            external.platform().apiValue() + ":" + external.externalId().toLowerCase(java.util.Locale.ROOT)
        );
    }

    private Map<String, CacheEntry> load() {
        if (Files.notExists(cachePath)) return new HashMap<>();
        try {
            JsonObject root = JsonParser.parseString(Files.readString(cachePath, StandardCharsets.UTF_8)).getAsJsonObject();
            Map<String, CacheEntry> result = new HashMap<>();
            for (String key : root.keySet()) {
                JsonObject value = root.getAsJsonObject(key);
                result.put(key, new CacheEntry(value.get("locked").getAsBoolean(), value.get("expires_at").getAsLong()));
            }
            return result;
        } catch (IOException | RuntimeException exception) {
            return new HashMap<>();
        }
    }

    private void save(Map<String, CacheEntry> cache) {
        JsonObject root = new JsonObject();
        for (Map.Entry<String, CacheEntry> entry : cache.entrySet()) {
            JsonObject value = new JsonObject();
            value.addProperty("locked", entry.getValue().locked);
            value.addProperty("expires_at", entry.getValue().expiresAt);
            root.add(entry.getKey(), value);
        }
        try {
            Files.createDirectories(cachePath.toAbsolutePath().getParent());
            Path tmp = cachePath.resolveSibling(cachePath.getFileName() + ".tmp");
            Files.writeString(tmp, gson.toJson(root), StandardCharsets.UTF_8);
            Files.move(tmp, cachePath, java.nio.file.StandardCopyOption.REPLACE_EXISTING, java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException exception) {
            throw new CompletionException(new ApiException("migration lock cache write failure", exception));
        }
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String stripSlash(String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private static int parseTimeout(String raw) {
        if (raw == null || raw.isBlank()) return 1000;
        try {
            int value = Integer.parseInt(raw);
            return Math.max(250, Math.min(5000, value));
        } catch (NumberFormatException exception) {
            return 1000;
        }
    }

    private record CacheEntry(boolean locked, long expiresAt) {}
}
