package com.msnafterlife.aylaauth;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

record AylaAuthConfig(String apiBaseUrl, String apiToken, int requestTimeoutMs, boolean failOpen) {
    static AylaAuthConfig load(Path path) throws IOException {
        Map<String, String> values = parse(Files.readAllLines(path, StandardCharsets.UTF_8));
        String baseUrl = required(values, "api_base_url");
        String token = required(values, "api_token");
        int timeout;
        try {
            timeout = Integer.parseInt(values.getOrDefault("request_timeout_ms", "3000"));
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("request_timeout_ms must be an integer", exception);
        }
        if (timeout < 250 || timeout > 30_000) {
            throw new IllegalArgumentException("request_timeout_ms must be between 250 and 30000");
        }
        boolean failOpen = parseBoolean(values.getOrDefault("fail_open", "false"), "fail_open");
        return new AylaAuthConfig(normalizeBaseUrl(baseUrl), token, timeout, failOpen);
    }

    static Map<String, String> parse(List<String> lines) {
        Map<String, String> result = new HashMap<>();
        for (int index = 0; index < lines.size(); index++) {
            String line = lines.get(index).trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            int equals = line.indexOf('=');
            if (equals <= 0) {
                throw new IllegalArgumentException("Invalid config line " + (index + 1));
            }
            String key = line.substring(0, equals).trim();
            String raw = line.substring(equals + 1).trim();
            String value = raw;
            if (raw.startsWith("\"") && raw.endsWith("\"") && raw.length() >= 2) {
                value = raw.substring(1, raw.length() - 1);
            }
            result.put(key, value);
        }
        return result;
    }

    private static String required(Map<String, String> values, String key) {
        String value = values.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(key + " must not be empty");
        }
        return value;
    }

    private static boolean parseBoolean(String value, String key) {
        if ("true".equalsIgnoreCase(value)) return true;
        if ("false".equalsIgnoreCase(value)) return false;
        throw new IllegalArgumentException(key + " must be true or false");
    }

    private static String normalizeBaseUrl(String value) {
        if (!value.startsWith("https://") && !value.startsWith("http://")) {
            throw new IllegalArgumentException("api_base_url must use http or https");
        }
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }
}
