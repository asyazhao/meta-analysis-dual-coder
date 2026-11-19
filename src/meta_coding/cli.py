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

    p = sub.add_parser("qa", help="Stage 8 分析可用性 QA")
    _add_common(p)
    p.add_argument("--batch", required=True)

    p = sub.add_parser("report", help="Stage 9 报告生成")
    _add_common(p)
    p.add_argument("--batch", required=True)

    p = sub.add_parser("verify", help="校验全部 manifest 哈希")
    _add_common(p)
    p.add_argument("--all", action="store_true")

    return parser


def _root(args: argparse.Namespace) -> Path:
    return args.repo.resolve() if args.repo else repo_root()


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
    if args.command == "verify":
        return cmd_verify(args)
    return _not_implemented(args.command)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
