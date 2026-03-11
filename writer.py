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
    fieldnames = ["候補者名", "年齢", "政党", "キャッチフレーズ", "プロフィール", "政策テキスト", "その他", "全文", "元ファイル", "ページ", "要確認"]

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in candidates:
            writer.writerow({
                "候補者名": c.get("name", ""),
                "年齢": c.get("age") or "",
                "政党": c.get("party") or "",
                "キャッチフレーズ": c.get("catchphrase") or "",
                "プロフィール": c.get("profile", ""),
                "政策テキスト": "\n".join(c.get("policies", [])),
                "その他": c.get("other") or "",
                "全文": c.get("raw_text", ""),
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
        age = c.get("age")
        catchphrase = c.get("catchphrase") or ""
        other = c.get("other") or ""
        raw_text = c.get("raw_text") or ""

        header = (
            (f"**政党**: {party}\n\n" if party else "")
            + (f"**年齢**: {age}歳\n\n" if age else "")
            + f"**出典**: {source}\n{review_note}\n---\n\n"
        )

        # profile.md
        profile_md = (
            f"# {name} — プロフィール\n\n"
            + header
            + (f"> {catchphrase}\n\n" if catchphrase else "")
            + (c.get("profile") or "（プロフィール情報なし）")
            + ("\n\n---\n\n## その他\n\n" + other if other else "")
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

        # raw.md（全文そのまま）
        if raw_text:
            raw_md = (
                f"# {name} — 全文（原文）\n\n"
                + header
                + raw_text
                + "\n"
            )
            (cands_dir / f"{safe}_raw.md").write_text(raw_md, encoding="utf-8")

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
        age = c.get("age")
        catchphrase = c.get("catchphrase") or ""
        safe = _safe_filename(name)
        review = " ⚠" if c.get("needs_review") else ""
        has_raw = bool(c.get("raw_text"))
        raw_link = (
            f"- **全文**: [candidates/{safe}_raw.md](candidates/{safe}_raw.md)\n"
            if has_raw else ""
        )
        lines.append(
            f"## {name}{review}\n"
            + (f"> {catchphrase}\n\n" if catchphrase else "")
            + f"- **政党**: {party}\n"
            + (f"- **年齢**: {age}歳\n" if age else "")
            + f"- **プロフィール**: [candidates/{safe}_profile.md](candidates/{safe}_profile.md)\n"
            f"- **政策**: [candidates/{safe}_policies.md](candidates/{safe}_policies.md)\n"
            + raw_link
            + f"- **出典**: {c.get('source_file', '')} p.{c.get('source_page', '')}\n"
        )

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {path}")
