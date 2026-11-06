"""Test database URL - use Docker Compose PostgreSQL or fall back to localhost"""
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"