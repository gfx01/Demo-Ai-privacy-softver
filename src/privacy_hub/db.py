"""Minimal async Postgres access shared by audit and settings.

One long-lived connection guarded by a lock (psycopg_pool is not available in
the pinned LiteLLM image and the audit insert rate of an SME is low). The
connection reconnects transparently after errors.
"""

from __future__ import annotations

import asyncio
from typing import Any

import psycopg


class Database:
    def __init__(self, url: str, connect_timeout_s: float = 3.0) -> None:
        self._url = url
        self._timeout = connect_timeout_s
        self._conn: psycopg.AsyncConnection | None = None
        self._lock = asyncio.Lock()

    async def _connection(self) -> psycopg.AsyncConnection:
        if self._conn is None or self._conn.closed:
            self._conn = await psycopg.AsyncConnection.connect(
                self._url, autocommit=True, connect_timeout=max(1, int(self._timeout))
            )
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> None:
        async with self._lock:
            try:
                conn = await self._connection()
                await conn.execute(sql, params)
            except psycopg.Error:
                await self._reset()
                raise

    async def fetchone(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> tuple | None:
        async with self._lock:
            try:
                conn = await self._connection()
                cur = await conn.execute(sql, params)
                return await cur.fetchone()
            except psycopg.Error:
                await self._reset()
                raise

    async def _reset(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except psycopg.Error:
                pass
        self._conn = None

    async def aclose(self) -> None:
        async with self._lock:
            await self._reset()
