"""本机 Web 控制台：通用编码工具的外壳。

启动：``python -m meta_coding.web.app --port 8765``

页面顺序：``/keys`` → ``/rules`` → ``/papers`` → ``/run`` → ``/results`` →
``/advice`` → ``/export``。

安全约定（写死在代码里，不靠用户记得）：
* 只监听 127.0.0.1，``--host`` 只接受回环地址；
* 密钥只写 ``.env``，任何响应都不回显真值，日志在入队时脱敏；
* 目录/文档浏览限定在仓库或用户显式配置的论文目录内；下载只放开产物目录；
* G2 未勾选时真实模型调用一律 403（只允许 dry-run）；G3 未勾选时不允许生成导出清单。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..paths import repo_root
from . import rulepacks, runs, store
from .jobs import JobManager

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_UPLOAD_BYTES = 60 * 1024 * 1024

STEPS = [
    ("keys", "①", "密钥", "/keys"),
    ("rules", "②", "规则", "/rules"),
    ("papers", "③", "论文", "/papers"),
    ("run", "④", "运行", "/run"),
    ("results", "⑤", "结果", "/results"),
    ("advice", "⑥", "修改意见", "/advice"),
    ("export", "⑦", "导出", "/export"),
]

GATE_META = {
    "g2": {
        "label": "G2 真实调用",
        "detail": "勾选后才允许真实调用模型；未勾选只能跑 dry-run。",
    },
    "g3": {
        "label": "G3 导出",
        "detail": "勾选后才允许生成对外导出清单。",
    },
}


# --------------------------------------------------------------------------- #
# 门禁、缓存与请求模型
# --------------------------------------------------------------------------- #


@dataclass
class Gates:
    g2: bool = False
    g3: bool = False

    def snapshot(self) -> dict[str, bool]:
        return {"g2": self.g2, "g3": self.g3}


class TTLCache:
    """页面导航用的轻量缓存：产物没变就别每页重算。"""

    def __init__(self, ttl: float = 2.0) -> None:
        self.ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or time.monotonic() - item[0] > self.ttl:
                return None
            return item[1]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class SettingsIn(BaseModel):
    budget_usd: float = 30
    concurrency: int = 4
    keys: dict[str, str] = Field(default_factory=dict)


class ParseIn(BaseModel):
    doc_path: str
    mode: str = "mock"
    pack_id: str | None = None


class ImportIn(BaseModel):
    pack_id: str = "meta-wbi-v1"


class FieldIn(BaseModel):
    name: str | None = None
    type: str | None = None
    allowed: Any = None
    labels: Any = None
    missing_policy: str | None = None
    required_when: str | None = None
    chapter: str | None = None
    quote: str | None = None
    note: str | None = None


class DirIn(BaseModel):
    input_dir: str


class RunCreateIn(BaseModel):
    pack_id: str


class StepIn(BaseModel):
    dry_run: bool = False


class DecisionIn(BaseModel):
    run_id: str
    item_id: str
    action: str
    value: str = ""
    note: str = ""


class GatesIn(BaseModel):
    g2: bool | None = None
    g3: bool | None = None


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    text = str(value).strip()
    return text if text else "—"


def _pick_model_slot(cfg) -> Any:
    if cfg is None:
        return None
    for slot in (cfg.models.adjudicator, cfg.models.coder_a, cfg.models.coder_b):
        if slot.bound:
            return slot
    return None


def _papers_table(root: Path, cfg) -> tuple[list[store.PdfEntry], list[dict[str, Any]], str | None]:
    directory = store.resolve_input_dir(root, cfg)
    if directory is None:
        return [], [], "尚未设置论文目录"
    pdfs, _, error = store.list_pdfs(directory)
    corpus = store.read_corpus(root)
    rows: list[dict[str, Any]] = []
    for item in corpus:
        article_id = store.sha16(str(item.get("article_key") or ""))
        packet = store.read_json(root / "data" / "02_ingest" / article_id / "packet.json") or {}
        biblio = packet.get("biblio") if isinstance(packet, dict) else {}
        biblio = biblio if isinstance(biblio, dict) else {}
        rows.append(
            {
                "source_file": str(item.get("source_file") or ""),
                "title": str(biblio.get("title_guess") or item.get("title_guess") or "—"),
                "year": str(item.get("year_guess") or "—"),
                "pages": str(item.get("pages") or "—"),
                "status": str(item.get("status") or "—"),
                "scanned": str(item.get("is_scanned") or ""),
            }
        )
    return pdfs, rows, error


def _selected(values: list[Any], key: str, wanted: str | None) -> Any:
    if wanted:
        match = next((item for item in values if str(getattr(item, key, "")) == wanted), None)
        if match is not None:
            return match
    return values[0] if values else None


def _run_progress_payload(root: Path, run_id: str, jobs: JobManager) -> dict[str, Any]:
    try:
        progress = runs.run_progress(root, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    active = [
        job.summary() for job in jobs.running()
        if str(job.stage).startswith(f"run:{run_id}:")
    ]
    progress["active_jobs"] = active
    return progress


def _fields_of_pack(root: Path, pack_id: str) -> list[str]:
    try:
        return [str(item.get("name") or "") for item in rulepacks.read_fields(root, pack_id)]
    except rulepacks.RulePackError:
        return []


# --------------------------------------------------------------------------- #
# 应用工厂
# --------------------------------------------------------------------------- #


def create_app(root: Path | None = None) -> FastAPI:
    repo = Path(root).resolve() if root is not None else repo_root()
    app = FastAPI(title="meta-coding 控制台", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = repo
    app.state.gates = Gates()
    app.state.scrubber = store.SecretScrubber(repo / ".env")
    app.state.jobs = JobManager(scrub=app.state.scrubber.scrub)
    app.state.cache = TTLCache(ttl=2.0)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    # ---- 中间件：脱敏 + 安全响应头 ---------------------------------- #
    @app.middleware("http")
    async def _redact(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/files/"):
            return response
        body = getattr(response, "body", None)
        if body is None:
            return response
        text = body.decode("utf-8", "ignore")
        cleaned = app.state.scrubber.scrub(text)
        if cleaned == text:
            return response
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(
            content=cleaned.encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        if request.url.path.startswith(("/api/", "/files/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # 不回显请求体，避免把密钥之类的输入原样弹回页面
        return JSONResponse({"detail": "请求参数不合法"}, status_code=400)

    # ---- 上下文 ------------------------------------------------------ #
    def _snapshot(app: FastAPI) -> dict[str, Any]:
        cached = app.state.cache.get("snapshot")
        if cached is not None:
            return cached
        packs = rulepacks.list_packs(repo)
        run_list = runs.list_runs(repo)
        cfg, _ = store.load_config_safe(repo)
        pdfs, corpus, _ = _papers_table(repo, cfg)
        data = {
            "packs": len(packs),
            "packs_confirmed": sum(1 for item in packs if item.confirmed),
            "pdfs": len(pdfs),
            "corpus": len(corpus),
            "runs": len(run_list),
            "latest_run": run_list[0].run_id if run_list else "",
        }
        app.state.cache.put("snapshot", data)
        return data

    def base_context(request: Request) -> dict[str, Any]:
        cfg, cfg_error = store.load_config_safe(repo)
        env_names = store.slot_env_names(repo)
        slots = []
        if cfg is not None:
            for slot in cfg.models.slots():
                slots.append(
                    {
                        "name": slot.name,
                        "label": store.SLOT_LABELS.get(slot.name, slot.name),
                        "provider": slot.provider,
                        "family": slot.family,
                        "model": slot.model,
                        "bound": slot.bound,
                        "env": env_names.get(slot.name, ""),
                        "set": store.key_status(repo).get(slot.name, False),
                    }
                )
        return {
            "request": request,
            "root": str(repo),
            "steps": STEPS,
            "batch": cfg.pipeline.batch_id if cfg else "—",
            "config_hash": cfg.config_hash if cfg else "",
            "cfg_error": cfg_error,
            "cfg": cfg,
            "gates": app.state.gates.snapshot(),
            "gate_meta": GATE_META,
            "keys": store.key_status(repo),
            "slots": slots,
            "snapshot": _snapshot(app),
            "jobs": app.state.jobs.recent(8),
        }

    def render(request: Request, name: str, active: str, **extra: Any) -> HTMLResponse:
        context = base_context(request)
        context["active"] = active
        context.update(extra)
        return templates.TemplateResponse(request, name, context)

    # ---- 页面 -------------------------------------------------------- #
    def _render_keys(request: Request) -> HTMLResponse:
        cfg, cfg_error = store.load_config_safe(repo)
        return render(
            request, "keys.html", "keys",
            cfg=cfg,
            cfg_error=cfg_error,
            pipeline=cfg.pipeline if cfg else None,
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return _render_keys(request)

    @app.get("/keys", response_class=HTMLResponse)
    def page_keys(request: Request):
        return _render_keys(request)

    @app.get("/rules", response_class=HTMLResponse)
    def page_rules(request: Request, pack: str | None = None):
        cfg, cfg_error = store.load_config_safe(repo)
        input_dir = store.resolve_input_dir(repo, cfg)
        packs = rulepacks.list_packs(repo)
        selected = _selected(packs, "pack_id", pack)
        fields: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        if selected is not None:
            try:
                fields = rulepacks.read_fields(repo, selected.pack_id)
                decisions = rulepacks.read_decisions(repo, selected.pack_id)
            except rulepacks.RulePackError:
                selected = None
        choices = store.source_document_choices(repo, input_dir)
        default_doc = ""
        for candidate in choices:
            if candidate["path"].endswith("CODING_PLAN.md"):
                default_doc = candidate["path"]
                break
        if not default_doc and choices:
            default_doc = choices[0]["path"]
        return render(
            request, "rules.html", "rules",
            cfg=cfg,
            cfg_error=cfg_error,
            packs=[item.as_dict() for item in packs],
            pack=selected.as_dict() if selected else None,
            fields=fields,
            decisions=decisions,
            choices=choices,
            default_doc=default_doc,
            type_choices=rulepacks.TYPE_CHOICES,
            missing_choices=rulepacks.MISSING_CHOICES,
            builtin_exists=(repo / "rules" / "fields.yaml").is_file(),
        )

    @app.get("/papers", response_class=HTMLResponse)
    def page_papers(request: Request):
        cfg, cfg_error = store.load_config_safe(repo)
        pdfs, corpus_rows, listing_error = _papers_table(repo, cfg)
        input_dir = store.resolve_input_dir(repo, cfg)
        return render(
            request, "papers.html", "papers",
            cfg=cfg,
            cfg_error=cfg_error,
            pdfs=pdfs,
            corpus_rows=corpus_rows,
            listing_error=listing_error,
            input_dir=str(input_dir) if input_dir else "",
        )

    @app.get("/run", response_class=HTMLResponse)
    def page_run(request: Request, run: str | None = None):
        cfg, cfg_error = store.load_config_safe(repo)
        pack_list = rulepacks.list_packs(repo)
        run_list = runs.list_runs(repo)
        selected = _selected(run_list, "run_id", run)
        progress = None
        if selected is not None:
            try:
                progress = _run_progress_payload(repo, selected.run_id, app.state.jobs)
            except HTTPException:
                progress = None
        return render(
            request, "run.html", "run",
            cfg=cfg,
            cfg_error=cfg_error,
            packs=[item.as_dict() for item in pack_list],
            confirmed_packs=[item.as_dict() for item in pack_list if item.confirmed],
            runs=[item.as_dict() for item in run_list],
            run=selected.as_dict() if selected else None,
            progress=progress,
            pipeline_steps=runs.STEP_SPECS,
            models_bound=bool(cfg and cfg.models.fully_bound),
        )

    @app.get("/results", response_class=HTMLResponse)
    def page_results(request: Request, run: str | None = None):
        cfg, cfg_error = store.load_config_safe(repo)
        run_list = runs.list_runs(repo)
        selected = _selected(run_list, "run_id", run)
        cells: list[dict[str, Any]] = []
        if selected is not None:
            cells = runs.load_cells(repo, selected.run_id)
        order = _fields_of_pack(repo, selected.pack_id) if selected else []
        if order:
            rank = {name: index for index, name in enumerate(order)}
            cells.sort(key=lambda cell: (cell.get("paper", ""), cell.get("sheet", ""),
                                         cell.get("block", ""), str(cell.get("sex", "")),
                                         rank.get(cell.get("field", ""), 999)))
        stats = {
            "cells": len(cells),
            "diff": sum(1 for cell in cells if cell.get("match_type") != "agree"),
            "critical": sum(1 for cell in cells if cell.get("severity") == "critical"),
            "papers": len({cell.get("paper") for cell in cells}),
        }
        return render(
            request, "results.html", "results",
            cfg=cfg,
            cfg_error=cfg_error,
            runs=[item.as_dict() for item in run_list],
            run=selected.as_dict() if selected else None,
            cells=cells,
            stats=stats,
        )

    @app.get("/advice", response_class=HTMLResponse)
    def page_advice(request: Request, run: str | None = None):
        cfg, cfg_error = store.load_config_safe(repo)
        run_list = runs.list_runs(repo)
        selected = _selected(run_list, "run_id", run)
        items: list[dict[str, Any]] = []
        if selected is not None:
            items = runs.load_advice(repo, selected.run_id)
        return render(
            request, "advice.html", "advice",
            cfg=cfg,
            cfg_error=cfg_error,
            runs=[item.as_dict() for item in run_list],
            run=selected.as_dict() if selected else None,
            items=items,
            stats=runs.advice_stats(items),
        )

    @app.get("/export", response_class=HTMLResponse)
    def page_export(request: Request, run: str | None = None):
        cfg, cfg_error = store.load_config_safe(repo)
        run_list = runs.list_runs(repo)
        selected = _selected(run_list, "run_id", run)
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        manifest = None
        if selected is not None:
            groups = runs.artifact_groups(repo, selected.run_id)
            manifest = repo / "runs" / selected.run_id / "exports" / "export_manifest.json"
        return render(
            request, "export.html", "export",
            cfg=cfg,
            cfg_error=cfg_error,
            runs=[item.as_dict() for item in run_list],
            run=selected.as_dict() if selected else None,
            groups=groups,
            manifest_exists=bool(manifest and manifest.is_file()),
            manifest_rel=(
                str(manifest.relative_to(repo)) if manifest and manifest.is_file() else ""
            ),
        )

    # ---- 密钥与配置 -------------------------------------------------- #
    @app.post("/api/settings")
    def api_settings(payload: SettingsIn):
        keys = {slot: str(value) for slot, value in (payload.keys or {}).items()
                if slot in store.SLOT_LABELS}
        try:
            result = store.apply_settings(
                repo,
                budget_usd=payload.budget_usd,
                concurrency=payload.concurrency,
                keys=keys,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {
            "ok": True,
            "config_hash": result["config_hash"],
            "budget_usd": result["budget_usd"],
            "concurrency": result["concurrency"],
            "written_keys": result["written_keys"],
            "keys_set": store.key_status(repo),
        }

    # ---- 规则包 ------------------------------------------------------ #
    @app.post("/api/rules/parse")
    def api_rules_parse(payload: ParseIn):
        cfg, cfg_error = store.load_config_safe(repo)
        if cfg is None:
            raise HTTPException(status_code=400, detail=cfg_error or "配置不可用")
        input_dir = store.resolve_input_dir(repo, cfg)
        try:
            doc = store.resolve_doc(repo, payload.doc_path, input_dir)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            text, pages = store.read_document(doc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        slot = _pick_model_slot(cfg) if payload.mode == "model" else None
        if payload.mode == "model" and slot is None:
            raise HTTPException(
                status_code=400,
                detail="用模型解析前，请先在 configs/models.yaml 绑定一个模型槽位",
            )
        try:
            info = rulepacks.create_pack(
                repo,
                doc_path=doc,
                text=text,
                pages=pages,
                mode=payload.mode,
                slot=slot,
                pack_id=payload.pack_id,
            )
            fields = rulepacks.read_fields(repo, info.pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict(), "fields": fields,
                "redirect": f"/rules?pack={info.pack_id}"}

    @app.post("/api/rules/import")
    def api_rules_import(payload: ImportIn):
        try:
            info = rulepacks.import_builtin(repo, payload.pack_id)
            fields = rulepacks.read_fields(repo, info.pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict(), "fields": fields,
                "redirect": f"/rules?pack={info.pack_id}"}

    @app.post("/api/rules/upload")
    async def api_rules_upload(file: Annotated[UploadFile, File()], mode: str = "mock"):
        name = Path(file.filename or "").name
        if not name or Path(name).suffix.casefold() not in store.DOC_SUFFIXES:
            raise HTTPException(
                status_code=400, detail="规则文档只支持 .md / .markdown / .txt / .pdf / .docx"
            )
        target_dir = repo / rulepacks.PACK_ROOT / "_sources"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if target.exists():
            target = target_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{name}"
        written = 0
        try:
            with target.open("wb") as handle:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > 20 * 1024 * 1024:
                        raise ValueError("规则文档超过 20 MB")
                    handle.write(chunk)
        except ValueError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cfg, cfg_error = store.load_config_safe(repo)
        if cfg is None:
            raise HTTPException(status_code=400, detail=cfg_error or "配置不可用")
        try:
            text, pages = store.read_document(target)
            slot = _pick_model_slot(cfg) if mode == "model" else None
            if mode == "model" and slot is None:
                raise rulepacks.RulePackError(
                    "用模型解析前，请先在 configs/models.yaml 绑定一个模型槽位"
                )
            info = rulepacks.create_pack(
                repo, doc_path=target, text=text, pages=pages, mode=mode, slot=slot
            )
            fields = rulepacks.read_fields(repo, info.pack_id)
        except (ValueError, rulepacks.RulePackError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict(), "fields": fields,
                "redirect": f"/rules?pack={info.pack_id}"}

    @app.post("/api/rules/{pack_id}/confirm")
    def api_rules_confirm(pack_id: str):
        try:
            info = rulepacks.confirm_pack(repo, pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict()}

    @app.post("/api/rules/{pack_id}/fields")
    def api_field_add(pack_id: str, payload: FieldIn):
        try:
            info = rulepacks.add_field(
                repo, pack_id, payload.model_dump(exclude_none=True)
            )
            fields = rulepacks.read_fields(repo, pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict(), "fields": fields}

    @app.patch("/api/rules/{pack_id}/fields/{name}")
    def api_field_update(pack_id: str, name: str, payload: FieldIn):
        try:
            info = rulepacks.update_field(
                repo, pack_id, name, payload.model_dump(exclude_none=True)
            )
            fields = rulepacks.read_fields(repo, pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict(), "fields": fields}

    @app.delete("/api/rules/{pack_id}/fields/{name}")
    def api_field_delete(pack_id: str, name: str):
        try:
            info = rulepacks.delete_field(repo, pack_id, name)
            fields = rulepacks.read_fields(repo, pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.cache.clear()
        return {"ok": True, "pack": info.as_dict(), "fields": fields}

    # ---- 论文 -------------------------------------------------------- #
    @app.post("/api/papers/dir")
    def api_papers_dir(payload: DirIn):
        try:
            directory, config_hash = store.set_input_dir(repo, payload.input_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        pdfs, truncated, error = store.list_pdfs(directory)
        app.state.cache.clear()
        return {
            "ok": True,
            "input_dir": str(directory),
            "config_hash": config_hash,
            "count": len(pdfs),
            "truncated": truncated,
            "error": error,
            "pdfs": [{"name": item.name, "size": item.size_text, "mtime": item.mtime}
                     for item in pdfs],
        }

    @app.get("/api/papers")
    def api_papers(dir: str | None = None):
        cfg, cfg_error = store.load_config_safe(repo)
        if cfg is None:
            raise HTTPException(status_code=400, detail=cfg_error or "配置不可用")
        input_dir = store.resolve_input_dir(repo, cfg)
        raw = dir or (str(input_dir) if input_dir else "")
        try:
            directory = store.resolve_user_dir(repo, raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not store.allowed_path(repo, directory, [input_dir] if input_dir else []):
            raise HTTPException(
                status_code=403, detail="目录不在允许范围内（仅仓库或当前论文目录）"
            )
        pdfs, truncated, error = store.list_pdfs(directory)
        return {
            "dir": str(directory),
            "count": len(pdfs),
            "truncated": truncated,
            "error": error,
            "pdfs": [{"name": item.name, "size": item.size_text, "mtime": item.mtime}
                     for item in pdfs],
        }

    @app.post("/api/papers/upload")
    async def api_upload(files: Annotated[list[UploadFile], File()]):
        cfg, cfg_error = store.load_config_safe(repo)
        if cfg is None:
            raise HTTPException(status_code=400, detail=cfg_error or "配置不可用")
        directory = store.resolve_input_dir(repo, cfg)
        if directory is None or not directory.is_dir():
            raise HTTPException(status_code=400, detail="论文目录不存在，请先在论文页设置")

        saved: list[dict[str, Any]] = []
        rejected: list[str] = []
        for upload in files:
            name = Path(upload.filename or "").name
            if not name or name.startswith(".") or Path(name).suffix.casefold() != ".pdf":
                rejected.append(f"{name or '(未命名)'}：不是 PDF")
                continue
            target = directory / name
            written = 0
            try:
                with target.open("wb") as handle:
                    while True:
                        chunk = await upload.read(1 << 20)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_UPLOAD_BYTES:
                            raise ValueError("单文件超过 60 MB")
                        handle.write(chunk)
            except ValueError as exc:
                target.unlink(missing_ok=True)
                rejected.append(f"{name}：{exc}")
                continue
            saved.append({"name": name, "size": store.human_size(written)})
        app.state.cache.clear()
        return {"ok": True, "saved": saved, "rejected": rejected,
                "count": len(store.list_pdfs(directory)[0])}

    # ---- 运行 -------------------------------------------------------- #
    @app.get("/api/runs")
    def api_runs():
        return {"runs": [item.as_dict() for item in runs.list_runs(repo)]}

    @app.post("/api/runs")
    def api_run_create(payload: RunCreateIn):
        cfg, cfg_error = store.load_config_safe(repo)
        if cfg is None:
            raise HTTPException(status_code=400, detail=cfg_error or "配置不可用")
        try:
            pack = rulepacks.load_pack(repo, payload.pack_id)
        except rulepacks.RulePackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not pack.confirmed:
            raise HTTPException(
                status_code=409,
                detail="规则包尚未确认（status != confirmed），不能进入编码；请先在规则页确认",
            )
        input_dir = store.resolve_input_dir(repo, cfg)
        if input_dir is None or not input_dir.is_dir():
            raise HTTPException(status_code=400, detail="论文目录不存在，请先在论文页设置")
        run = runs.create_run(
            repo,
            pack_id=pack.pack_id,
            pack_hash=pack.pack_hash,
            papers_dir=str(input_dir),
            batch_id=cfg.pipeline.batch_id,
            config_hash=cfg.config_hash,
        )
        app.state.cache.clear()
        return {"ok": True, "run": run.as_dict(), "redirect": f"/run?run={run.run_id}"}

    @app.post("/api/runs/{run_id}/steps/{step}")
    def api_run_step(run_id: str, step: str, payload: StepIn | None = None):
        try:
            run = runs.load_run(repo, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if step not in runs.STEP_IDS:
            raise HTTPException(status_code=400, detail="未知步骤")
        dry_run = bool(payload and payload.dry_run)
        if step != "diff" and not dry_run and step in {"code_a", "code_b", "adjudicate"}:
            if not app.state.gates.g2:
                raise HTTPException(
                    status_code=403,
                    detail="门禁未通过：G2 未确认，真实模型调用被拒绝（可先勾选 dry-run 试跑）",
                )
        stage = f"run:{run.run_id}:{step}"
        if app.state.jobs.is_running(stage):
            raise HTTPException(status_code=409, detail="该步骤已在运行")

        if step == "diff":
            try:
                summary = runs.sync_and_diff(repo, run_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            app.state.cache.clear()
            return {"ok": True, "mode": "inline", "diff": summary,
                    "run": runs.load_run(repo, run_id).as_dict()}

        cfg, cfg_error = store.load_config_safe(repo)
        if cfg is None:
            raise HTTPException(status_code=400, detail=cfg_error or "配置不可用")
        batch = cfg.pipeline.batch_id
        if step == "ingest":
            args = ["ingest", "--batch", batch]
        elif step in {"code_a", "code_b"}:
            args = ["code", "--coder", step[-1], "--batch", batch]
            if dry_run:
                args.append("--dry-run")
        else:  # adjudicate
            args = ["adjudicate", "--batch", batch]
            if dry_run:
                args.append("--dry-run")
        argv = [sys.executable, "-m", "meta_coding.cli", *args, "--repo", str(repo)]

        runs.update_step(repo, run_id, step, status="running", started_at=_ts())
        app.state.cache.clear()

        def _on_done(job) -> None:
            try:
                runs.update_step(
                    repo, run_id, step,
                    status="done" if job.returncode == 0 else "failed",
                    returncode=job.returncode, ended_at=_ts(),
                    note="" if job.returncode == 0 else "见运行详情",
                )
                fresh = runs.load_run(repo, run_id)
                fresh.cost_usd = float(runs.run_progress(repo, run_id)["cost_usd"])
                runs.save_run(repo, fresh)
            except Exception:  # noqa: BLE001 - 回调失败不影响任务状态
                pass

        try:
            job = app.state.jobs.start(
                stage=stage,
                label=f"{run.run_id} · {step}",
                argv=argv,
                cwd=repo,
                env=store.run_env(repo),
                on_done=_on_done,
            )
        except OSError as exc:
            runs.update_step(
                repo, run_id, step, status="failed", ended_at=_ts(),
                note=f"无法启动子进程：{type(exc).__name__}",
            )
            raise HTTPException(status_code=500, detail="无法启动子进程") from exc
        return JSONResponse(
            {
                "ok": True,
                "job_id": job.id,
                "stage": step,
                "dry_run": dry_run,
                "command": " ".join(argv[1:]),
            },
            status_code=202,
        )

    @app.get("/api/runs/{run_id}/progress")
    def api_run_progress(run_id: str):
        return _run_progress_payload(repo, run_id, app.state.jobs)

    @app.post("/api/runs/{run_id}/export")
    def api_run_export(run_id: str):
        if not app.state.gates.g3:
            raise HTTPException(
                status_code=403,
                detail="门禁未通过：G3 未确认，导出清单未生成",
            )
        try:
            runs.load_run(repo, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = runs.write_export_manifest(repo, run_id)
        payload = store.read_json(path) or {}
        app.state.cache.clear()
        return {
            "ok": True,
            "path": str(path.relative_to(repo)),
            "entries": len(payload.get("entries") or []),
        }

    # ---- 修改意见 ---------------------------------------------------- #
    @app.post("/api/advice/decision")
    def api_advice_decision(payload: DecisionIn):
        try:
            runs.load_run(repo, payload.run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            result = runs.decide(
                repo, payload.run_id, payload.item_id, payload.action,
                value=payload.value, note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    # ---- 任务与下载 -------------------------------------------------- #
    @app.post("/api/gates")
    def api_gates(payload: GatesIn):
        for name in ("g2", "g3"):
            value = getattr(payload, name)
            if value is not None:
                setattr(app.state.gates, name, bool(value))
        app.state.cache.clear()
        return {"ok": True, "gates": app.state.gates.snapshot(), "meta": GATE_META}

    @app.get("/api/jobs")
    def api_jobs():
        return {"jobs": app.state.jobs.recent(20)}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str, since: int = 0):
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job.snapshot(max(0, since))

    @app.post("/api/jobs/{job_id}/stop")
    def api_job_stop(job_id: str):
        if app.state.jobs.get(job_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        stopped = app.state.jobs.stop(job_id)
        return {"ok": stopped, "message": "已发送终止信号" if stopped else "任务已结束"}

    @app.get("/files/{rel:path}")
    def download(rel: str):
        target = store.safe_download(repo, rel)
        if target is None:
            raise HTTPException(status_code=404, detail="文件不存在或不允许下载")
        return FileResponse(target, filename=target.name)

    @app.get("/api/state")
    def api_state():
        cfg, cfg_error = store.load_config_safe(repo)
        return {
            "ok": cfg is not None,
            "config_error": cfg_error,
            "batch": cfg.pipeline.batch_id if cfg else None,
            "config_hash": cfg.config_hash if cfg else None,
            "keys_set": store.key_status(repo),
            "gates": app.state.gates.snapshot(),
            "snapshot": _snapshot(app),
            "jobs": app.state.jobs.recent(8),
        }

    @app.get("/api/health")
    def health():
        return {"ok": True, "root": str(repo), "host": "127.0.0.1"}

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meta_coding.web.app", description="meta-coding 本机 Web 控制台（只监听回环地址）"
    )
    parser.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    parser.add_argument("--host", default="127.0.0.1", help="仅接受回环地址")
    parser.add_argument("--root", type=Path, default=None, help="仓库根目录（默认自动向上查找）")
    args = parser.parse_args(argv)

    if args.host not in LOOPBACK:
        parser.error("出于安全考虑，只允许监听回环地址（127.0.0.1 / localhost / ::1）")
    if not 1 <= args.port <= 65535:
        parser.error("端口必须落在 1–65535")

    root = args.root.resolve() if args.root else repo_root()
    app = create_app(root)

    import uvicorn

    print(f"meta-coding 控制台：http://127.0.0.1:{args.port}/")
    print(f"仓库：{root}")
    print("按 Ctrl+C 停止（只监听本机，不对外网开放）")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover - 由命令行入口执行
    raise SystemExit(main())
