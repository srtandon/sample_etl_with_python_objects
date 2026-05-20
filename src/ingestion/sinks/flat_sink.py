"""
FlatDuckDBSink: writes a FlatRecord to Parquet via DuckDB.

Because FlatRecord has a dynamic, schema-inferred field set (unlike the fixed
TrialRecord columns in DuckDBSink), this sink writes through a temporary JSON
file and lets DuckDB infer the column types automatically.  The output can
then be queried with `read_parquet('output/source_name/*.parquet')`.

One Parquet file is written per record, named by record_id.  Multiple records
from the same source are stored in the same sub-directory so DuckDB can
wildcard-query them:

    output/
      nba_games/
        G2024_NBA_001.parquet
        G2024_NBA_002.parquet
        ...

Note: unlike DuckDBSink, FlatDuckDBSink does not satisfy the Processor
protocol because its run() method takes a FlatRecord, not a TrialRecord.
Call it directly after flatten_to_record() rather than passing it to ImportJob.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ingestion.flattener import FlatRecord

logger = logging.getLogger(__name__)


class FlatDuckDBSink:
    """
    Writes a FlatRecord to a Parquet file using DuckDB.

    The Parquet schema is inferred from the FlatRecord's fields dict, so
    no schema must be declared in advance — the same sink handles basketball,
    football, or any other sport whose API returns different field names.

    Args:
        output_dir: Root directory for output.  Sub-directories are created
                    per ``record.source``.  Defaults to ``output/``.
    """

    def __init__(self, output_dir: str | Path = "output"):
        self.output_dir = Path(output_dir)

    def write(self, record: FlatRecord) -> dict:
        """
        Persist *record* to Parquet and return a write summary.

        The row written to Parquet is the FlatRecord's fields dict plus
        ``record_id``, ``source``, and ``ingested_at`` columns for
        provenance tracking.
        """
        try:
            import duckdb
        except ImportError as exc:
            raise ImportError(
                "duckdb is required for FlatDuckDBSink.  "
                "Install it with: pip install duckdb"
            ) from exc

        source_dir = self.output_dir / record.source
        source_dir.mkdir(parents=True, exist_ok=True)

        row = {
            "record_id":  record.record_id,
            "source":     record.source,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            **record.fields,
        }

        parquet_path = str(source_dir / f"{record.record_id}.parquet")

        # DuckDB infers the schema from the JSON array.
        # Temp file avoids inline quoting issues with complex field values.
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        try:
            json.dump([row], tmp)
            tmp.flush()
            tmp.close()

            con = duckdb.connect()
            con.execute(
                f"COPY (SELECT * FROM read_json_auto('{tmp.name}')) "
                f"TO '{parquet_path}' (FORMAT PARQUET)"
            )
            con.close()
        finally:
            Path(tmp.name).unlink(missing_ok=True)

        summary = {
            "record_id":    record.record_id,
            "source":       record.source,
            "fields_written": len(record.fields),
            "path":         parquet_path,
        }
        logger.info(
            f"{record.source}/{record.record_id}: "
            f"wrote {summary['fields_written']} fields → {parquet_path}"
        )
        return summary
