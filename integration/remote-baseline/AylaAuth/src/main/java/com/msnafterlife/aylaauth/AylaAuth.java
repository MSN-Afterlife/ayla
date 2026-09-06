package com.msnafterlife.aylaauth;

import com.google.inject.Inject;
import com.velocitypowered.api.event.EventTask;
import com.velocitypowered.api.event.ResultedEvent;
import com.velocitypowered.api.event.Subscribe;
import com.velocitypowered.api.event.connection.LoginEvent;
import com.velocitypowered.api.event.player.GameProfileRequestEvent;
import com.velocitypowered.api.event.proxy.ProxyInitializeEvent;
import com.velocitypowered.api.event.proxy.ProxyShutdownEvent;
import com.velocitypowered.api.plugin.Plugin;
import com.velocitypowered.api.plugin.Dependency;
import com.velocitypowered.api.plugin.annotation.DataDirectory;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.slf4j.Logger;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import org.geysermc.floodgate.api.FloodgateApi;

@Plugin(id = "aylaauth", name = "AylaAuth", version = "1.2.0",
    description = "Ayla canonical identity authorization for Java and Bedrock players",
    dependencies = {@Dependency(id = "floodgate")})
public final class AylaAuth {
    private final Logger logger;
    private final Path dataDirectory;
    private final ScheduledExecutorService contextScheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "AylaAuth context expiry");
        thread.setDaemon(true);
        return thread;
    });
    private final LoginContextStore loginContexts = new LoginContextStore(contextScheduler, Duration.ofSeconds(30));
    private volatile AylaApiClient apiClient;
    private volatile AylaAuthConfig config;
    private volatile ExternalIdentityResolver identityResolver;

    @Inject
    public AylaAuth(Logger logger, @DataDirectory Path dataDirectory) {
        this.logger = logger;
        this.dataDirectory = dataDirectory;
    }

    @Subscribe
    public void onInitialize(ProxyInitializeEvent ignored) {
        try {
            Path configPath = installDefaultConfig();
            config = AylaAuthConfig.load(configPath);
            apiClient = new AylaApiClient(config);
            identityResolver = new FloodgateIdentityResolver(FloodgateApi.getInstance());
            logger.info("[AylaAuth] Plugin started; API configured at {} (timeout={}ms, fail_open={})",
                config.apiBaseUrl(), config.requestTimeoutMs(), config.failOpen());
        } catch (IOException | IllegalArgumentException exception) {
            logger.error("[AylaAuth] Configuration error; all logins will be blocked: {}", exception.getMessage());
        }
    }

    @Subscribe
    public void onShutdown(ProxyShutdownEvent ignored) {
        contextScheduler.shutdownNow();
    }

    @Subscribe
    public EventTask onGameProfileRequest(GameProfileRequestEvent event) {
        AylaApiClient client = apiClient;
        var original = event.getOriginalProfile();
        var remoteAddress = event.getConnection().getRemoteAddress();
        ExternalIdentity seed = ExternalIdentity.java(original.getId(), original.getName());
        java.util.concurrent.atomic.AtomicReference<ExternalIdentity> resolvedRef =
            new java.util.concurrent.atomic.AtomicReference<>(seed);
        CompletableFuture<Void> authorization;
        try {
            authorization = ConnectionIdentityClassifier.resolve(original, event.isOnlineMode(), requireResolver())
                .thenCompose(resolved -> {
                    resolvedRef.set(resolved);
                    if (client == null || config == null) {
                        throw new ApiException("Plugin configuration is unavailable");
                    }
                    return client.authorize(resolved).thenAccept(decision -> {
                        if (decision.type() == AuthDecision.Type.AUTHORIZED) {
                            var canonical = CanonicalProfileFactory.create(original, decision.identity());
                            event.setGameProfile(canonical);
                            logger.info("[AylaAuth] Canonical profile applied: {}={}/{} -> Ayla={}/{}",
                                resolved.platform().apiValue(), original.getName(), original.getId(),
                                canonical.getName(), canonical.getId());
                        }
                        loginContexts.put(remoteAddress, LoginContext.completed(resolved, decision));
                    });
                })
                .handle((ignored, throwable) -> {
                    if (throwable != null) {
                        Throwable cause = unwrap(throwable);
                        ExternalIdentity resolved = resolvedRef.get();
                        loginContexts.put(remoteAddress, LoginContext.failed(resolved, cause));
                        logger.error("[AylaAuth] Bedrock/identity authorization failed for {} ({}): {}",
                            resolved.username(), resolved.externalId(), cause.getMessage());
                    }
                    return null;
                });
        } catch (RuntimeException exception) {
            loginContexts.put(remoteAddress, LoginContext.failed(seed, exception));
            return null;
        }
        return EventTask.resumeWhenComplete(authorization);
    }

    @Subscribe
    public void onLogin(LoginEvent event) {
        var player = event.getPlayer();
        LoginContext context = loginContexts.take(player.getRemoteAddress());
        if (context == null) {
            logger.error("[AylaAuth] Missing transient login context for {} from {}",
                player.getUsername(), player.getRemoteAddress());
            event.setResult(ResultedEvent.ComponentResult.denied(technicalFailureMessage()));
            return;
        }
        if (context.failure() != null) {
            logger.error("[AylaAuth] Blocking login after technical failure for {} ({}): {}",
                context.externalIdentity().username(), context.externalIdentity().externalId(),
                context.failure().getMessage());
            // Canonical mode must never fall back to a Mojang profile, even when fail_open=true.
            event.setResult(ResultedEvent.ComponentResult.denied(technicalFailureMessage()));
            return;
        }
        applyDecision(event, context);
    }

    private void applyDecision(LoginEvent event, LoginContext context) {
        AuthDecision decision = context.decision();
        var player = event.getPlayer();
        switch (decision.type()) {
            case AUTHORIZED -> logger.info("[AylaAuth] Authorized {} player {} ({})",
                context.externalIdentity().platform().apiValue(), context.externalIdentity().username(),
                context.externalIdentity().externalId());
            case DISABLED -> {
                logger.info("[AylaAuth] Identity disabled for {} ({})", player.getUsername(), player.getUniqueId());
                event.setResult(ResultedEvent.ComponentResult.denied(Component.text()
                    .append(Component.text("Sua conta Minecraft está desativada no MSN Afterlife.\n", NamedTextColor.RED))
                    .append(Component.text("Entre em contato com a administração.", NamedTextColor.WHITE)).build()));
            }
            case MIGRATION_LOCKED -> {
                logger.info("[AylaAuth] Migration lock denied login for {} ({})", player.getUsername(), player.getUniqueId());
                event.setResult(ResultedEvent.ComponentResult.denied(Component.text()
                    .append(Component.text("Sua conta está passando por uma migração de dados.\n", NamedTextColor.YELLOW))
                    .append(Component.text("Aguarde alguns instantes e tente novamente.", NamedTextColor.WHITE)).build()));
            }
            case UNLINKED -> {
                logger.info("[AylaAuth] {} player {} ({}) is not linked",
                    context.externalIdentity().platform().apiValue(), context.externalIdentity().username(),
                    context.externalIdentity().externalId());
                long minutes = Math.max(1, (decision.expiresIn() + 59L) / 60L);
                event.setResult(ResultedEvent.ComponentResult.denied(Component.text()
                    .append(Component.text(context.externalIdentity().platform() == AccountPlatform.BEDROCK
                        ? "Sua conta Bedrock ainda não está vinculada.\n\n"
                        : "Sua conta Minecraft ainda não está vinculada.\n\n", NamedTextColor.YELLOW))
                    .append(Component.text("Use no Discord:\n", NamedTextColor.WHITE))
                    .append(Component.text("a!minecraft link " + decision.code() + "\n\n", NamedTextColor.AQUA))
                    .append(Component.text("O código expira em " + minutes + " minutos.", NamedTextColor.GRAY)).build()));
            }
            case TECHNICAL_FAILURE -> throw new IllegalStateException("Technical failures are represented by exceptions");
        }
    }

    private ExternalIdentityResolver requireResolver() {
        ExternalIdentityResolver resolver = identityResolver;
        if (resolver == null) throw new ApiException("Floodgate identity resolver is unavailable");
        return resolver;
    }

    private Path installDefaultConfig() throws IOException {
        Files.createDirectories(dataDirectory);
        Path configPath = dataDirectory.resolve("config.toml");
        if (Files.notExists(configPath)) {
            try (InputStream input = getClass().getResourceAsStream("/config.toml")) {
                if (input == null) throw new IOException("Bundled config.toml is missing");
                Files.copy(input, configPath, StandardCopyOption.COPY_ATTRIBUTES);
            }
        }
        return configPath;
    }

    private static Component technicalFailureMessage() {
        return Component.text()
            .append(Component.text("Não foi possível validar sua conta agora.\n", NamedTextColor.RED))
            .append(Component.text("Tente novamente em alguns instantes.", NamedTextColor.WHITE)).build();
    }

    private static Throwable unwrap(Throwable throwable) {
        Throwable current = throwable;
        while ((current instanceof CompletionException || current instanceof java.util.concurrent.ExecutionException)
            && current.getCause() != null) current = current.getCause();
        return current;
    }
}
