package com.msnafterlife.aylaauth;

import java.net.InetSocketAddress;
import java.time.Duration;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

final class LoginContextStore {
    private final ConcurrentHashMap<InetSocketAddress, LoginContext> contexts = new ConcurrentHashMap<>();
    private final ScheduledExecutorService scheduler;
    private final Duration ttl;

    LoginContextStore(ScheduledExecutorService scheduler, Duration ttl) {
        this.scheduler = Objects.requireNonNull(scheduler);
        this.ttl = Objects.requireNonNull(ttl);
    }

    void put(InetSocketAddress key, LoginContext context) {
        contexts.put(key, context);
        scheduler.schedule(() -> contexts.remove(key, context), ttl.toMillis(), TimeUnit.MILLISECONDS);
    }

    LoginContext take(InetSocketAddress key) {
        return contexts.remove(key);
    }

    int size() {
        return contexts.size();
    }
}
