# 仕様: pii_scan.py（ファイル／カラム単位の PII スキャナ）

## 目的
pii_check.py の単体検証を実務の形にする：「この CSV / JSON の、このカラムに PII があるか？ あったら出して」。
対象データは GB〜TB 級を想定する。したがって **ファイルを丸ごとメモリに載せない**（ストリーミング処理）が絶対条件。

## 入力形式
- CSV（ヘッダ行あり、UTF-8 / UTF-8 BOM 可）
- JSON Lines（1行1オブジェクト）
- JSON（トップレベルがオブジェクトの配列。チャンク読み＋逐次パースでストリーミングする）
- 形式は拡張子から自動判定（.csv / .jsonl / .ndjson / .json）、`--format` で上書き可。

## 処理方式
- 行（レコード）を1件ずつ読み、各カラムの値を pii_check.classify で判定し、カラムごとに集計する。
- デフォルトは **サンプリング：先頭 100,000 レコード**まで読んで止める（`--sample N` で変更、`--full` で全件）。
- レポートには `rows_scanned` と `reached_eof`（最後まで読んだか）を必ず含める。サンプリングで止めた場合は「全件ではない」ことがレポート自体から分かること。
- JSON の値：文字列だけを判定対象にする（数値は先行ゼロを保持できないため対象外）。ネストしたオブジェクト／配列の値は対象外。
- 判定はセル値全体に対して行う。自由文の中に埋め込まれた番号の抽出（部分文字列検索）は対象外（v2 候補）。

## エラーと境界の扱い（2026-11-15 レビューで確定）
- `reached_eof` は正確に報告する：レコード数がちょうどサンプル上限と一致する場合は True（1件先読みして判定する）。
- JSON 配列が途中で切れている・末尾カンマ・閉じ括弧の後にデータがある場合は、黙って正常終了せず ValueError にする。空入力も同様。
- JSON / JSONL でオブジェクトでないレコード（配列・文字列・数値・null）は、スキャンを中断せずスキップし、レポートの `skipped_non_object` に件数を残す。JSON として壊れている JSONL 行は、行番号つきで ValueError にする。
- CSV のヘッダに重複したカラム名がある場合は ValueError にする（黙って上書きしない）。
- 空白のみの値は「空」として数える（`values_seen` の分母に入れない）。
- `sample_limit` の負数はエラー。CLI の `--sample` も同様。

## レポート（カラムごと）
- `values_seen`（空でない値の数）、`empty`
- `matches`：種別ごとの一致数（1つの値が複数種別に一致しうる）
- `top_type` / `top_ratio`（最頻種別の一致率）
- `tier`：top_type の格。`check_digit`（個人番号・法人番号・カード・IMEI・免許証）か `format`（電話番号）
- `verdict`：
  - check_digit ティア： ratio ≥ 0.8 → `pii`、≥ 0.2 → `possible`、未満 → `none`
  - format ティア： ratio ≥ 0.8 でも `possible` まで（形だけでは確定させない）、≥ 0.2 → `possible`、未満 → `none`
- `example_masked`：top_type に一致した最初の値をマスクしたもの（先頭2文字＋末尾2文字を残し中間を `*`。4文字以下は末尾1文字だけ残す）。生の PII をレポートに残さない。

## CLI
```
python3 pii_scan.py FILE [--format csv|jsonl|json] [--sample N | --full] [--json-out PATH]
```
標準出力に人間可読の要約（カラム名・verdict・top_type・ratio）を出し、`--json-out` で完全なレポート JSON を書く。

## ライブラリ API
- `scan_csv(path, sample_limit=100000) -> dict`
- `scan_jsonl(path, sample_limit=100000) -> dict`
- `scan_json_array(path, sample_limit=100000) -> dict`
- `scan_file(path, format=None, sample_limit=100000) -> dict`
（sample_limit=None で全件）

## テスト観点（Muse 側で実施）
- 正常系カラム（個人番号／法人番号／電話番号）、無関係カラム（氏名・金額）、混在カラム（半分だけ法人番号 → possible）。
- CSV / JSONL / JSON 配列の3形式で同じ構造のデータを読み、同じ判定になること。
- サンプリング：1000行の CSV を sample=100 で読み、rows_scanned=100・reached_eof=False になること。
- 大規模：20万行 CSV を生成して全件スキャンし、判定が正しく、実用時間で終わること。
- マスク形式の確認。
