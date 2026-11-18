-- Synthetic Halcyon world. Entity-scoped tables carry entity_id so row-level security
-- (added with the Query API) can key on it. Money is numeric(18,2) plus an ISO currency.

DROP SCHEMA IF EXISTS treasury CASCADE;
DROP SCHEMA IF EXISTS docs CASCADE;
DROP SCHEMA IF EXISTS eval CASCADE;
CREATE SCHEMA treasury;
CREATE SCHEMA docs;
CREATE SCHEMA eval;

CREATE TABLE treasury.entities (
    entity_id       text PRIMARY KEY,
    name            text NOT NULL,
    country         char(2) NOT NULL,
    currency        char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    timezone        text NOT NULL,
    approval_limit  numeric(18,2) NOT NULL CHECK (approval_limit > 0)
);

CREATE TABLE treasury.banks (
    bank_id  text PRIMARY KEY,
    name     text NOT NULL,
    bic      text NOT NULL
);

CREATE TABLE treasury.accounts (
    account_id       text PRIMARY KEY,
    entity_id        text NOT NULL REFERENCES treasury.entities,
    bank_id          text NOT NULL REFERENCES treasury.banks,
    iban             text NOT NULL UNIQUE,
    currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    opened_on        date NOT NULL,
    opening_balance  numeric(18,2) NOT NULL
);

CREATE TABLE treasury.counterparties (
    counterparty_id  text PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    country          char(2) NOT NULL
);

-- effective_from <= d < effective_to; NULL effective_to means open-ended.
CREATE TABLE treasury.ssis (
    ssi_id           text PRIMARY KEY,
    counterparty_id  text NOT NULL REFERENCES treasury.counterparties,
    currency         char(3) NOT NULL,
    iban             text NOT NULL,
    effective_from   date NOT NULL,
    effective_to     date,
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE TABLE treasury.fx_rates (
    rate_date  date NOT NULL,
    base       char(3) NOT NULL,
    quote      char(3) NOT NULL,
    rate       numeric(18,6) NOT NULL CHECK (rate > 0),
    PRIMARY KEY (rate_date, base, quote)
);

CREATE TABLE treasury.transactions (
    transaction_id    text NOT NULL,
    version           integer NOT NULL CHECK (version >= 1),
    is_current        boolean NOT NULL,
    entity_id         text NOT NULL REFERENCES treasury.entities,
    account_id        text NOT NULL REFERENCES treasury.accounts,
    counterparty_id   text NOT NULL REFERENCES treasury.counterparties,
    booking_date      date NOT NULL,
    value_date        date NOT NULL,
    amount            numeric(18,2) NOT NULL,
    currency          char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status            text NOT NULL CHECK (status IN ('booked', 'cancelled')),
    reference         text NOT NULL,
    remittance_text   text NOT NULL,
    beneficiary_iban  text,
    loaded_at         timestamptz NOT NULL,
    PRIMARY KEY (transaction_id, version)
);
-- Exactly one current version per transaction.
CREATE UNIQUE INDEX transactions_one_current ON treasury.transactions (transaction_id)
    WHERE is_current;
CREATE INDEX transactions_entity_date ON treasury.transactions (entity_id, booking_date);

CREATE TABLE treasury.payments (
    payment_id           text PRIMARY KEY,
    transaction_id       text NOT NULL,
    transaction_version  integer NOT NULL,
    entity_id            text NOT NULL REFERENCES treasury.entities,
    maker                text NOT NULL,
    checker              text NOT NULL,
    releaser             text NOT NULL,
    created_at           timestamptz NOT NULL,
    FOREIGN KEY (transaction_id, transaction_version) REFERENCES treasury.transactions,
    CHECK (maker <> checker AND maker <> releaser AND checker <> releaser)
);

CREATE TABLE treasury.daily_balances (
    account_id    text NOT NULL REFERENCES treasury.accounts,
    balance_date  date NOT NULL,
    closing       numeric(18,2) NOT NULL,
    currency      char(3) NOT NULL,
    PRIMARY KEY (account_id, balance_date)
);

CREATE TABLE docs.documents (
    document_id  text PRIMARY KEY,
    title        text NOT NULL,
    kind         text NOT NULL,
    body         text NOT NULL,
    tainted      boolean NOT NULL
);

-- Ground truth for evals. The copilot's database roles never get access to this schema.
CREATE TABLE eval.labels (
    kind       text NOT NULL,
    ref_table  text NOT NULL,
    ref_id     text NOT NULL,
    expected   text NOT NULL
);

CREATE TABLE eval.world_manifest (
    world_version  text NOT NULL,
    seed           integer NOT NULL,
    content_hash   text NOT NULL,
    manifest       jsonb NOT NULL
);
