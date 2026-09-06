package com.msnafterlife.aylaauth;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;

import static org.junit.jupiter.api.Assertions.*;

class LoginContextStoreTest {
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();

    @AfterEach
    void stopScheduler() {
        scheduler.shutdownNow();
    }

    @Test
    void contextIsSharedThenRemovedAfterLogin() {
        var store = new LoginContextStore(scheduler, Duration.ofSeconds(10));
        var key = new InetSocketAddress("192.0.2.10", 40001);
        var context = context("PlayerOne");
        store.put(key, context);
        assertSame(context, store.take(key));
        assertNull(store.take(key));
        assertEquals(0, store.size());
    }

    @Test
    void concurrentPlayersRemainIsolated() {
        var store = new LoginContextStore(scheduler, Duration.ofSeconds(10));
        var firstKey = new InetSocketAddress("192.0.2.10", 40001);
        var secondKey = new InetSocketAddress("192.0.2.10", 40002);
        var first = context("PlayerOne");
        var second = context("PlayerTwo");
        store.put(firstKey, first);
        store.put(secondKey, second);
        assertSame(second, store.take(secondKey));
        assertSame(first, store.take(firstKey));
        assertEquals(0, store.size());
    }

    @Test
    void abandonedContextExpiresWithoutLeak() throws Exception {
        var store = new LoginContextStore(scheduler, Duration.ofMillis(30));
        store.put(new InetSocketAddress("192.0.2.10", 40001), context("PlayerOne"));
        long deadline = System.nanoTime() + Duration.ofSeconds(2).toNanos();
        while (store.size() != 0 && System.nanoTime() < deadline) Thread.sleep(5);
        assertEquals(0, store.size());
    }

    @Test
    void oldExpiryCannotRemoveReplacementContext() throws Exception {
        var store = new LoginContextStore(scheduler, Duration.ofMillis(50));
        var key = new InetSocketAddress("192.0.2.10", 40001);
        var oldContext = context("OldPlayer");
        var replacement = context("NewPlayer");
        store.put(key, oldContext);
        Thread.sleep(20);
        store.put(key, replacement);
        Thread.sleep(40);
        assertSame(replacement, store.take(key));
    }

    private static LoginContext context(String name) {
        UUID mojang = UUID.nameUUIDFromBytes(name.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        var identity = new CanonicalIdentity(UUID.randomUUID(), name);
        return LoginContext.completed(ExternalIdentity.java(mojang, name), AuthDecision.authorized(identity));
    }
}
