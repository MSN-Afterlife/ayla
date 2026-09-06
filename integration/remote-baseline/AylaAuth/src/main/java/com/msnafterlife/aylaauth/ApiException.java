package com.msnafterlife.aylaauth;

final class ApiException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    ApiException(String message) { super(message); }
    ApiException(String message, Throwable cause) { super(message, cause); }
}
