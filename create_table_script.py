import sys
sys.path.insert(0, '/app/src')
from sqlalchemy import create_engine, text

gen = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"
engine = create_engine(gen)

with engine.connect() as conn:
    conn.execute(text("""CREATE TABLE IF NOT EXISTS monitoring_jobs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        transaction_hash CHAR(64) NOT NULL UNIQUE,
        network VARCHAR(10) NOT NULL DEFAULT 'testnet',
        rpc_status VARCHAR(20) NOT NULL DEFAULT 'pending',
        monitoring_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
        raw_rpc_response JSONB NOT NULL,
        envelope_xdr TEXT,
        result_xdr TEXT,
        result_meta_xdr TEXT,
        diagnostic_events_xdr TEXT[] DEFAULT '{}',
        ledger INTEGER,
        created_at_ledger INTEGER,
        fee_bump BOOLEAN NOT NULL DEFAULT FALSE,
        failure_reason TEXT,
        locked_by VARCHAR(128),
        locked_until TIMESTAMPTZ,
        next_check_at TIMESTAMPTZ NOT NULL,
        attempt_count INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now() ON UPDATE now(),
        CHECK (monitoring_status IN ('ACTIVE', 'COMPLETED', 'OBSERVATION_EXPIRED')),
        CHECK (rpc_status IN ('SUCCESS', 'FAILED', 'NOT_FOUND')),
        UNIQUE (transaction_hash)
    )"""))
    conn.commit()
    print("Table created successfully")
