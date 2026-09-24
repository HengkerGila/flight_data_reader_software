from arinc717_reader.dataframe.adb_codec import parse_adb_text
from arinc717_reader.dataframe.compare import dataframe_differences
from arinc717_reader.dataframe.repository import DataframeRepository
from arinc717_reader.dataframe.validator import ValidationIssue, validate_dataframe

from .test_adb_roundtrip import FIXTURE, HEADER, afda_row


def test_demo_save_load_roundtrip(demo_dataframe):
    repo = DataframeRepository(":memory:")
    document_id = repo.save_dataframe(demo_dataframe)
    loaded = repo.load_dataframe(document_id)
    assert dataframe_differences(demo_dataframe, loaded) == []
    assert loaded.metadata.wps == 256
    assert loaded.metadata.sync_words == [583, 1464, 2631, 3512]
    repo.close()


def test_adb_parsed_save_load_preserves_raw_records():
    trailing = afda_row("SPARE", "with trailing fields", "Unsigned Analog", "", "0", "4095",
                        "1", "0", "0", "-", locations=[("1234", 40, 1, 12)])
    text = FIXTURE + trailing + ",TRAILA,TRAILB\r\n"
    dataframe = parse_adb_text(text, source_filename="sample.adb")
    repo = DataframeRepository(":memory:")
    document_id = repo.save_dataframe(dataframe, status="PUBLISHED")
    loaded = repo.load_dataframe(document_id)
    assert dataframe_differences(dataframe, loaded) == []
    assert loaded.metadata.adb_settings_raw == dataframe.metadata.adb_settings_raw
    assert (
        loaded.parameters[0].provenance.raw_record
        == dataframe.parameters[0].provenance.raw_record
    )
    assert loaded.parameters[-1].provenance.extra["trailing_fields"] == [
        "TRAILA",
        "TRAILB",
    ]
    # the state table and BCD digit weights survive the database too
    mode = next(p for p in loaded.parameters if p.mnemonic == "MODE SEL")
    assert [(s.value, s.label) for s in mode.states] == [(0, "OFF"), (1, "LOW"), (2, "MID"), (3, "HIGH")]
    year = next(p for p in loaded.parameters if p.mnemonic == "01 YEAR")
    assert sorted(s.bcd_weight for s in year.occurrences[0].segments) == [1.0, 10.0]
    repo.close()


def test_list_documents_and_issues(demo_dataframe):
    repo = DataframeRepository(":memory:")
    document_id = repo.save_dataframe(demo_dataframe)
    documents = repo.list_documents()
    assert len(documents) == 1
    assert documents[0]["name"] == "DEMO_256WPS"
    issues = validate_dataframe(demo_dataframe)
    repo.save_validation_issues(document_id, issues)
    repo.save_validation_issues(document_id, issues)  # idempotent overwrite
    repo.close()


def test_file_backed_repository(tmp_path, demo_dataframe):
    path = tmp_path / "internal.sqlite"
    repo = DataframeRepository(path)
    document_id = repo.save_dataframe(demo_dataframe)
    repo.close()

    reopened = DataframeRepository(path)
    loaded = reopened.load_dataframe(document_id)
    assert dataframe_differences(demo_dataframe, loaded) == []
    reopened.close()
