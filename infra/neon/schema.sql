-- Relationship Manager Intelligence Platform — source schema
-- Every table carries updated_at, auto-maintained by a trigger, since the
-- CDC poller detects changes by updated_at > last_checkpoint.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationship_managers (
    rm_id           TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_rm_updated_at
BEFORE UPDATE ON relationship_managers
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    client_id               TEXT PRIMARY KEY,
    rm_id                   TEXT NOT NULL REFERENCES relationship_managers(rm_id),
    name                    TEXT NOT NULL,
    relationship_start_date DATE NOT NULL,
    profitability_score     NUMERIC(5, 2) NOT NULL, -- 0-100
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clients_rm_id ON clients(rm_id);

CREATE TRIGGER trg_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- Daily operating balance snapshot per client (source for deposit trend).
CREATE TABLE IF NOT EXISTS ledger_balances (
    id              BIGSERIAL PRIMARY KEY,
    client_id       TEXT NOT NULL REFERENCES clients(client_id),
    balance_date    DATE NOT NULL,
    operating_balance NUMERIC(14, 2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, balance_date)
);

CREATE INDEX IF NOT EXISTS idx_ledger_client_date ON ledger_balances(client_id, balance_date);
CREATE INDEX IF NOT EXISTS idx_ledger_updated_at ON ledger_balances(updated_at);

CREATE TRIGGER trg_ledger_updated_at
BEFORE UPDATE ON ledger_balances
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- Daily wire/ACH volume per client (source for wire migration signal).
CREATE TABLE IF NOT EXISTS wire_payment_logs (
    id              BIGSERIAL PRIMARY KEY,
    client_id       TEXT NOT NULL REFERENCES clients(client_id),
    log_date        DATE NOT NULL,
    wire_volume     NUMERIC(14, 2) NOT NULL DEFAULT 0,
    ach_volume      NUMERIC(14, 2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, log_date)
);

CREATE INDEX IF NOT EXISTS idx_wire_client_date ON wire_payment_logs(client_id, log_date);
CREATE INDEX IF NOT EXISTS idx_wire_updated_at ON wire_payment_logs(updated_at);

CREATE TRIGGER trg_wire_updated_at
BEFORE UPDATE ON wire_payment_logs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- RM-client touchpoints (source for "days since last interaction").
CREATE TABLE IF NOT EXISTS crm_interactions (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           TEXT NOT NULL REFERENCES clients(client_id),
    rm_id               TEXT NOT NULL REFERENCES relationship_managers(rm_id),
    interaction_date    DATE NOT NULL,
    interaction_type    TEXT NOT NULL, -- call, email, meeting, etc.
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_client_date ON crm_interactions(client_id, interaction_date);
CREATE INDEX IF NOT EXISTS idx_crm_updated_at ON crm_interactions(updated_at);

CREATE TRIGGER trg_crm_updated_at
BEFORE UPDATE ON crm_interactions
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- CDs and loans per client (source for the maturity-proximity churn trigger).
CREATE TABLE IF NOT EXISTS cd_loan_accounts (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           TEXT NOT NULL REFERENCES clients(client_id),
    account_type        TEXT NOT NULL, -- 'CD' or 'LOAN'
    principal_amount    NUMERIC(14, 2) NOT NULL,
    maturity_date       DATE,           -- NULL for open-ended loans
    status              TEXT NOT NULL DEFAULT 'active', -- active, matured, closed
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cd_loan_client ON cd_loan_accounts(client_id);
CREATE INDEX IF NOT EXISTS idx_cd_loan_updated_at ON cd_loan_accounts(updated_at);

CREATE TRIGGER trg_cd_loan_updated_at
BEFORE UPDATE ON cd_loan_accounts
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
