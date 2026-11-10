package com.example.backuprecovery;

import java.math.BigDecimal;
import java.time.Instant;

public record TransactionResponse(Long id, String transactionId, String customerName,
                                  BigDecimal amount, Instant transactionDate) {

    public static TransactionResponse from(PaymentTransaction t) {
        return new TransactionResponse(t.getId(), t.getTransactionId(),
                t.getCustomerName(), t.getAmount(), t.getTransactionDate());
    }
}