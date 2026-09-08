package com.msnafterlife.aylaauth;

enum AccountPlatform {
    JAVA("java"), BEDROCK("bedrock");

    private final String apiValue;

    AccountPlatform(String apiValue) {
        this.apiValue = apiValue;
    }

    String apiValue() {
        return apiValue;
    }
}
