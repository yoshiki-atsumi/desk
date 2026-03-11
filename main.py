"""
main.py — 選挙公報 AI パーサー
選挙公報（PDF/JPG/PNG）から候補者情報を一括抽出し、
JSON / CSV / Markdown 形式で出力します。

使い方:
  1. .env に OPENAI_API_KEY を設定
  2. input/ フォルダに選挙公報ファイルを配置
  3. python main.py
"""

import io
import os
import sys
from pathlib import Path

# Windowsコンソールの文字化け対策
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import openai
from dotenv import load_dotenv

from extractor import process_file
from writer import write_csv, write_index, write_json, write_markdown

load_dotenv()

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
SUPPORTED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}


def main() -> None:
    # API キー確認
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("エラー: OPENAI_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        print("エラー: GOOGLE_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    client = openai.OpenAI(api_key=api_key)

    # input フォルダ確認
    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir()
        print(f"'{INPUT_DIR}/' フォルダを作成しました。")
        print(f"選挙公報（PDF / JPG / PNG）を配置して再実行してください。")
        return

    files = sorted(
        f for f in INPUT_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
    )

    if not files:
        print(f"'{INPUT_DIR}/' に処理対象ファイルがありません（PDF/JPG/PNG）。")
        return

    print("=" * 50)
    print("  選挙公報 AI パーサー")
    print("=" * 50)
    print(f"対象ファイル: {len(files)} 件\n")

    # 出力フォルダ準備（candidatesフォルダは毎回クリア）
    OUTPUT_DIR.mkdir(exist_ok=True)
    cands_dir = OUTPUT_DIR / "candidates"
    if cands_dir.exists():
        for f in cands_dir.iterdir():
            f.unlink()
    else:
        cands_dir.mkdir()

    # 全ファイル処理
    all_candidates: list[dict] = []
    unknown_counter = [1]  # 不明候補者の連番（リスト経由で参照渡し）

    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] {file_path.name}")
        try:
            candidates = process_file(file_path, client, google_api_key, unknown_counter)
            all_candidates.extend(candidates)
            print(f"  ✓ {len(candidates)} 名を抽出")
        except Exception as e:
            print(f"  ✗ エラー: {e}", file=sys.stderr)
        print()

    if not all_candidates:
        print("候補者データを取得できませんでした。")
        return

    print(f"─" * 50)
    print(f"合計 {len(all_candidates)} 名を抽出しました。\n")

    # 出力
    print("出力中...")
    write_json(all_candidates, OUTPUT_DIR)
    write_csv(all_candidates, OUTPUT_DIR)
    write_markdown(all_candidates, OUTPUT_DIR)
    write_index(all_candidates, OUTPUT_DIR)

    print(f"\n✓ 出力完了: {OUTPUT_DIR}/")
    print(f"  candidates.json  — JSON")
    print(f"  candidates.csv   — CSV")
    print(f"  candidates/      — 候補者別 Markdown（プロフィール・政策・その他）")
    print(f"  index.md         — 候補者一覧")

    # 要確認フラグがある候補者を表示
    needs_review = [c for c in all_candidates if c.get("needs_review")]
    if needs_review:
        print(f"\n⚠ 手動確認推奨: {len(needs_review)} 名（OCR精度が低い）")
        for c in needs_review:
            print(f"  - {c['name']}  ({c['source_file']} p.{c['source_page']})")


if __name__ == "__main__":
    main()
