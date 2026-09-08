package com.msnafterlife.aylaauth;

import com.google.gson.JsonObject;

import java.util.UUID;
import java.util.regex.Pattern;

record CanonicalIdentity(UUID uuid, String name) {
    private static final Pattern VALID_NAME = Pattern.compile("[A-Za-z0-9_]{1,16}");

    static CanonicalIdentity fromResponse(JsonObject response, String operation) {
        JsonObject identity;
        try {
            identity = response.getAsJsonObject("identity");
        } catch (ClassCastException | IllegalStateException exception) {
            throw new ApiException("Invalid identity in " + operation + " response", exception);
        }
        if (identity == null) throw new ApiException("Missing identity in " + operation + " response");

        String rawUuid = requiredString(identity, "canonical_uuid", operation);
        String name = requiredString(identity, "canonical_name", operation);
        UUID uuid;
        try {
            uuid = UUID.fromString(rawUuid);
        } catch (IllegalArgumentException exception) {
            throw new ApiException("Invalid canonical_uuid in " + operation + " response", exception);
        }
        if (!VALID_NAME.matcher(name).matches()) {
            throw new ApiException("Invalid canonical_name in " + operation + " response");
        }
        return new CanonicalIdentity(uuid, name);
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
}
