"""Download files from Kaggle, one at a time, with a checksum manifest.

Credentials come from ``.env`` (``KAGGLE_USERNAME`` and ``KAGGLE_KEY``), loaded by
``fincrime.config``. Files already downloaded with the expected size are skipped, so a long
download can be stopped and restarted. Competition data (IEEE-CIS) only downloads after the
rules have been accepted on the Kaggle website.
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from fincrime import config
from fincrime.ingest import manifest
from fincrime.ingest.sources import SOURCES, Source


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of one file."""

    source: str
    file: str
    path: Path
    bytes: int
    skipped: bool


class RulesNotAccepted(RuntimeError):
    """Kaggle refused the request: the competition rules have not been accepted."""


def select_files(
    source: Source,
    files: list[str] | None = None,
    all_files: bool = False,
    sizes: list[str] | None = None,
    kinds: list[str] | None = None,
) -> list[str]:
    """Which files of a source to fetch. Sizes and kinds only apply to AMLworld."""
    if files:
        unknown = [f for f in files if f not in source.files]
        if unknown:
            raise ValueError(f"not files of {source.key}: {unknown}")
        return list(files)
    if all_files:
        return list(source.files)
    if sizes or kinds:
        wanted_sizes = {s.title() for s in sizes} if sizes else {"Small", "Medium", "Large"}
        wanted_kinds = {k.upper() for k in kinds} if kinds else {"HI", "LI"}
        return [
            f
            for f in source.files
            if f.split("_")[0].split("-")[0] in wanted_kinds
            and f.split("_")[0].split("-")[1] in wanted_sizes
        ]
    return list(source.default_files)


def _api():
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    return api


def _remote_sizes(api, source: Source) -> dict[str, int]:
    if source.kind == "competition":
        listing = api.competition_list_files(source.ref)
    else:
        listing = api.dataset_list_files(source.ref)
    return {f.name: int(f.total_bytes) for f in listing.files}


def download(source_key: str, filename: str, force: bool = False, api=None) -> DownloadResult:
    """Fetch one file into ``data/raw/<source>/`` and log it in the manifest."""
    source = SOURCES[source_key]
    paths = config.get_paths().ensure()
    dest = paths.raw / source.key / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    api = api or _api()

    try:
        sizes = _remote_sizes(api, source)
    except Exception as err:
        if source.kind == "competition" and "401" in str(err):
            raise RulesNotAccepted(
                f"Kaggle returned 401 for {source.ref}: accept the rules at {source.url}/rules"
            ) from err
        raise
    expected = sizes.get(filename)
    known = {
        r.file: r
        for r in manifest.read(paths.manifest, manifest.RawRecord)
        if r.source == source.key
    }
    if (
        not force
        and dest.exists()
        and filename in known
        and expected in (None, dest.stat().st_size)
    ):
        return DownloadResult(source.key, filename, dest, dest.stat().st_size, True)

    part = dest.parent / ".part"
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir()
    if source.kind == "competition":
        api.competition_download_file(source.ref, filename, path=str(part), force=True, quiet=False)
    else:
        api.dataset_download_file(source.ref, filename, path=str(part), force=True, quiet=False)
    # Kaggle may URL-encode the name ("Variant%20I.csv") and may wrap big files in a zip
    for item in list(part.iterdir()):
        clean = unquote(item.name)
        if clean == f"{filename}.zip":
            with zipfile.ZipFile(item) as z:
                z.extract(filename, part)
            item.unlink()
        elif clean == filename and item.name != filename:
            item.rename(part / filename)
    got = part / filename
    if not got.exists():
        raise FileNotFoundError(
            f"Kaggle did not deliver {filename} (found {[p.name for p in part.iterdir()]})"
        )
    if expected is not None and got.stat().st_size != expected:
        raise OSError(f"{filename}: got {got.stat().st_size:,} bytes, Kaggle lists {expected:,}")
    dest.unlink(missing_ok=True)
    got.replace(dest)
    shutil.rmtree(part, ignore_errors=True)

    manifest.upsert(
        paths.manifest,
        manifest.RawRecord(
            source=source.key,
            file=filename,
            url=source.url,
            license=source.license,
            bytes=dest.stat().st_size,
            sha256=manifest.sha256_of(dest),
            downloaded_at=manifest.now(),
        ),
        key=("source", "file"),
    )
    return DownloadResult(source.key, filename, dest, dest.stat().st_size, False)


def raw_path(source_key: str, filename: str) -> Path:
    """Where a raw file lives (it may not exist yet)."""
    return config.get_paths().raw / source_key / filename
