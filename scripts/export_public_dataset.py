#!/usr/bin/env python3
"""Build a NEW distribution dataset from an explicit admissions-only allowlist.

This is an export, never a backup or a database copy. It excludes credentials,
sessions, free-text application payloads, raw responses and ingest metadata.
"""
import argparse
import gzip
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE applicants (id INTEGER PRIMARY KEY, applicant_id TEXT NOT NULL UNIQUE);
CREATE TABLE universities (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE programs (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE competitions_core (id INTEGER PRIMARY KEY, campaign_year INTEGER NOT NULL,
 source_path TEXT NOT NULL UNIQUE, file_name TEXT NOT NULL, program_id INTEGER,
 education_level TEXT, city TEXT, university_id INTEGER, study_form TEXT,
 quota_type TEXT, seats INTEGER, row_count INTEGER NOT NULL, imported_at TEXT NOT NULL);
CREATE TABLE applications (id INTEGER PRIMARY KEY, competition_id INTEGER NOT NULL,
 applicant_id INTEGER NOT NULL, ordinal INTEGER, competition_priority INTEGER,
 has_consent INTEGER NOT NULL, score_total REAL);
CREATE VIEW competitions AS SELECT c.id,c.campaign_year,c.source_path,c.file_name,
 p.name AS program,c.education_level,c.city,u.name AS university,c.study_form,
 c.quota_type,c.seats,c.row_count,c.imported_at FROM competitions_core c
 LEFT JOIN programs p ON p.id=c.program_id LEFT JOIN universities u ON u.id=c.university_id;
CREATE VIEW entries AS SELECT a.id,a.competition_id,ap.applicant_id,a.ordinal,
 a.competition_priority,a.has_consent,a.score_total FROM applications a
 JOIN applicants ap ON ap.id=a.applicant_id;
"""
TABLES = {"applicants", "universities", "programs", "competitions_core", "applications"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(source, output):
    if output.exists():
        raise ValueError("output already exists; refusing to overwrite")
    if source.resolve() == output.resolve():
        raise ValueError("source and output must differ")
    # Archived source only: immutable excludes sidecar creation and cannot merge a live WAL.
    if Path(str(source) + "-wal").exists():
        raise ValueError("source has WAL: use a verified quiescent archived source")
    with source.open("rb") as source_stream:
        if source_stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("not a SQLite file")
    source_hash = digest(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output, uri=True)) as target:
        target.execute("PRAGMA journal_mode=DELETE")
        target.execute("PRAGMA synchronous=NORMAL")
        target.execute("ATTACH DATABASE ? AS src", (source.resolve().as_uri() + "?mode=ro&immutable=1",))
        target.executescript(SCHEMA)
        target.execute("INSERT INTO universities SELECT id,name FROM src.universities")
        target.execute("INSERT INTO programs SELECT id,name FROM src.programs")
        target.execute("""INSERT INTO competitions_core
            SELECT c.id,ca.year,CAST(ca.year AS TEXT)||'/'||CAST(c.id AS TEXT),
            'public-snapshot',c.program_id,c.education_level,c.city,c.university_id,
            c.study_form,c.quota_type,c.seats,c.row_count,c.imported_at
            FROM src.competitions_core c JOIN src.campaigns ca ON ca.id=c.campaign_id
            WHERE ca.year IN (2025,2026)""")
        target.execute("""INSERT INTO applicants SELECT a.id,a.applicant_id FROM src.applicants a
            WHERE EXISTS (SELECT 1 FROM src.applications p JOIN competitions_core c
            ON c.id=p.competition_id WHERE p.applicant_id=a.id)""")
        target.execute("""INSERT INTO applications
            SELECT a.id,a.competition_id,a.applicant_id,a.ordinal,a.competition_priority,
            CASE WHEN a.has_consent=1 THEN 1 ELSE 0 END,a.score_total
            FROM src.applications a JOIN competitions_core c ON c.id=a.competition_id""")
        target.commit()
        target.executescript("""CREATE INDEX idx_applications_applicant_id ON applications(applicant_id);
            CREATE INDEX idx_applications_competition_ordinal ON applications(competition_id,ordinal);
            CREATE INDEX idx_competitions_year ON competitions_core(campaign_year);""")
        tables = {row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != TABLES:
            raise ValueError("unexpected exported table")
        if target.execute("PRAGMA freelist_count").fetchone()[0] != 0:
            raise ValueError("export has free pages")
        check = target.execute("PRAGMA integrity_check").fetchall()
        if check != [("ok",)]:
            raise ValueError("export integrity check failed")
        if target.execute("""SELECT COUNT(*) FROM applications a LEFT JOIN applicants p ON p.id=a.applicant_id
             WHERE p.id IS NULL""").fetchone()[0]:
            raise ValueError("orphaned applications")
        invalid = target.execute("""SELECT COUNT(*) FROM applicants WHERE
            (CASE WHEN applicant_id LIKE '2026:%' THEN substr(applicant_id,6) ELSE applicant_id END)
            GLOB '*[^0-9]*' OR length(applicant_id) NOT BETWEEN 1 AND 32""").fetchone()[0]
        if invalid:
            raise ValueError("unexpected applicant identifier format")
        counts = {table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in sorted(TABLES)}
        source_rows = target.execute("SELECT COUNT(*) FROM src.applications a JOIN competitions_core c ON c.id=a.competition_id").fetchone()[0]
        if source_rows != counts["applications"]:
            raise ValueError("application count differs from source")
        campaigns = [dict(zip(("year", "competitions", "applications"), row)) for row in target.execute(
            "SELECT campaign_year,COUNT(*),SUM(row_count) FROM competitions_core GROUP BY campaign_year")]
    if digest(source) != source_hash:
        raise ValueError("source changed during export; do not publish")
    return {"schema":"vvuzz-public-admissions.v1", "created_at":datetime.now(timezone.utc).isoformat(),
            "source_archive":source.name, "source_sha256":source_hash,
            "dataset":output.name, "dataset_sha256":digest(output), "bytes":output.stat().st_size,
            "tables":counts, "campaigns":campaigns, "integrity_check":"ok",
            "code_license":"MIT", "data_license":"Source rights retained; see docs/DATA.md",
            "excluded":["auth", "users", "sessions", "audit", "ingest", "raw", "free-text application fields"],
            "limitations":["Partial historical coverage; not current admissions data", "Identifiers are campaign-scoped, not names", "Not a guarantee of enrollment or a grant of third-party rights"]}


def self_test():
    with tempfile.TemporaryDirectory() as directory:
        source, output = (Path(directory) / name for name in ("source.sqlite", "public.sqlite"))
        with closing(sqlite3.connect(source)) as db:
            db.executescript("""CREATE TABLE users(password_text TEXT);INSERT INTO users VALUES('do-not-publish');
                CREATE TABLE campaigns(id INTEGER,year INTEGER);INSERT INTO campaigns VALUES(1,2026);
                CREATE TABLE applicants(id INTEGER,applicant_id TEXT);INSERT INTO applicants VALUES(1,'2026:700002');
                CREATE TABLE universities(id INTEGER,name TEXT);INSERT INTO universities VALUES(1,'Test');
                CREATE TABLE programs(id INTEGER,name TEXT);INSERT INTO programs VALUES(1,'Test');
                CREATE TABLE competitions_core(id INTEGER,campaign_id INTEGER,program_id INTEGER,education_level TEXT,
                city TEXT,university_id INTEGER,study_form TEXT,quota_type TEXT,seats INTEGER,row_count INTEGER,imported_at TEXT);
                INSERT INTO competitions_core VALUES(1,1,1,'bachelor','Test',1,'full-time','general',10,1,'2026-07-27');
                CREATE TABLE applications(id INTEGER,competition_id INTEGER,applicant_id INTEGER,ordinal INTEGER,
                competition_priority INTEGER,has_consent INTEGER,score_total REAL,raw_json TEXT);
                INSERT INTO applications VALUES(1,1,1,2,1,1,275,'private raw payload');""")
        manifest=export(source,output)
        assert manifest["tables"]["applications"]==1
        assert b"do-not-publish" not in output.read_bytes()
        assert b"private raw payload" not in output.read_bytes()
        try:
            export(source,output)
        except ValueError:
            pass
        else:
            raise AssertionError("overwrite must fail")
        print("Public export self-check: OK")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.source or not args.output:
        parser.error("--source and --output are required")
    manifest=export(args.source,args.output)
    manifest_path=args.output.with_suffix(".manifest.json")
    with manifest_path.open("x",encoding="utf-8") as stream:
        json.dump(manifest,stream,ensure_ascii=False,indent=2)
    compressed=args.output.with_suffix(args.output.suffix+".gz")
    with args.output.open("rb") as source, compressed.open("xb") as raw:
        with gzip.GzipFile(filename=args.output.name,mode="wb",fileobj=raw,compresslevel=6,mtime=0) as target:
            while chunk:=source.read(4<<20):
                target.write(chunk)
    print(json.dumps({"manifest":str(manifest_path),"compressed_bytes":compressed.stat().st_size,"compressed_sha256":digest(compressed),"tables":manifest["tables"]}))


if __name__=="__main__":
    main()
