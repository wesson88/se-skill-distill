import json
from conftest import load_script


def test_standalone_ingest_failure_returns_nonzero(isolated,tmp_path,monkeypatch):
    ingest=load_script("01_ingest.py")
    source={"kind":"local_dir","path":"unused"}
    config=tmp_path/"sources.yaml"
    config.write_text(json.dumps({"sources":[source]}),encoding="utf-8")
    monkeypatch.setattr(ingest,"ingest_batch",lambda sources,console=None:[(source,[])])
    assert ingest.main(["--config",str(config)])==1
