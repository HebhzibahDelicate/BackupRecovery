package com.example.backuprecovery;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "transactions")
public class PaymentTransaction {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "transaction_id", nullable = false, unique = true, updatable = false, length = 40)
    private String transactionId;

    @Column(name = "customer_name", nullable = false, length = 100)
    private String customerName;

    @Column(nullable = false, precision = 12, scale = 2)
    private BigDecimal amount;

    @Column(name = "transaction_date", nullable = false, updatable = false)
    private Instant transactionDate;

    protected PaymentTransaction() { } // required by JPA

    public PaymentTransaction(String transactionId, String customerName,
                              BigDecimal amount, Instant transactionDate) {
        this.transactionId = transactionId;
        this.customerName = customerName;
        this.amount = amount;
        this.transactionDate = transactionDate;
    }

    public Long getId() { return id; }
    public String getTransactionId() { return transactionId; }
    public String getCustomerName() { return customerName; }
    public BigDecimal getAmount() { return amount; }
    public Instant getTransactionDate() { return transactionDate; }
}