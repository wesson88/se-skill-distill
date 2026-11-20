import hashlib
import json
from pathlib import Path
from conftest import load_script


def test_route_cli_uses_scoped_state_and_keeps_all_cards(isolated,tmp_path):
    paths=[]
    for index in range(2):
        path=tmp_path/f"draft{index}.json"
        path.write_text(json.dumps({"uuid":f"card-{index}","meta":{"name":f"流程{index}"}}),encoding="utf-8")
        paths.append(str(path))
    report=isolated.AuditResult("hash",paths[0],"技术主管",verdict="PASS",needs_human_review=False,
                                draft_paths=paths,draft_sha256={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths})
    isolated.APPROVED_FILE.parent.mkdir(parents=True,exist_ok=True)
    isolated.APPROVED_FILE.write_text(json.dumps(report.to_dict())+"\n",encoding="utf-8")
    route=load_script("03_route.py")
    assert route.main(["--dry-run"])==0
    rows=[json.loads(line) for line in (isolated.STATE_DIR/"routed.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows)==2
    assert len({row["new_path"] for row in rows})==2
    assert len({row["next_n"] for row in rows})==2
    assert all(row["audit"]["draft_sha256"] for row in rows)
