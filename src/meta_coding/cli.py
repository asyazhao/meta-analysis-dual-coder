"""Command line entry point.

Stage 0 ships the full command surface so that the interface is fixed before
any stage logic exists. Commands whose stage has not started exit with code 2
and a message naming the stage that will implement them — they never pretend
to have done work.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .artifacts import verify_all
from .config import ConfigError, load_config
from .paths import ensure_layout, repo_root

# command -> stage that implements it
STAGE_OF_COMMAND: dict[str, int] = {
    "init": 0,
    "intake": 1,
    "rules": 2,
    "ingest": 3,
    "code": 4,
    "validate": 5,
    "diff": 6,
    "adjudicate": 6,
    "consensus": 7,
    "qa": 8,
    "report": 9,
    "verify": 0,
}

NOT_YET = "尚未实现"


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", type=Path, default=None, help="仓库根目录（默认自动向上查找）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-coding",
        description="元分析双模型编码与第三方裁决流水线",
        epilog="阶段命令在对应阶段实现前会以退出码 2 结束，不会假装完成工作。",
    )
    parser.add_argument("--version", action="version", version=f"meta-coding {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("init", help="初始化/校验工作目录骨架")
    _add_common(p)
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("intake", help="Stage 1 语料清点")
    _add_common(p)
    p.add_argument("--batch", required=True)

    p = sub.add_parser("rules", help="Stage 2 规则机器化")
    _add_common(p)
    p.add_argument("action", choices=["build", "verify"])

    p = sub.add_parser("ingest", help="Stage 3 文档摄取")
    _add_common(p)
    p.add_argument("--batch", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("code", help="Stage 4 双盲编码")
    _add_common(p)
    p.add_argument("--coder", choices=["a", "b"], required=True)
    p.add_argument("--batch", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-cost", type=float, default=None, dest="max_cost")

    p = sub.add_parser("validate", help="Stage 5 单模型校验")
    _add_common(p)
    p.add_argument("--coder", choices=["a", "b"], required=True)
    p.add_argument("--batch", required=True)
    p.add_argument("--limit", type=int, default=None)

    p = sub.add_parser("diff", help="Stage 6 确定性比对")
    _add_common(p)
    p.add_argument("--batch", required=True)

    p = sub.add_parser("adjudicate", help="Stage 6 第三方裁决与一致样本审计")
    _add_common(p)
    p.add_argument("--batch", required=True)
    p.add_argument("--audit-rate", type=float, default=None, dest="audit_rate")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-cost", type=float, default=None, dest="max_cost")

    p = sub.add_parser("consensus", help="Stage 7 共识工作簿")
    _add_common(p)
    p.add_argument("--batch", required=True)
    p.add_argument("--coder-a", type=Path, default=None, dest="coder_a",
                   help="编码员 A 工作簿（默认在 data/03_coded/coder_a/ 下查找）")
    p.add_argument("--coder-b", type=Path, default=None, dest="coder_b",
                   help="编码员 B 工作簿（默认在 data/03_coded/coder_b/ 下查找）")
    p.add_argument("--adjudication", type=Path, default=None,
                   help="裁决记录 JSONL（默认 data/05_adjudication/）")
    p.add_argument("--manual", type=Path, default=None,
                   help="人工修改表 CSV（优先级最高）")

    p = sub.add_parser("qa", help="Stage 8 分析可用性 QA")
    _add_common(p)
    p.add_argument("--batch", required=True)
    p.add_argument("--consensus", type=Path, default=None,
                   help="共识工作簿（默认 data/06_consensus/<batch>_consensus_codebook.xlsx）")
    p.add_argument("--out-dir", type=Path, default=None, dest="out_dir",
                   help="导出目录（默认 data/07_analysis/）")

    p = sub.add_parser("report", help="Stage 9 报告生成")
    _add_common(p)
    p.add_argument("--batch", required=True)

    p = sub.add_parser("verify", help="校验全部 manifest 哈希")
    _add_common(p)
    p.add_argument("--all", action="store_true")

    return parser


def _root(args: argparse.Namespace) -> Path:
    return args.repo.resolve() if args.repo else repo_root()


def _rel(root: Path, path: Path | None) -> str:
    """报告里统一用仓库相对路径。"""
    if path is None:
        return "-"
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def cmd_init(args: argparse.Namespace) -> int:
    root = _root(args)
    created, existed = ensure_layout(root)
    if not args.quiet:
        print(f"仓库：{root}")
        print(f"新建目录 {len(created)} 个，已存在 {len(existed)} 个")
        for rel in created:
            print(f"  + {rel}")
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(f"配置检查：未通过 —— {exc}", file=sys.stderr)
        return 2
    print(f"配置：batch={cfg.pipeline.batch_id} input_dir={cfg.pipeline.input_dir}")
    print(f"config_hash = {cfg.config_hash}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = _root(args)
    results = verify_all(root)
    if not results:
        print("尚无 manifest（artifacts/stage-*/** 为空）；Stage 0 之前属正常。")
        return 0
    failed = 0
    for res in results:
        print(res.summary())
        failed += 0 if res.ok else 1
    print(f"\n共 {len(results)} 份 manifest，失败 {failed} 份")
    return 1 if failed else 0


def cmd_intake(args: argparse.Namespace) -> int:
    from .ingest.pipeline import run_intake

    root = _root(args)
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.batch != cfg.pipeline.batch_id:
        print(
            f"批次不一致：命令行 --batch {args.batch} 与 configs/pipeline.yaml "
            f"的 {cfg.pipeline.batch_id} 不符（input_dir 按批次配置）",
            file=sys.stderr,
        )
        return 2

    result = run_intake(root, cfg)
    print(f"文件 {result.files} 个：文章 {result.articles}、补充材料 {result.supplements}、"
          f"重复 {result.duplicates}")
    print(f"扫描件 {result.scanned}、无 DOI {result.no_doi}、不可读 {result.unreadable}")
    print(f"清单：{result.manifest_path.relative_to(root)}")
    print(f"注册表：{result.registry_path.relative_to(root)}")
    print(f"报告：{result.report_path.relative_to(root)}")
    print(f"manifest：{result.manifest_json_path.relative_to(root)}")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    from .rules.build import build, verify_rules_lock
    from .rules.contract import check_contract

    root = _root(args)
    rules_dir = root / "rules"

    if args.action == "build":
        result = build(rules_dir)
        print(f"字段：{result.fields}")
        print(f"Schema：{result.schema_path.relative_to(root)}")
        print(f"Schema：{result.adjudication_path.relative_to(root)}")
        print(f"契约：{result.contract_path.relative_to(root)}")
        print(f"模板：{result.workbook_path.relative_to(root)}")
        if result.problems:
            for p in result.problems:
                print(f"  ✗ {p}", file=sys.stderr)
            return 1
        print("规则锁与模板契约校验：通过")
        return 0

    problems = check_contract(rules_dir / "clean_codebook_BLANK.xlsx",
                              rules_dir / "template_contract.json")
    problems += verify_rules_lock(rules_dir)
    if problems:
        for p in problems:
            print(f"✗ {p}", file=sys.stderr)
        return 1
    print("规则锁与模板契约校验：通过")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest.pipeline_ingest import run_ingest

    root = _root(args)
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.batch != cfg.pipeline.batch_id:
        print(f"批次不一致：--batch {args.batch} vs {cfg.pipeline.batch_id}", file=sys.stderr)
        return 2

    result = run_ingest(root, cfg, limit=args.limit, dry_run=args.dry_run)
    print(f"文章 {result.articles}，写入 packet {result.packets}，"
          f"表格 {result.total_tables}，字符 {result.total_chars:,}")
    print(f"含扫描页文章：{result.scanned}")
    print(f"QA：{result.qa_path.relative_to(root)}")
    print(f"manifest：{result.manifest_path.relative_to(root)}")
    return 0


def cmd_code(args: argparse.Namespace) -> int:
    from .coders.runner import check_parity, run_coder

    root = _root(args)
    try:
        cfg = load_config(root, require_bound_models=not args.dry_run)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.batch != cfg.pipeline.batch_id:
        print(f"批次不一致：--batch {args.batch} vs {cfg.pipeline.batch_id}", file=sys.stderr)
        return 2

    try:
        parity = check_parity(root, cfg, args.limit)
    except AssertionError as exc:
        print(f"✗ 双编码员提示词不一致，已中止：{exc}", file=sys.stderr)
        return 3

    result = run_coder(
        root, cfg, args.coder, limit=args.limit, dry_run=args.dry_run, max_cost=args.max_cost
    )
    tag = "DRY-RUN" if result.dry_run else "RUN"
    print(f"[{tag}] coder_{result.coder}: 文章 {result.articles}，调用 {result.calls}，"
          f"失败 {result.failures}，跳过已完成 {result.skipped_existing}")
    print(f"提示词对齐：{parity['articles']} 篇，hash {parity['prompt_hash'][:24]}…（A==B）")
    if result.dry_run:
        total = sum(p.est_tokens for p in result.previews)
        print(f"预估输入 token：{total:,}（按字符数/4 粗估，未调用任何 API）")
        for item in result.previews:
            print(f"  {item.source_file:<10} {item.sha256[:20]}… {item.est_tokens:>8,} tok")
    else:
        print(f"token：输入 {result.prompt_tokens:,} / 输出 {result.completion_tokens:,}；"
              f"成本 {result.cost_usd:.4f} USD")
        print(f"落盘：{result.run_dir.relative_to(root)}")
    return 0 if result.failures == 0 else 1


def _write_stage_manifest(
    *,
    root: Path,
    cfg,
    stage: str,
    seed: str,
    inputs: list[Path],
    outputs: list[Path],
    counts: dict,
    checks: list[dict],
    notes: str = "",
    status: str = "complete",
) -> Path:
    """按 RUNBOOK §5 写 stage manifest：输入/输出哈希 + checks + counts。"""
    from .artifacts import build_manifest, git_head, make_run_id, write_manifest

    manifest = build_manifest(
        root=root,
        stage=stage,
        run_id=make_run_id(seed),
        git_commit=git_head(root),
        rules_hash=cfg.rules_hash,
        config_hash=cfg.config_hash,
        inputs=[path for path in inputs if path.is_file()],
        outputs=outputs,
        counts=counts,
        checks=checks,
        status=status,
        notes=notes,
    )
    return write_manifest(root, stage, manifest)


def cmd_consensus(args: argparse.Namespace) -> int:
    from .consensus.build import build_consensus

    root = _root(args)
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.batch != cfg.pipeline.batch_id:
        print(
            f"批次不一致：--batch {args.batch} vs configs/pipeline.yaml 的 "
            f"{cfg.pipeline.batch_id}",
            file=sys.stderr,
        )
        return 2

    try:
        result = build_consensus(
            root=root,
            batch=args.batch,
            coder_a=args.coder_a,
            coder_b=args.coder_b,
            adjudication=args.adjudication,
            manual=args.manual,
        )
    except FileNotFoundError as exc:
        print(f"输入缺失：{exc}", file=sys.stderr)
        return 2

    for line in result.summary():
        print(line)
    for example in result.diff_examples:
        print(f"  差异 {example}", file=sys.stderr)

    manifest = _write_stage_manifest(
        root=root,
        cfg=cfg,
        stage="07_consensus",
        seed=f"07:{args.batch}:{result.cells}",
        inputs=result.inputs,
        outputs=[result.workbook, result.provenance, result.paper_id_map],
        counts={
            "papers": result.papers,
            "blocks": result.blocks,
            "rows": result.rows,
            "cells": result.cells,
            "pending_human": result.pending,
            "by_source": result.by_source,
            "writeback_diffs": result.writeback_diffs,
        },
        checks=[
            {
                "id": "paper_id_contiguous",
                "ok": result.numbering_ok,
                "detail": f"论文 {result.papers} 篇；最大 paper_id 等于不同论文数",
            },
            {
                "id": "paper_id_assigned",
                "ok": result.unidentified_blocks == 0,
                "detail": f"无 full_reference 的块 {result.unidentified_blocks} 个",
            },
            {
                "id": "writeback_readback",
                "ok": result.writeback_diffs == 0,
                "detail": f"差异 {result.writeback_diffs} 格",
            },
        ],
        notes="取值优先级：人工 > 裁决 > A/B 一致 > pending_human",
        status=(
            "complete"
            if result.writeback_diffs == 0
            and result.numbering_ok
            and result.unidentified_blocks == 0
            else "failed"
        ),
    )
    print(f"manifest：{manifest.relative_to(root)}")
    ok = (
        result.writeback_diffs == 0
        and result.numbering_ok
        and result.unidentified_blocks == 0
    )
    return 0 if ok else 1


def cmd_qa(args: argparse.Namespace) -> int:
    from .analysis.export import export_analysis

    root = _root(args)
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.batch != cfg.pipeline.batch_id:
        print(
            f"批次不一致：--batch {args.batch} vs configs/pipeline.yaml 的 "
            f"{cfg.pipeline.batch_id}",
            file=sys.stderr,
        )
        return 2

    try:
        result = export_analysis(
            root=root,
            batch=args.batch,
            consensus_path=args.consensus,
            out_dir=args.out_dir,
        )
    except FileNotFoundError as exc:
        print(f"输入缺失：{exc}", file=sys.stderr)
        return 2

    for line in result.summary():
        print(line)

    manifest = _write_stage_manifest(
        root=root,
        cfg=cfg,
        stage="08_analysis",
        seed=f"08:{args.batch}:{result.rows}",
        inputs=result.inputs,
        outputs=[
            result.long_path,
            result.dictionary_path,
            result.completeness_path,
            result.unresolved_path,
        ],
        counts={
            "papers": result.papers,
            "rows": result.rows,
            "skipped_placeholder": result.skipped_placeholder,
            "missing_cells": result.missing_cells,
            "pending_cells": result.pending_cells,
            "unresolved_entries": result.unresolved_entries,
            "unusable_rows": result.unusable_rows,
        },
        checks=[
            {
                "id": "missing_code_removed",
                "ok": result.missing_code_leaks == 0,
                "detail": f"-999 泄漏 {result.missing_code_leaks} 格；应为 0",
            },
            {
                "id": "placeholder_rows_skipped",
                "ok": True,
                "detail": f"跳过纯占位行 {result.skipped_placeholder} 行",
            },
        ],
        status="complete" if result.missing_code_leaks == 0 else "failed",
    )
    print(f"manifest：{manifest.relative_to(root)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .consensus.materialize import MaterializeError
    from .validate.records import run_validate

    root = _root(args)
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.batch != cfg.pipeline.batch_id:
        print(f"批次不一致：--batch {args.batch} vs {cfg.pipeline.batch_id}", file=sys.stderr)
        return 2

    try:
        result = run_validate(root, args.coder, args.batch, limit=args.limit)
    except (FileNotFoundError, MaterializeError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if result.articles == 0:
        print(f"批次 {args.batch} 在语料清单里没有文章记录，无可校验对象。", file=sys.stderr)
        return 1

    print(f"[coder_{result.coder}] 文章 {result.articles}，有输出 {result.outputs}，"
          f"缺输出 {result.missing_output}，解析失败 {result.parse_errors}，"
          f"Schema 错误 {result.schema_errors}")
    print(f"违规 {result.violation_total}（auto {result.auto_total} / "
          f"semi {result.semi_total}）；待处理记录 {result.pending_total}")
    if result.by_vid:
        print("违规分布：" + "，".join(f"{vid}×{n}" for vid, n in result.by_vid.items()))
    if result.workbook is not None:
        blocks = "，".join(f"{sheet} {n}" for sheet, n in result.workbook_blocks.items())
        print(f"物化工作簿：{_rel(root, result.workbook)}（{blocks} 块）")
        if result.batch_violations:
            detail = "，".join(f"{vid}×{n}" for vid, n in result.batch_violations.items())
            print(f"整批复核：{sum(result.batch_violations.values())} 条（{detail}）")
    print(f"逐篇报告：{_rel(root, result.out_dir)}/<article_id>.json")
    print(f"回灌清单：{_rel(root, result.repair_log)}（{result.repair_articles} 篇）")
    print(f"汇总：{_rel(root, result.summary_csv)}")
    return 0 if (result.violation_total == 0 and result.missing_output == 0
                 and result.parse_errors == 0) else 1


def _not_implemented(command: str) -> int:
    stage = STAGE_OF_COMMAND.get(command, 9)
    print(
        f"[meta-coding {command}] {NOT_YET}：将由 Stage {stage:02d} 落地。"
        "当前仓库只完成已验收阶段的产物，未验收阶段的命令一律显式失败而非静默跳过。",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "init":
        return cmd_init(args)
    if args.command == "intake":
        return cmd_intake(args)
    if args.command == "rules":
        return cmd_rules(args)
    if args.command == "ingest":
        return cmd_ingest(args)
    if args.command == "code":
        return cmd_code(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "consensus":
        return cmd_consensus(args)
    if args.command == "qa":
        return cmd_qa(args)
    if args.command == "verify":
        return cmd_verify(args)
    return _not_implemented(args.command)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
