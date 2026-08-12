"""Repositories — the only layer that writes SQL.

Routers call these; they never build queries themselves. That is what keeps the
data access in one place instead of spreading across request handlers the way
Redis calls did.
"""
