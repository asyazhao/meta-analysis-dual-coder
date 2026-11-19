"""Web 控制台测试：密钥不回显、门禁、规则包、结果与修改意见写回。

所有测试都在临时仓库里跑：复制真实的 configs/models.yaml 与 rules/ 的一小块，
用 fixture 造一次运行的 results / revisions，绝不触碰真实仓库的 .env 或 data/。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from meta_coding.web import runs, store
from meta_coding.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
SECRETS = {
    "coder_a": "sk-test-ALPHA-9f3c2b7d1e",
    "coder_b": "sk-test-BRAVO-4a8e6c0d2f",
    "adjudicator": "sk-test-CHARLIE-7b1d5e9a3c",
}
RUN_ID = "run-20260101-000000-test"
DOC_TEXT = """# 编码规则（示例）

研究编号必须写成第一作者的姓加年份，例如 Doe2020。
样本量取基线分析的实际人数，随访人数不得替代基线人数。
均值与标准差取自同一张表、同一个时间点，不得跨列拼接。
缺失值统一记 -999，不参与任何均值或标准差计算。
缺均值或标准差的记录进入追踪表，不进入主表。
"""


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _record(source: str, fields: dict, pages: dict[str, int]) -> dict:
    provenance = {
        name: {"page": page, "table": "Table 2", "quote": f"{name} 出处 p.{page}"}
        for name, page in pages.items()
    }
    return {
        "source_file": source,
        "sheet": "WBIS",
        "block_id": "b01",
        "sex": 0,
        "fields": fields,
        "provenance": provenance,
        "record_status": "coded",
    }


def _make_run(root: Path, papers: Path) -> runs.RunInfo:
    run = runs.create_run(
        root,
        pack_id="demo-pack",
        pack_hash="deadbeef" * 8,
        papers_dir=str(papers),
        batch_id="batch01",
        config_hash="cfg-hash",
    )
    a_rows = [
        _record("6047.pdf", {"study_id": "6047", "sample_size": 120,
                             "mean_value": 3.54, "sd_value": 1.69},
                {"sample_size": 3, "mean_value": 7, "sd_value": 7}),
        _record("6127.pdf", {"study_id": "6127", "sample_size": 88,
                             "mean_value": 2.1, "sd_value": 0.8},
                {"sample_size": 2, "mean_value": 5, "sd_value": 5}),
    ]
    b_rows = [
        _record("6047.pdf", {"study_id": "6047", "sample_size": 121,
                             "mean_value": 3.45, "sd_value": 1.69},
                {"sample_size": 3, "mean_value": 7, "sd_value": 7}),
        _record("6127.pdf", {"study_id": "6127", "sample_size": 88,
                             "mean_value": 2.1, "sd_value": 0.8},
                {"sample_size": 2, "mean_value": 5, "sd_value": 5}),
    ]
    base = root / "runs" / run.run_id
    _write_jsonl(base / "results" / "coder_a.jsonl", a_rows)
    _write_jsonl(base / "results" / "coder_b.jsonl", b_rows)
    (base / "advice" / "revisions.md").write_text(
        """# 修改意见

## 6047.pdf · WBIS · Overall sample · mean_value
- A: 3.54  B: 3.45   差异类型：mismatch
- 建议：3.54（Table 2, p.7：「M = 3.54, SD = 1.69」）
- 理由：B 取到相邻列；A 与表格一致
- 置信度：0.92    严重度：critical

## 6047.pdf · WBIS · Overall sample · sample_size
- A: 120  B: 121   差异类型：mismatch
- 建议：120（Table 1, p.3：「N = 120」）
- 理由：B 多算了一名退出者
- 置信度：0.81    严重度：critical
""",
        encoding="utf-8",
    )
    return run


@pytest.fixture()
def sandbox(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    shutil.copy(REPO / "configs" / "pipeline.yaml", root / "configs" / "pipeline.yaml")
    shutil.copy(REPO / "configs" / "models.yaml", root / "configs" / "models.yaml")

    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "6047.pdf").write_bytes(b"%PDF-1.4 fake one")
    (papers / "6127.pdf").write_bytes(b"%PDF-1.4 fake two")

    (root / "docs").mkdir()
    (root / "docs" / "rule.md").write_text(DOC_TEXT, encoding="utf-8")

    text = (root / "configs" / "pipeline.yaml").read_text(encoding="utf-8")
    (root / "configs" / "pipeline.yaml").write_text(
        store.update_pipeline_text(text, {"input_dir": str(papers)}), encoding="utf-8"
    )

    env_lines = [f"{name}={value}" for name, value in (
        ("META_CODER_A_API_KEY", SECRETS["coder_a"]),
        ("META_CODER_B_API_KEY", SECRETS["coder_b"]),
        ("META_ADJUDICATOR_API_KEY", SECRETS["adjudicator"]),
    )]
    (root / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    run = _make_run(root, papers)
    app = create_app(root)
    return SimpleNamespace(root=root, papers=papers, run_id=run.run_id, app=app)


@pytest.fixture()
def client(sandbox: SimpleNamespace) -> TestClient:
    return TestClient(sandbox.app)


def test_home_renders_keys_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "接入" in response.text
    assert response.headers.get("x-frame-options") == "DENY"


def test_main_pages_render_without_secrets(client: TestClient) -> None:
    pages = ["/keys", "/rules", "/papers", "/run", "/results", "/advice", "/export"]
    for path in pages:
        response = client.get(path)
        assert response.status_code == 200, path
        for secret in SECRETS.values():
            assert secret not in response.text, f"{path} 泄漏了密钥"
    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["keys_set"] == {"coder_a": True, "coder_b": True, "adjudicator": True}
    for secret in SECRETS.values():
        assert secret not in state.text


def test_settings_write_env_without_echo(client: TestClient, sandbox: SimpleNamespace) -> None:
    fresh = "sk-test-DELTA-1c2f3a4b5d"
    response = client.post(
        "/api/settings",
        json={"budget_usd": 12.5, "concurrency": 3, "keys": {"coder_a": fresh}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["keys_set"]["coder_a"] is True
    assert fresh not in response.text

    env_text = (sandbox.root / ".env").read_text(encoding="utf-8")
    assert fresh in env_text
    assert SECRETS["coder_a"] not in env_text

    config_path = sandbox.root / "configs" / "pipeline.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["pipeline"]["budget_usd"] == 12.5
    assert config["pipeline"]["concurrency"] == 3

    page = client.get("/keys")
    assert fresh not in page.text
    assert "已设置" in page.text


def test_g2_gate_blocks_real_calls(client: TestClient, sandbox: SimpleNamespace) -> None:
    response = client.post(
        f"/api/runs/{sandbox.run_id}/steps/code_a", json={"dry_run": False}
    )
    assert response.status_code == 403
    assert "G2" in response.json()["detail"]

    dry = client.post(f"/api/runs/{sandbox.run_id}/steps/code_a", json={"dry_run": True})
    assert dry.status_code == 202
    assert dry.json()["dry_run"] is True

    assert client.post("/api/gates", json={"g2": True}).json()["gates"]["g2"] is True


def test_g3_gate_blocks_export(client: TestClient, sandbox: SimpleNamespace) -> None:
    response = client.post(f"/api/runs/{sandbox.run_id}/export")
    assert response.status_code == 403
    assert "G3" in response.json()["detail"]

    client.post("/api/gates", json={"g3": True})
    ok = client.post(f"/api/runs/{sandbox.run_id}/export")
    assert ok.status_code == 200
    assert ok.json()["entries"] >= 1
    manifest = sandbox.root / "runs" / sandbox.run_id / "exports" / "export_manifest.json"
    assert manifest.is_file()
    assert "run_id" in manifest.read_text(encoding="utf-8")


def test_unknown_job_404(client: TestClient) -> None:
    assert client.get("/api/jobs/job_does_not_exist").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_mock_rulepack_and_review_flow(client: TestClient, sandbox: SimpleNamespace) -> None:
    response = client.post(
        "/api/rules/parse",
        json={"doc_path": "docs/rule.md", "mode": "mock", "pack_id": "demo-pack"},
    )
    assert response.status_code == 200, response.text
    pack = response.json()["pack"]
    assert pack["mock"] is True
    assert 3 <= pack["field_count"] <= 5
    assert pack["status"] == "draft"

    pack_dir = sandbox.root / "rulepacks" / "demo-pack"
    for name in ("pack.json", "fields.yaml", "decisions.yaml",
                 "schema/record.schema.json", "template_contract.json"):
        assert (pack_dir / name).is_file(), name
    stored = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    assert stored["status"] == "draft"
    assert stored["pack_hash"]

    page = client.get("/rules?pack=demo-pack")
    assert page.status_code == 200
    assert "字段清单" in page.text
    assert "study_id" in page.text
    assert "离线示例" in page.text

    fields = response.json()["fields"]
    first = fields[0]["name"]
    edited = client.patch(
        f"/api/rules/demo-pack/fields/{first}",
        json={"quote": "研究编号必须写成第一作者的姓加年份。"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["pack"]["human_edits"] == 1

    added = client.post(
        "/api/rules/demo-pack/fields",
        json={"name": "extra_note", "type": "string", "missing_policy": "optional"},
    )
    assert added.status_code == 200
    assert added.json()["pack"]["field_count"] == pack["field_count"] + 1

    removed = client.delete("/api/rules/demo-pack/fields/extra_note")
    assert removed.status_code == 200
    assert removed.json()["pack"]["field_count"] == pack["field_count"]

    confirmed = client.post("/api/rules/demo-pack/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["pack"]["status"] == "confirmed"
    assert confirmed.json()["pack"]["pack_hash"]

    locked = client.patch(
        f"/api/rules/demo-pack/fields/{first}", json={"note": "不应生效"}
    )
    assert locked.status_code == 400
    assert "已确认" in locked.json()["detail"]


def test_rulepack_cannot_be_recreated(client: TestClient) -> None:
    payload = {"doc_path": "docs/rule.md", "mode": "mock", "pack_id": "dup-pack"}
    assert client.post("/api/rules/parse", json=payload).status_code == 200
    again = client.post("/api/rules/parse", json=payload)
    assert again.status_code == 400
    assert "已存在" in again.json()["detail"]


def test_rulepack_import_builtin(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    shutil.copy(REPO / "configs" / "pipeline.yaml", root / "configs" / "pipeline.yaml")
    shutil.copy(REPO / "configs" / "models.yaml", root / "configs" / "models.yaml")
    (root / "rules" / "schema").mkdir(parents=True)
    for name in ("fields.yaml", "decisions.yaml", "template_contract.json"):
        shutil.copy(REPO / "rules" / name, root / "rules" / name)
    shutil.copy(REPO / "rules" / "schema" / "record.schema.json",
                root / "rules" / "schema" / "record.schema.json")

    client = TestClient(create_app(root))
    response = client.post("/api/rules/import", json={"pack_id": "meta-wbi-v1"})
    assert response.status_code == 200, response.text
    assert response.json()["pack"]["field_count"] == 28
    assert len(response.json()["fields"]) == 28


def test_results_render_with_diff_highlight(client: TestClient, sandbox: SimpleNamespace) -> None:
    response = client.get(f"/results?run={sandbox.run_id}")
    assert response.status_code == 200
    text = response.text
    assert "mean_value" in text
    assert "3.54" in text
    assert "3.45" in text
    assert "critical" in text
    assert "cell diff critical" in text
    assert "P7" in text
    assert "6047.pdf · WBIS" in text

    payload = client.get(f"/api/runs/{sandbox.run_id}/progress").json()
    assert payload["total"] == 2
    assert payload["percent"]["a"] == 0


def test_diff_step_keeps_existing_run_results(
    client: TestClient, sandbox: SimpleNamespace
) -> None:
    """data/03_coded 为空时，比对不能把 run 工作区已有的 results 清空。"""
    response = client.post(f"/api/runs/{sandbox.run_id}/steps/diff")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "inline"
    assert body["diff"]["cells"] == 8
    assert body["diff"]["diff"] == 2
    assert body["run"]["steps"][3]["status"] == "done"

    cells_file = sandbox.root / "runs" / sandbox.run_id / "diff" / "cells.csv"
    assert cells_file.is_file()
    assert "mean_value" in cells_file.read_text(encoding="utf-8")
    assert "3.45" in (sandbox.root / "runs" / sandbox.run_id
                      / "results" / "coder_b.jsonl").read_text(encoding="utf-8")


def test_advice_accept_and_reject_write_back(client: TestClient, sandbox: SimpleNamespace) -> None:
    page = client.get(f"/advice?run={sandbox.run_id}")
    assert page.status_code == 200
    assert "修改意见" in page.text
    assert "mean_value" in page.text
    assert "Table 2" in page.text

    item_id = "6047.pdf|WBIS|Overall sample|mean_value"
    accepted = client.post(
        "/api/advice/decision",
        json={"run_id": sandbox.run_id, "item_id": item_id, "action": "accept",
              "value": "3.54", "note": "与表格一致"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["item"]["decision"] == "accepted"

    other = "6047.pdf|WBIS|Overall sample|sample_size"
    rejected = client.post(
        "/api/advice/decision",
        json={"run_id": sandbox.run_id, "item_id": other, "action": "reject"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["item"]["decision"] == "rejected"

    base = sandbox.root / "runs" / sandbox.run_id / "advice"
    decisions = json.loads((base / "decisions.json").read_text(encoding="utf-8"))
    assert decisions[item_id]["action"] == "accepted"
    assert decisions[item_id]["value"] == "3.54"
    assert decisions[other]["action"] == "rejected"
    provenance = (base / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(provenance) == 2
    assert json.loads(provenance[0])["actor"] == "human"

    again = client.get(f"/advice?run={sandbox.run_id}")
    assert "已采纳" in again.text
    assert "已驳回" in again.text


def test_advice_rejects_unknown_item(client: TestClient, sandbox: SimpleNamespace) -> None:
    response = client.post(
        "/api/advice/decision",
        json={"run_id": sandbox.run_id, "item_id": "不存在", "action": "accept"},
    )
    assert response.status_code == 400


def test_path_traversal_and_download_boundary(client: TestClient, sandbox: SimpleNamespace) -> None:
    assert client.get("/api/papers", params={"dir": "/etc"}).status_code == 403
    assert client.get("/files/configs/models.yaml").status_code == 404
    assert client.get("/files/.env").status_code == 404
    assert store.safe_download(sandbox.root, "../.env") is None
    assert store.safe_download(sandbox.root, ".env") is None

    allowed = store.safe_download(sandbox.root, f"runs/{sandbox.run_id}/run.json")
    assert allowed is not None and allowed.is_file()


def test_papers_listing_and_upload(client: TestClient, sandbox: SimpleNamespace) -> None:
    listing = client.get("/api/papers")
    assert listing.status_code == 200
    assert listing.json()["count"] == 2

    response = client.post(
        "/api/papers/upload",
        files={"files": ("new.pdf", b"%PDF-1.4 uploaded", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 3
    assert (sandbox.papers / "new.pdf").is_file()

    bad = client.post(
        "/api/papers/upload",
        files={"files": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert bad.json()["rejected"]


def test_settings_rejects_bad_batch_and_budget(client: TestClient) -> None:
    bad_budget = client.post(
        "/api/settings", json={"budget_usd": -1, "concurrency": 4, "keys": {}}
    )
    assert bad_budget.status_code == 400
    bad_concurrency = client.post(
        "/api/settings", json={"budget_usd": 10, "concurrency": 0, "keys": {}}
    )
    assert bad_concurrency.status_code == 400


def test_run_creation_requires_confirmed_pack(client: TestClient, sandbox: SimpleNamespace) -> None:
    response = client.post("/api/runs", json={"pack_id": "demo-pack"})
    assert response.status_code == 400

    client.post(
        "/api/rules/parse",
        json={"doc_path": "docs/rule.md", "mode": "mock", "pack_id": "demo-pack"},
    )
    unconfirmed = client.post("/api/runs", json={"pack_id": "demo-pack"})
    assert unconfirmed.status_code == 409
    assert "确认" in unconfirmed.json()["detail"]

    client.post("/api/rules/demo-pack/confirm")
    created = client.post("/api/runs", json={"pack_id": "demo-pack"})
    assert created.status_code == 200
    assert created.json()["run"]["rulepack"]["id"] == "demo-pack"
