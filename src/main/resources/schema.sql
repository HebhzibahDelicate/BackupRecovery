CREATE TABLE IF NOT EXISTS transactions (
    id               BIGSERIAL PRIMARY KEY,
    transaction_id   VARCHAR(40)    NOT NULL UNIQUE,
    customer_name    VARCHAR(100)   NOT NULL,
    amount           NUMERIC(12,2)  NOT NULL CHECK (amount > 0),
    transaction_date TIMESTAMPTZ    NOT NULL DEFAULT now()
    );