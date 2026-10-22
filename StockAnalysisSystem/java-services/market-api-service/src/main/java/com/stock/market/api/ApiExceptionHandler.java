package com.stock.market.api;

import com.stock.market.storage.DependencyException;

import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(DailyBasicNotFoundException.class)
    public ResponseEntity<?> dailyBasicMissing() {
        return ResponseEntity.status(404).body(Map.of(
                "code", "DAILY_BASIC_NOT_FOUND", "error", "Daily basic not found"));
    }

    @ExceptionHandler({
        IllegalArgumentException.class,
        org.springframework.web.method.annotation.MethodArgumentTypeMismatchException.class
    })
    public ResponseEntity<?> invalid(Exception ex) {
        return ResponseEntity.badRequest().body(Map.of("error", "Invalid query parameters"));
    }

    @ExceptionHandler(DependencyException.class)
    public ResponseEntity<?> unavailable() {
        return ResponseEntity.status(503).body(Map.of("error", "Market dependency unavailable"));
    }
}
