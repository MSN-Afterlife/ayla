package com.msnafterlife.aylaauth;

import java.util.UUID;

record LoginContext(ExternalIdentity externalIdentity, AuthDecision decision, Throwable failure) {
    static LoginContext completed(ExternalIdentity identity, AuthDecision decision) {
        return new LoginContext(identity, decision, null);
    }

    static LoginContext failed(ExternalIdentity identity, Throwable failure) {
        return new LoginContext(identity, null, failure);
    }
}
