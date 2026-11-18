# Existing mobile notification task — proposed update

Apply this to the already existing ChatGPT task, preserving its current hourly
schedule and mobile push delivery. It is a reviewable instruction draft; it has
not been installed or tested for delivery. Do not create a duplicate task.

---

既存のFX相場分析通知を更新する。通知先は従来どおりChatGPTのモバイル通知とし、実注文や証券口座への接続は一切行わない。

分析元は公開リポジトリ oyamada-shinya/FX_auto の検証済みリビジョンとする。
開発ブランチ codex/research-hardening-20261118 は研究用で、最終受入未完了である。
mainへの統合前に両ブランチの結果を混同しない。

毎回、利用可能な正規のGitHub接続で対象ブランチの最新コミットと、そのコミットに対応するFX researchの最新実行・最新attemptを確認する。
過去の成功実行だけを選ばない。実行ID、コミット、結論、取得日時を照合し、その実行のreport.json成果物を読む。
実行情報や成果物を読めない場合は「今回取得できず、新規注文見送り」と通知し、以前のレートを最新値として転用しない。
別チャットの回答や共有コピーをデータ取得の代用にしない。

docs/notification-contract.md と notification.validate_notification の契約に従い、schema_version、研究専用HOLD、5通貨×3時間足の完全性、品質、確定足時刻と現在の経過時間、全数値の有限性を検査する。
検証を実行できない場合も完了扱いにしない。
欠損・異常値・取得失敗・古い成果物・未確認の市場休場では、レートや設定値を再利用せず、静的な停止理由だけを通知する。

通知は、問題整理→原因分析→対応の順に簡潔に構成する。
正常な市場データを読めた場合も、研究専用・新規注文見送りを維持し、データ時刻と足の確定時刻を明示する。
WMA・ATR・BB・MACDの検算と、実相場の収益検証を区別する。
実履歴・コスト・非公開の建玉状態・リスク評価・未使用OOS/ウォークフォワードの受入が済むまで、売買設定を受入済み推奨値として提示しない。
目標達成のために損失制限を緩和しない。

個人の資金・リスク設定・口座情報・認証情報・個別バックテストの詳細を公開リポジトリ、公開Actionsログ、成果物へ送らない。
通知済みの実行IDとコミットの組を記録し、配信成功後にのみ通知済みへ更新する。
重複通知を避け、失敗から復旧した場合は新しい完全かつ新鮮な実行であることを再検証する。

設定更新と実際のiPhone通知の到着は別々に確認し、到着を確認できるまで通知統合完了と記録しない。
