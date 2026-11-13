package com.example.backuprecovery;

import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.net.URI;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/transactions")
public class TransactionController {

    private final TransactionService service;

    public TransactionController(TransactionService service) {
        this.service = service;
    }

    @PostMapping
    public ResponseEntity<TransactionResponse> create(@Valid @RequestBody CreateTransactionRequest request) {
        TransactionResponse created = service.create(request);
        return ResponseEntity
                .created(URI.create("/api/transactions/" + created.transactionId()))
                .body(created);
    }

    @PostMapping("/seed")
    public ResponseEntity<Map<String, Object>> seed(@RequestParam(defaultValue = "15") int count) {
        List<TransactionResponse> created = service.seed(count);
        return ResponseEntity.status(201).body(Map.of("created", created.size()));
    }

    @GetMapping
    public List<TransactionResponse> list() {
        return service.findAll();
    }

    @GetMapping("/count")
    public Map<String, Long> count() {
        return Map.of("count", service.count());
    }

    @GetMapping("/ids")
    public List<String> ids() {
        return service.allTransactionIds();
    }

    @GetMapping("/{transactionId}")
    public TransactionResponse get(@PathVariable String transactionId) {
        return service.findByTransactionId(transactionId);
    }
}
