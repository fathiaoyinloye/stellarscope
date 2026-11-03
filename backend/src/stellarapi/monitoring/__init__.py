"""StellarScope monitoring orchestration package.

Contains the application service, jobclaimer, and background worker for
coordinating Stellar transaction monitoring checks.

All modules in this package follow the approved design decisions:
- RPC communication separate from database operations
- Monitoring lifecycle separate from transaction status
- PostgreSQL-based worker coordination (no Redis/Celery)
- Best-effort XDR decoding with raw evidence preservation
"""