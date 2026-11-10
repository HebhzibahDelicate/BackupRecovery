package com.example.backuprecovery;

import jakarta.validation.constraints.*;
import java.math.BigDecimal;

public record CreateTransactionRequest(
        @NotBlank(message = "customerName is required")
        @Size(max = 100, message = "customerName must be at most 100 characters")
        String customerName,

        @NotNull(message = "amount is required")
        @DecimalMin(value = "0.01", message = "amount must be at least 0.01")
        @Digits(integer = 10, fraction = 2, message = "amount allows up to 10 digits and 2 decimals")
        BigDecimal amount) { }