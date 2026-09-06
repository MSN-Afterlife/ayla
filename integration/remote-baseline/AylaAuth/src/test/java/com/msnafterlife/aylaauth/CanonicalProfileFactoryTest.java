package com.msnafterlife.aylaauth;

import com.velocitypowered.api.util.GameProfile;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;

class CanonicalProfileFactoryTest {
    @Test
    void appliesCanonicalUuidAndNameAndPreservesOriginalProperties() {
        var texture = new GameProfile.Property("textures", "texture-value", "texture-signature");
        var properties = List.of(texture);
        var original = new GameProfile(UUID.fromString("12345678-1234-1234-1234-123456789abc"),
            "Mounkass", properties);
        var identity = new CanonicalIdentity(UUID.fromString("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"), "Abroba");

        GameProfile canonical = CanonicalProfileFactory.create(original, identity);

        assertEquals(identity.uuid(), canonical.getId());
        assertEquals(identity.name(), canonical.getName());
        assertEquals(properties, canonical.getProperties());
        assertSame(texture, canonical.getProperties().getFirst());
    }
}
