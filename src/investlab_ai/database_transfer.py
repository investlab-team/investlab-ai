"""One-off, bounded-memory transfer without DART/OpenAI calls or source writes."""
import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, LargeBinary, func, select

from investlab_ai.storage import Base

# Ephemeral analysis locks are deliberately not carried to a different DB.
TABLES = tuple(table for table in Base.metadata.sorted_tables
               if table.name != "rag_analysis_leases")


class TransferError(RuntimeError):
    pass


def row_digest(table, row) -> bytes:
    values = []
    for column in table.columns:
        value = row[column.name]
        if value is not None:
            if isinstance(column.type, LargeBinary):
                value = hashlib.sha256(value).hexdigest()
            elif isinstance(column.type, DateTime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=UTC)
                value = value.astimezone(UTC).isoformat()
        values.append(value)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).digest()


def normalize_row(table, row):
    result = dict(row)
    for column in table.columns:
        value = result[column.name]
        if isinstance(column.type, DateTime) and value is not None and value.tzinfo is None:
            result[column.name] = value.replace(tzinfo=UTC)
        if isinstance(column.type, JSON) and value is not None:
            # Fail before modifying the target if JSON cannot be represented in PostgreSQL.
            json.dumps(value, allow_nan=False)
    return result


def table_fingerprint(connection, table):
    total, checksum = 0, hashlib.sha256()
    buffer = 1 if any(isinstance(c.type, LargeBinary) for c in table.columns) else 1000
    query = select(table).order_by(*table.primary_key.columns).execution_options(yield_per=buffer)
    for row in connection.execute(query).mappings():
        checksum.update(row_digest(table, row))
        total += 1
    return {"rows": total, "sha256": checksum.hexdigest()}


def transfer(source_engine, target_engine, emit=lambda item: None, batch_size=1000):
    """Require empty/equivalent target tables; commit and verify each table, safe to rerun.

    Keep the source app stopped. SQLite transaction gives a consistent read snapshot.
    Already copied identical tables are verified and skipped; differing target data is
    never overwritten. A failure in a table rolls back that table's writes.
    """
    if source_engine.url == target_engine.url:
        raise TransferError("SOURCE_EQUALS_TARGET")
    report = {"verifiedAt": datetime.now(UTC).isoformat(), "tables": {}}
    with source_engine.connect() as source:
        if source.dialect.name == "sqlite":
            source.exec_driver_sql("BEGIN")
        for table in TABLES:
            with target_engine.begin() as target:
                target_count = target.scalar(select(func.count()).select_from(table))
                if target_count:
                    expected = table_fingerprint(source, table)
                    actual = table_fingerprint(target, table)
                    if actual != expected:
                        raise TransferError(f"TARGET_TABLE_DIFFERS:{table.name}")
                    report["tables"][table.name] = {**actual, "reused": True}
                    emit({"table": table.name, **report["tables"][table.name]})
                    continue
                buffer = 1 if any(isinstance(c.type, LargeBinary) for c in table.columns) else batch_size
                query = select(table).order_by(*table.primary_key.columns).execution_options(yield_per=buffer)
                rows = source.execute(query).mappings()
                checksum, copied = hashlib.sha256(), 0
                if target.dialect.name == "postgresql":
                    # Psycopg COPY avoids a million individual INSERTs and stays bounded.
                    from psycopg import sql
                    from psycopg.types.json import Jsonb
                    columns = list(table.columns)
                    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                        sql.Identifier(table.name),
                        sql.SQL(", ").join(sql.Identifier(c.name) for c in columns),
                    )
                    with target.connection.driver_connection.cursor() as cursor:
                        with cursor.copy(statement) as copy:
                            for row in rows:
                                row = normalize_row(table, row)
                                checksum.update(row_digest(table, row))
                                copy.write_row([Jsonb(row[c.name]) if isinstance(c.type, JSON)
                                                and row[c.name] is not None else row[c.name]
                                                for c in columns])
                                copied += 1
                                if copied % 50000 == 0:
                                    emit({"table": table.name, "copied": copied})
                else:
                    batch = []
                    for row in rows:
                        row = normalize_row(table, row)
                        checksum.update(row_digest(table, row))
                        batch.append(row)
                        copied += 1
                        if len(batch) >= batch_size:
                            target.execute(table.insert(), batch)
                            batch.clear()
                    if batch:
                        target.execute(table.insert(), batch)
                actual = table_fingerprint(target, table)
                if actual != {"rows": copied, "sha256": checksum.hexdigest()}:
                    raise TransferError(f"VERIFICATION_FAILED:{table.name}")
                report["tables"][table.name] = {**actual, "reused": False}
            emit({"table": table.name, **report["tables"][table.name]})
        source.rollback()
    return report
