package com.example.backuprecovery;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

@Service
public class TransactionService {

    private static final List<String> SAMPLE_NAMES = List.of(
            "Arun", "Ravi", "Priya", "John", "Kumar",
            "Meena", "Divya", "Sanjay", "Fatima", "Karthik");

    private final TransactionRepository repository;

    public TransactionService(TransactionRepository repository) {
        this.repository = repository;
    }

    @Transactional
    public TransactionResponse create(CreateTransactionRequest request) {
        PaymentTransaction saved = repository.save(newEntity(request.customerName(), request.amount()));
        return TransactionResponse.from(saved);
    }

    // Generates synthetic data: random customer, random amount 100.00 - 9,999.99
    @Transactional
    public List<TransactionResponse> seed(int count) {
        if (count < 1 || count > 100) {
            throw new IllegalArgumentException("count must be between 1 and 100");
        }
        ThreadLocalRandom rnd = ThreadLocalRandom.current();
        List<PaymentTransaction> batch = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            String name = SAMPLE_NAMES.get(rnd.nextInt(SAMPLE_NAMES.size()));
            BigDecimal amount = BigDecimal.valueOf(rnd.nextLong(10_000, 1_000_000), 2);
            batch.add(newEntity(name, amount));
        }
        return repository.saveAll(batch).stream().map(TransactionResponse::from).toList();
    }

    @Transactional(readOnly = true)
    public List<TransactionResponse> findAll() {
        return repository.findAll().stream().map(TransactionResponse::from).toList();
    }

    @Transactional(readOnly = true)
    public TransactionResponse findByTransactionId(String transactionId) {
        return repository.findByTransactionId(transactionId)
                .map(TransactionResponse::from)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "Transaction not found: " + transactionId));
    }

    @Transactional(readOnly = true)
    public long count() {
        return repository.count();
    }

    @Transactional(readOnly = true)
    public List<String> allTransactionIds() {
        return repository.findAllTransactionIds();
    }

    private PaymentTransaction newEntity(String customerName, BigDecimal amount) {
        String txnId = "TXN-" + UUID.randomUUID().toString().substring(0, 8).toUpperCase();
        return new PaymentTransaction(txnId, customerName.trim(), amount, Instant.now());
    }
}