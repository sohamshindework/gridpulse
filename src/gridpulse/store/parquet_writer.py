import json
from pathlib import Path
import duckdb


def write(rows,root):
    """Merge by source interval, publish partitioned Parquet via atomic replace."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    groups={}
    for row in rows:
        key=(row['region'],row['timestamp_utc'][:4],row['timestamp_utc'][5:7])
        groups.setdefault(key,[]).append(row)
    con=duckdb.connect()
    try:
        for (region,year,month),group in groups.items():
            path=root/f'region={region}'/f'year={year}'/f'month={month}'/'observations.parquet'
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.json.tmp');temp.write_text('\n'.join(json.dumps(r) for r in group))
            con.execute("CREATE OR REPLACE TEMP TABLE incoming AS SELECT * FROM read_json(?, columns={timestamp_utc: 'VARCHAR', region: 'VARCHAR', metric: 'VARCHAR', fuel: 'VARCHAR', value: 'DOUBLE', unit: 'VARCHAR', source: 'VARCHAR', fetched_at: 'VARCHAR'})",[str(temp)])
            if path.exists():
                con.execute('CREATE OR REPLACE TEMP TABLE merged AS SELECT * FROM incoming UNION ALL SELECT * FROM read_parquet(?)',[str(path)])
            else:
                con.execute('CREATE OR REPLACE TEMP TABLE merged AS SELECT * FROM incoming')
            dest=str(path)+'.tmp'
            # Paths are locally generated; parameters do not work for every COPY version.
            safe=dest.replace("'","''")
            con.execute(f"COPY (SELECT * FROM merged QUALIFY row_number() OVER (PARTITION BY timestamp_utc, region, metric, fuel, source ORDER BY fetched_at DESC)=1) TO '{safe}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            Path(dest).replace(path);temp.unlink()
    finally:
        con.close()


def read(root):
    files=list(Path(root).glob('region=*/year=*/month=*/*.parquet'))
    if not files:
        return []
    con=duckdb.connect()
    try:
        result=con.execute('SELECT * EXCLUDE (year, month) FROM read_parquet(?, hive_partitioning=true)',[[str(p) for p in files]])
        names=[column[0] for column in result.description]
        return [dict(zip(names,row)) for row in result.fetchall()]
    finally:
        con.close()
