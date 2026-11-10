package com.example.backuprecovery;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import java.util.List;
import java.util.Optional;

public interface TransactionRepository extends JpaRepository<PaymentTransaction, Long> {

    Optional<PaymentTransaction> findByTransactionId(String transactionId);

    // Used later to compare original vs restored data by ID, not just by count
    @Query("select t.transactionId from PaymentTransaction t order by t.transactionId")
    List<String> findAllTransactionIds();
}