package com.msnafterlife.aylaauth;

record AuthDecision(Type type, String code, int expiresIn, CanonicalIdentity identity) {
    enum Type { AUTHORIZED, UNLINKED, DISABLED, MIGRATION_LOCKED, TECHNICAL_FAILURE }

    static AuthDecision authorized(CanonicalIdentity identity) {
        return new AuthDecision(Type.AUTHORIZED, null, 0, identity);
    }
    static AuthDecision unlinked(String code, int expiresIn) {
        return new AuthDecision(Type.UNLINKED, code, expiresIn, null);
    }
    static AuthDecision disabled() { return new AuthDecision(Type.DISABLED, null, 0, null); }
    static AuthDecision migrationLocked(CanonicalIdentity identity) { return new AuthDecision(Type.MIGRATION_LOCKED, null, 0, identity); }
    static AuthDecision technicalFailure() { return new AuthDecision(Type.TECHNICAL_FAILURE, null, 0, null); }
}
