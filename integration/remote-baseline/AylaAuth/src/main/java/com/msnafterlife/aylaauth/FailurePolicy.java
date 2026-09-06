package com.msnafterlife.aylaauth;

final class FailurePolicy {
    private FailurePolicy() {}

    static boolean allowTechnicalFailure(boolean failOpen) {
        return failOpen;
    }
}
