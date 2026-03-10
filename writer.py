"""
writer.py — 抽出した候補者データを各形式で出力するモジュール
JSON / CSV / Markdown（候補者別 profile.md + policies.md）
"""

import csv
import json
import re
from pathlib import Path


def _safe_filename(name: str) -> str:
    """ファイル名に使えない文字を置換"""
    return re.sub(r'[\\/:*?"<>|\s]', "_", name)


# ─────────────────────────────────────────
# JSON 出力
# ─────────────────────────────────────────

def write_json(candidates: list[dict], output_dir: Path) -> None:
    path = output_dir / "candidates.json"
    output = {"candidates": candidates}
    path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  → {path}")


# ─────────────────────────────────────────
# CSV 出力
# ─────────────────────────────────────────

def write_csv(candidates: list[dict], output_dir: Path) -> None:
    path = output_dir / "candidates.csv"
    fieldnames = ["候補者名", "政党", "プロフィール", "政策テキスト", "元ファイル", "ページ", "要確認"]

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in candidates:
            writer.writerow({
                "候補者名": c.get("name", ""),
                "政党": c.get("party") or "",
                "プロフィール": c.get("profile", ""),
                "政策テキスト": "\n".join(c.get("policies", [])),
                "元ファイル": c.get("source_file", ""),
                "ページ": c.get("source_page", ""),
                "要確認": "○" if c.get("needs_review") else "",
            })
    print(f"  → {path}")


# ─────────────────────────────────────────
# Markdown 出力（候補者別）
# ─────────────────────────────────────────

def write_markdown(candidates: list[dict], output_dir: Path) -> None:
    cands_dir = output_dir / "candidates"
    cands_dir.mkdir(exist_ok=True)

    for c in candidates:
        name = c.get("name", "不明")
        safe = _safe_filename(name)
        party = c.get("party") or ""
        source = f"{c.get('source_file', '')} (p.{c.get('source_page', '')})"
        review_note = (
            "\n> ⚠ **要確認**: OCR精度が低い箇所があります。内容を手動で確認してください。\n"
            if c.get("needs_review")
            else ""
        )
        header = (
            f"**政党**: {party}\n\n" if party else ""
        ) + f"**出典**: {source}\n{review_note}\n---\n\n"

        # profile.md
        profile_md = (
            f"# {name} — プロフィール\n\n"
            + header
            + (c.get("profile") or "（プロフィール情報なし）")
            + "\n"
        )
        (cands_dir / f"{safe}_profile.md").write_text(profile_md, encoding="utf-8")

        # policies.md
        policies = c.get("policies") or []
        policies_body = (
            "\n".join(f"- {p}" for p in policies)
            if policies
            else "（政策情報なし）"
        )
        policies_md = (
            f"# {name} — 政策・公約\n\n"
            + header
            + policies_body
            + "\n"
        )
        (cands_dir / f"{safe}_policies.md").write_text(policies_md, encoding="utf-8")

    print(f"  → {cands_dir}/ （{len(candidates)} 名分）")


# ─────────────────────────────────────────
# インデックス Markdown（候補者一覧）
# ─────────────────────────────────────────

def write_index(candidates: list[dict], output_dir: Path) -> None:
    """候補者一覧インデックスを生成（Q&Aシステム連携用）"""
    path = output_dir / "index.md"
    lines = ["# 候補者一覧\n"]

    for c in candidates:
        name = c.get("name", "不明")
        party = c.get("party") or "（政党不明）"
        safe = _safe_filename(name)
        review = " ⚠" if c.get("needs_review") else ""
        lines.append(
            f"## {name}{review}\n"
            f"- **政党**: {party}\n"
            f"- **プロフィール**: [candidates/{safe}_profile.md](candidates/{safe}_profile.md)\n"
            f"- **政策**: [candidates/{safe}_policies.md](candidates/{safe}_policies.md)\n"
            f"- **出典**: {c.get('source_file', '')} p.{c.get('source_page', '')}\n"
        )

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {path}")
