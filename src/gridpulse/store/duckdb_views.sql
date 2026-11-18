-- Run from repository root in DuckDB.
CREATE VIEW observations AS
SELECT * FROM read_parquet('data/observations/region=*/year=*/month=*/*.parquet', hive_partitioning=true);
