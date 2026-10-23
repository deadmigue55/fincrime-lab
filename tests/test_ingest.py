import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import duckdb
import polars as pl
import pytest

from fincrime.ingest import kaggle_client, manifest
from fincrime.ingest.convert import (
    AML_ACCOUNT_COLUMNS,
    AML_TRANS_COLUMNS,
    convert_file,
    csv_to_parquet,
    parse_patterns,
)
from fincrime.ingest.kaggle_client import RulesNotAccepted, select_files
from fincrime.ingest.sources import AMLWORLD, BAF, SOURCES

PATTERNS = """BEGIN LAUNDERING ATTEMPT - FAN-OUT:  Max 2-degree Fan-Out
2022/09/01 00:06,021174,800737690,012,80011F990,2848.96,Euro,2848.96,Euro,ACH,1
2022/09/01 04:33,021174,800737690,020,80020C5B0,8630.40,Euro,8630.40,Euro,ACH,1
END LAUNDERING ATTEMPT - FAN-OUT

BEGIN LAUNDERING ATTEMPT - CYCLE
2022/09/02 10:00,00220,8007A5B70,001244,80093C0D0,500.00,US Dollar,500.00,US Dollar,Cheque,1
END LAUNDERING ATTEMPT - CYCLE
"""

AML_HEADER = (
    "Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,"
    "Amount Paid,Payment Currency,Payment Format,Is Laundering\n"
)
AML_CSV = AML_HEADER + (
    "2022/09/01 00:20,010,8000EBD30,010,8000EBD30,3697.34,US Dollar,3697.34,US Dollar,"
    "Reinvestment,0\n"
    "2022/09/01 00:20,03208,8000F4580,001,8000F5340,0.01,US Dollar,0.01,US Dollar,Cheque,1\n"
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FCL_DATA_DIR", str(tmp_path))
    return tmp_path


def test_registry_is_consistent():
    assert set(SOURCES) == {"amlworld", "baf", "sparkov", "ieee_cis"}
    for s in SOURCES.values():
        assert s.license and s.publish_rule and s.url.startswith("https://")
        assert set(s.default_files) <= set(s.files)
        assert set(s.verified_rows) <= set(s.files)
        assert len(set(s.files)) == len(s.files)
    assert len(AMLWORLD.files) == 18  # 2 kinds x 3 sizes x (transactions, accounts, patterns)
    assert len(BAF.files) == 6
    assert "NC-ND" in BAF.license  # no derivatives: never publish converted copies


def test_select_files():
    assert select_files(AMLWORLD) == list(AMLWORLD.default_files)
    assert all("Large" not in f for f in AMLWORLD.default_files)
    large_hi = select_files(AMLWORLD, sizes=["large"], kinds=["hi"])
    assert large_hi == ["HI-Large_Trans.csv", "HI-Large_accounts.csv", "HI-Large_Patterns.txt"]
    assert select_files(BAF, files=["Base.csv"]) == ["Base.csv"]
    assert len(select_files(AMLWORLD, all_files=True)) == 18
    with pytest.raises(ValueError):
        select_files(BAF, files=["nope.csv"])


def test_parse_patterns_blocks_and_types():
    df = parse_patterns(PATTERNS)
    assert df.height == 3
    assert df["attempt_id"].to_list() == [1, 1, 2]
    assert df["pattern_type"].to_list() == ["FAN-OUT", "FAN-OUT", "CYCLE"]
    assert df["pattern_detail"][0] == "Max 2-degree Fan-Out" and df["pattern_detail"][2] == ""
    assert df["from_bank"][2] == "00220"  # leading zeros survive
    assert df.schema["ts"] == pl.Datetime("us") and df["is_laundering"].sum() == 3
    with pytest.raises(ValueError):
        parse_patterns("not a patterns file\n1,2,3")


def test_aml_csv_has_typed_columns_and_keeps_leading_zeros(tmp_path):
    src, out = tmp_path / "t.csv", tmp_path / "t.parquet"
    src.write_text(AML_CSV, encoding="utf-8")
    csv_to_parquet(src, out, AML_TRANS_COLUMNS, extra=", row_number() over () as txn_id")
    rows = duckdb.sql(f"select * from read_parquet('{out.as_posix()}') order by txn_id").fetchall()
    assert len(rows) == 2
    cols = [
        d[0]
        for d in duckdb.sql(f"describe select * from read_parquet('{out.as_posix()}')").fetchall()
    ]
    assert cols[0] == "ts" and "txn_id" in cols and cols.count("from_account") == 1
    assert rows[1][1] == "03208" and rows[1][10] == 1  # bank code string, label
    assert str(rows[0][0]) == "2022-09-01 00:20:00"
    assert len(AML_ACCOUNT_COLUMNS) == 5


def test_manifest_roundtrip_upsert_and_checksum(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert (
        manifest.sha256_of(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    log = tmp_path / "M.csv"
    rec = manifest.RawRecord("s", "a", "u", "l", 5, "abc", "t1")
    manifest.upsert(log, rec, key=("source", "file"))
    manifest.upsert(
        log, manifest.RawRecord("s", "a", "u", "l", 7, "def", "t2"), key=("source", "file")
    )
    manifest.upsert(
        log, manifest.RawRecord("s", "b", "u", "l", 1, "ghi", "t3"), key=("source", "file")
    )
    rows = manifest.read(log, manifest.RawRecord)
    assert [(r.file, r.bytes) for r in rows] == [
        ("a", 7),
        ("b", 1),
    ]  # same key replaced, int parsed


def test_convert_is_idempotent_and_flags_row_count_mismatch(data_dir):
    raw = data_dir / "raw" / "baf"
    raw.mkdir(parents=True)
    (raw / "Base.csv").write_text("fraud_bool,month\n0,0\n1,1\n0,2\n", encoding="utf-8")
    first = convert_file("baf", "Base.csv")
    assert (
        first.rows == 3 and "MISMATCH" in first.expected_rows
    )  # BAF files must have 1,000,000 rows
    assert convert_file("baf", "Base.csv") is None  # already converted
    assert convert_file("baf", "Base.csv", force=True).rows == 3
    out = pl.read_parquet(data_dir / "processed" / "baf" / "Base.parquet")
    assert out["variant"].to_list() == ["Base"] * 3
    with pytest.raises(FileNotFoundError):
        convert_file("baf", "Variant I.csv")


class FakeApi:
    """Stands in for the Kaggle client: serves files from memory, as Kaggle does."""

    def __init__(self, files, zipped=False, encode=False, bad_size=False, deny=False):
        self.files, self.zipped, self.encode, self.bad_size, self.deny = (
            files,
            zipped,
            encode,
            bad_size,
            deny,
        )
        self.calls = 0

    def _list(self):
        if self.deny:
            raise RuntimeError("401 Client Error: Unauthorized")
        size = lambda b: len(b) + (5 if self.bad_size else 0)  # noqa: E731
        return SimpleNamespace(
            files=[SimpleNamespace(name=n, total_bytes=size(b)) for n, b in self.files.items()]
        )

    dataset_list_files = competition_list_files = lambda self, ref: self._list()

    def _fetch(self, ref, name, path, force, quiet):
        self.calls += 1
        shown = name.replace(" ", "%20") if self.encode else name
        target = Path(path) / (shown + (".zip" if self.zipped else ""))
        if self.zipped:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(name, self.files[name])
            target.write_bytes(buf.getvalue())
        else:
            target.write_bytes(self.files[name])

    dataset_download_file = competition_download_file = _fetch


@pytest.mark.parametrize(
    "zipped,encode", [(False, False), (True, False), (True, True), (False, True)]
)
def test_download_handles_zips_and_encoded_names_and_skips_repeats(data_dir, zipped, encode):
    api = FakeApi({"Variant I.csv": b"a,b\n1,2\n"}, zipped=zipped, encode=encode)
    r = kaggle_client.download("baf", "Variant I.csv", api=api)
    assert not r.skipped and r.path.read_bytes() == b"a,b\n1,2\n"
    assert not (r.path.parent / ".part").exists()
    rec = manifest.read(data_dir / "raw" / "MANIFEST.csv", manifest.RawRecord)[0]
    assert rec.file == "Variant I.csv" and rec.bytes == 8 and rec.license == BAF.license
    assert kaggle_client.download("baf", "Variant I.csv", api=api).skipped
    assert api.calls == 1
    assert not kaggle_client.download("baf", "Variant I.csv", api=api, force=True).skipped


def test_download_rejects_wrong_size_and_explains_unaccepted_rules(data_dir):
    with pytest.raises(OSError, match="bytes"):
        kaggle_client.download("baf", "Base.csv", api=FakeApi({"Base.csv": b"abc"}, bad_size=True))
    with pytest.raises(RulesNotAccepted, match="rules"):
        kaggle_client.download("ieee_cis", "train_identity.csv", api=FakeApi({}, deny=True))


def test_report_summarises_converted_files(data_dir):
    from fincrime.ingest.report import render_markdown

    base = data_dir / "processed"
    (base / "amlworld").mkdir(parents=True)
    pl.DataFrame(
        {
            "ts": [
                "2022-09-01 00:20:00",
                "2022-09-02 10:00:00",
                "2022-09-03 10:00:00",
                "2022-09-04 10:00:00",
            ],
            "from_account": ["a", "b", "c", "d"],
            "is_laundering": [0, 1, 1, 0],
        }
    ).with_columns(pl.col("ts").str.to_datetime()).write_parquet(
        base / "amlworld" / "HI-Small_Trans.parquet"
    )
    parse_patterns(PATTERNS).write_parquet(base / "amlworld" / "HI-Small_Patterns.parquet")
    text = render_markdown(base)
    assert "| HI-Small | 4 | 2 | 50.0000% | 2022-09-01 to 2022-09-04 | 3 | 150% | 2 |" in text
    assert text.count("Not downloaded yet") == 3  # BAF, Sparkov and IEEE-CIS are absent
    assert "CDLA-Sharing-1.0" in text and "CC BY-NC-ND 4.0" in text
