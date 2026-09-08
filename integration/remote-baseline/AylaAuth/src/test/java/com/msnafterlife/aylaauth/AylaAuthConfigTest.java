package com.msnafterlife.aylaauth;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

class AylaAuthConfigTest {
    @Test
    void parsesTomlScalarValues() {
        var values = AylaAuthConfig.parse(List.of(
            "api_base_url = \"https://staging.example\"",
            "api_token = \"secret\"",
            "request_timeout_ms = 3000",
            "fail_open = false"));
        assertEquals("https://staging.example", values.get("api_base_url"));
        assertEquals("false", values.get("fail_open"));
    }
}
