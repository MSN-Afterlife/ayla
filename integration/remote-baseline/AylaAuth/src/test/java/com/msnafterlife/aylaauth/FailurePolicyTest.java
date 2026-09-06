package com.msnafterlife.aylaauth;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FailurePolicyTest {
    @Test
    void failClosedBlocksTechnicalFailure() {
        assertFalse(FailurePolicy.allowTechnicalFailure(false));
    }

    @Test
    void failOpenAllowsOnlyTechnicalFailurePath() {
        assertTrue(FailurePolicy.allowTechnicalFailure(true));
    }
}
