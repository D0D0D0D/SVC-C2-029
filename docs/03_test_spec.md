# Test Specification — SVC-C2-029

Business Travel Hotel & Plan Recommendation Agent

テスト方針: 全ノードは DI シーム（PolicyKB / HotelSearch / LLMClient）で double を注入し、
ネットワーク非依存・決定論で検証する。ローカル `pytest tests/` 100% PASS を push 前ゲートとする。

## 1. Framework Compliance Tests (TC)

| TC-ID | 内容 | 対応テスト | 期待結果 |
|-------|------|-----------|----------|
| TC-01 | State は flat TypedDict（Pydantic/dataclass 不可） | `test_state_is_flat_typeddict` / PB `test_state_file_safety` | `__dataclass_fields__` 不在・credential 欄なし |
| TC-02 | 不正入力で SecurityViolationError | `test_s2_input_size_limit_raises` / `test_s2_egress_pii_blocked` | raise される |
| TC-03 | JWT/credential が src/ に無い | CI の資格情報スキャン | PASS |
| TC-04 | InvocationContext は configurable 経由のみ | 実装（secrets=`current_secrets()`、ctx=`config` 経由） | State 非格納 |
| TC-05 | 監査ログ（emit_trace_event）が副作用ノードで発火 | 実装（web_fetch/llm_rerank/policy_filter/policy_kb_build） | domain event 発火 |
| TC-06 | S-2 入力ゲート非バイパス（`@final`+`_extra_` フック） | `test_s2_*`（`_extra_security_gate_input`） | ゲート常時実行 |
| TC-07 | S-3 出力ゲート非バイパス（`@final`+`_extra_` フック） | `test_s3_pii_in_output_raises` | ゲート常時実行 |
| TC-08 | required_trust_level 宣言・強制、S-1 拒否時に `execute()` が呼ばれなかったことを証明 | `tests/proof_of_boundary/test_pb_invoke_order.py::test_s1_denial_refuses_execution_before_execute` | 不足時拒否・`execute()` 未到達 |

## 2. Domain Tests (unit)

| 対象 | テスト | 検証内容 |
|------|--------|----------|
| PreProcess | `test_pre_process_extracts_params` / `_empty_input_errors` | request_params 抽出・空入力で ERROR |
| PolicyKB | `test_policy_kb_build_and_load` / `_missing_tenant_errors` | build→retrieve・tenant 欠落で ERROR |
| WebFetch | `test_web_fetch_returns_candidates` / `_degrades_on_empty` / `_degrades_on_exception` | 取得・0件縮退・例外縮退（raise しない） |
| PolicyFilter | `test_policy_filter_drops_clear_over_per_diem` / `_keeps_unknown_as_needs_review` | 明白な規程外のみ除外・判定不能は needs_review 保持 |
| SoftRerank | `test_soft_rerank_subset_and_orders_cheaper_nearer_first` | 出力 ID ⊆ 入力（候補を増やさない）・価値判断順序 |
| OutputAssemble | `test_output_assemble_adds_disclaimer_and_remaining_budget` | 残予算目安・参考価格 disclaimer |
| ノード契約 | `test_node_contract_execute_signature` | execute(self, state)・`_invoke_impl` 不在 |

## 3. Proof-of-Boundary Tests (PB)

| PB-ID | 境界 | テスト | 期待結果 |
|-------|------|--------|----------|
| PB-2/PB-5 | State serialization | `test_state_file_safety` / `test_pb5_final_state_is_msgpack_serializable` | primitives のみ・msgpack 可 |
| PB-4 | Import isolation | `test_no_prohibited_imports_in_src` | agenticstar(L0) 直 import 0 |
| PB-5 | Checkpoint safety（memory=on） | `test_pb5_final_state_is_msgpack_serializable` | checkpoint に Pydantic/JWT 無し |
| PB-6 | S-3 保全（ID 突合） | `test_pb6_s3_invariant_output_ids_subset_of_filtered` / `test_s3_drops_hallucinated_recommendation` / `_all_hallucinated_degrades_to_needs_review` | 出力 ID ⊆ filtered_candidates・幻覚は除外続行・全滅で needs_review |
| （invoke） | 実行順・backbone | `test_recommend_invoke_succeeds_and_backbone_runs` / `test_onboard_policy_mode_builds_kb` | 5 ノード backbone 実行・2 モード動作 |

## 4. 実行

```bash
python -m pytest tests/ -v
python -m pytest tests/proof_of_boundary/ -v
```

現状: **32 tests PASS**（unit + proof_of_boundary、ローカル Python 3.11 + SDK 1.0.0 wheel）。

## 実 LLM 1 回転検証

モックだけで固めた実装は実 LLM を繋いだ瞬間に動かないことがあるため、注入シームへ実 LLM クライアントを刺して 1 回 invoke し、**中身のある出力が出ること**を確認している（`gitproject-dev-flow.md` §14-1-bis）。

| 項目 | 内容 |
|---|---|
| 実施日 | 2026-08-18 |
| 使用 LLM | 外部 CLI 経由のモデルを `BaseLLM` 実装としてラップして注入 |
| 注入シーム | `config[llm]` |
| 実 LLM 呼び出し | 1 回 |
| 出力 | ['destination', 'recommendations', 'compliance_notes', 'disclaimer'] が非空。縮退理由なし |

本番の LLM は `AnthropicClient` 等で、これは検証用シーム。テンプレの `src/` は変更していない（一時テストを生成→実行→削除する方式）。CI には入れていない（外部 CLI と鍵が要るため）。
