# Template Design Specification — SVC-C2-029

Business Travel Hotel & Plan Recommendation Agent（出張ホテル・プラン推薦エージェント）

## Position in AgentCore Architecture

- **Agent Class**: `BusinessTravelHotelAgent`
- **L1 Base**: `AgentBaseGraph`（L1 直継承。`AutonomousBaseGraph` ではない — 自律 think-act ループは不要）
- **Pattern**: DocGenerationAgent 型（Web 横断取得 → 規程一次フィルタ → LLM 価値判断リランク＋推薦生成）。
  なお **DocGenerationAgent は実装 shape であって Cat ではない**（Cat 軸と実装パターン軸は直交）。
- **Cat**: Cat 2（出張規程を踏まえたホテル推薦という specific job-to-be-done を orchestrate）
- **Three-Layer Separation**:
  - State: flat TypedDict composition（primitives + JSON-serializable のみ。Pydantic 不可 — msgpack 非互換）
  - Node: L1 inheritance（Template Method: `execute(self, state) -> dict` のみ override・部分 dict 返却）
  - Graph: composition（`register_nodes()` で backbone スロットにノードを割当）

> **設計上の核（moat）**: ①**顧客固有の出張規程を KB 化して"踏まえた上で"**、②**社員に代わって
> 価値判断で宿を序列付けする**こと。単純な価格・距離ソート（純関数）では、「ポイント実質お得」
> 「大浴場で出張の疲れに効く」「会場から実際に行きやすい」等の複数の弱いシグナルを、会社の規程
> コンテキストと合わせて統合できない。この「規程 KB ＋ 価値判断リランク」は compliance × 推薦系
> （経費・調達ベンダー選定 等）へ横展開できる再利用パターンの原型。

> **スコープと責務境界（重要）**: 本エージェントは **公開情報に基づく候補提示＋予約ページ URL 誘導**まで。
> **最終的な価格確認・規程適合の最終判断・予約はユーザーが URL 先で行う**。したがって本エージェントは
> 「表示価格＝実支払額」を保証しない（参考価格・規程目安として提示）。実支払額のズレ（法人レート・
> 税サービス料・ポイント）はユーザー／経費側の責務でありスコープ外。規程フィルタは**明白な規程外
> （承認外ベンダー・上限の大幅超過）を落とす一次フィルタ**であり、精算可否の完全保証は行わない。

## Architecture Overview

Cat 2 = 外側 `AgentBaseGraph`（固定 5 ノード backbone）＋ `main` スロットに `GraphNode`
（内側 `HotelRecommendationWorkflow` を `src/graph/domain_workflow_graph.py` で実装）。

### 2 モード（テナント運用）

| mode | 用途 | 頻度 |
|---|---|---|
| `onboard_policy` | テナント初回のみ。顧客の出張規程文書（PDF/doc）を受け取り、**規程 KB（ベクトル DB）を構築**。以降そのテナントでは不要 | 初回1回 |
| `recommend`（既定） | 実行時。KB から該当規程条項を retrieve → Web 横断でホテル取得 → 一次フィルタ → LLM リランク → 出力 | 毎回 |

> `mode` は `request_params.mode` で判定。`onboard_policy` 時は WebFetch/Filter/Rerank をスキップし
> PolicyKBBuild のみ実行する（同一テンプレ内 2 モード。テンプレは分割しない）。

**内側ワークフローのモード分岐実装方針（M-01）**

内側 `HotelRecommendationWorkflow`（BaseGraph）は、モードを **`add_conditional_edges` による分岐**で
実装する（ノード内自己判定で全ノードを走らせて途中 skip する方式は取らない — S-4 監査が
「実行された副作用ノード」を正確に表さなくなるため）。

```
START → route_by_mode ─┬─[mode==onboard_policy]→ PolicyKBBuild ──────────────→ END
                       └─[mode==recommend]────→ PolicyLoad → WebFetch →
                                                 PolicyFilter → SoftRerank →
                                                 OutputAssemble → END
```

- `route_by_mode` は `state["request_params"]["mode"]`（既定 `recommend`）を読み、分岐先ノード名を返す
  条件関数。`add_conditional_edges(START, route_by_mode, {"onboard_policy": "policy_kb_build",
  "recommend": "policy_load"})` で配線する。
- 両分岐は独立に END へ合流し、`get_output()` が**モードに応じた返却キー**を組む。

**`get_output()` / `merge_output()` の 2 モード別返却キー（M-01）**

| mode | inner `get_output()` が返すキー | 外側 `merge_output()` が昇格する外側 State キー |
|---|---|---|
| `onboard_policy` | `{kb_status, status}` | `kb_status`, `status` |
| `recommend` | `{filtered_candidates, ranked_recommendations, compliance_notes, status}` | `filtered_candidates`, `ranked_recommendations`, `compliance_notes`, `status` |

- `merge_output()` は `sub_result` に含まれるキーだけを外側 delta として返す（存在しないキーは触らない）
  ため、モード差はキーの有無で自然に吸収される（分岐フラグを別途持たない）。
- `onboard_policy` 時は `ranked_recommendations` が無いので、外側 `PostProcessNode` は
  「KB 構築完了メッセージ」を組み、**S-3 の ID 突合は `ranked_recommendations` 不在時スキップ**
  （突合対象が無い＝幻覚も起き得ないため。PII スキャンは実行）。

### AGENTIC STAR Marketplace entry adapter

リポジトリ直下の `cli.py` は既存 `Graph` を継承した `MarketplaceGraph` を公開する。Marketplace
runner が渡す `input_context={"conversation_history": [...]}` は S-2 の走査対象外の自由文を含むため、
一切マージせず破棄する。State に渡す `input_context` は adapter が所有する次の値だけで組み直す。

| State key | deployment environment | 宣言元 |
|---|---|---|
| `tenant_id` | `STG_DEFAULT_COMPANY_ID` | `src.entry_adapter.STG_DEFAULT_TENANT_ENV` |
| `org_id` | `STG_DEFAULT_PROJECT_ID` | `src.entry_adapter.STG_DEFAULT_ORG_ENV` |

`cli.py` の `SCOPE_KEYS` と上表の対応は `src/entry_adapter/` の公開定数から構築する。未設定、空文字、
空白のみ、または空白を含む値は `_scope_from_environment()` が拒否する。この検証は
`MarketplaceGraph.__init__()` で行うため compile 前に fail closed し、`provision_secrets()` と各 invoke
でも再検証する。`mode` は常に `recommend` として adapter が付与するため、書込を伴う
`onboard_policy` は Marketplace から到達できない。

Marketplace の caller trust は `VERIFIED_EXTERNAL`、HITL は無効に固定する。LLM は credential-free な
facade を compile 前に inner graph へ注入し、Azure 接続値を State やインスタンスへ保持しない。
`AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_DEPLOYMENT` は
`provision_secrets()` で起動時検証し、実際の `AzureOpenAIClient` と接続値は LLM 呼出ごとに解決する。
SDK 1.0.0/1.0.1 に Marketplace bootstrap、environment provider、Azure client がない場合は adapter 内の
互換実装を使う。

### Node Configuration（外側 backbone）

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | セッション初期化（framework） | user_input | node_history | InitializeNode (default) |
| pre_process | 入力検証・NL クエリ→構造化パラメータ抽出（出張先/日程/per-diem/tier/venue/mode） | user_input | validated_input, request_params | `PreProcessNode`（S-2 hook） |
| main | 内側 workflow 呼出（PolicyLoad→Fetch→PolicyFilter→Rerank→Output） | validated_input | ranked_recommendations, compliance_notes, status | `HotelRecommendationGraphNode`（GraphNode） |
| post_process | Markdown/JSON 整形・予約 URL 埋込・S-3 保全検証 | ranked_recommendations | result, output | `PostProcessNode`（S-3 hook） |
| finalize | 出力射影・終了（framework） | result | output | FinalizeNode (default) |

### 内側 workflow（`HotelRecommendationWorkflow`）ノード

| Step | Node | 主要ロジック | 出力 |
|---|---|---|---|
| 0 | `PolicyKBBuildNode`（`onboard_policy` 時のみ） | 顧客規程文書をチャンク化し**規程 KB（ベクトル DB）へ格納**。以降そのテナントで再利用 | kb_status |
| 1 | `PolicyLoadNode` | 規程 KB から該当社員の役職×地域×条件で**規程条項を retrieve** → 構造化制約に落とす | policy_constraints |
| 2 | `WebFetchNode` | **Web 検索ツール**で OTA/公式/旅館サイト横断にホテル候補・公開価格・アメニティ・予約ページ URL を取得（単一 OTA に閉じない） | raw_candidates |
| 3 | `PolicyFilterNode` | 明白な規程外（承認外ベンダー・上限の大幅超過等）を落とす**一次フィルタ**（決定論）。判定不能はフィルタせず `needs_review` タグ付与 | filtered_candidates, filter_notes |
| 4 | `SoftRerankNode` | 規程を踏まえ LLM が価値判断でリランク＋推薦理由生成（フィルタ済み候補のみを対象） | ranked_recommendations |
| 5 | `OutputAssembleNode` | TOP3-5 を予約ページ URL・参考価格・残予算目安・規程メモ付きで整形 | output（inner get_output） |

> 旅行商品セマンティック検索テンプレートは **ホテルに限定して**ステップ2 `WebFetchNode` +
> pre_process の NL 構造化抽出に内包。航空券・旅程等には広げない。

**規程 KB クライアントシームの最小インターフェース（M-02）**

`PolicyKBBuildNode` / `PolicyLoadNode` は、KB バックエンド（ベクトル DB）に**直接依存せず**
`src/services/policy_kb.py` の注入シーム経由でアクセスする（テスト時に InMemory double へ DI 差替可能に）。
最小 IIF（バックエンド選定は Open Design Questions #2 のまま）:

```python
class PolicyKB(Protocol):
    def build(self, tenant_id: str, docs: list[str]) -> dict:
        """規程文書をチャンク化・埋め込み・格納。戻り値 = {"chunks": int, "status": "ok"}（kb_status 相当）"""
    def retrieve(self, tenant_id: str, query: str, top_k: int = 8) -> list[dict]:
        """該当規程条項を検索。戻り値 = [{"clause_id": str, "text": str, "score": float}, ...]"""
```

- `tenant_id` は `config["configurable"]["invocation_context"]` 由来（State に入れない）。
- `PolicyLoadNode` は `retrieve()` 結果を決定論コードで `policy_constraints`（per-diem 目安・
  承認ベンダー・tier ルール）に構造化する（LLM に規程解釈させない）。
- KB アクセスキーは `ctx.secrets.require("POLICY_KB_KEY")` で取得。

**`SoftRerankNode` の LLM 価値判断軸（L-02）**

LLM リランクプロンプトは以下の主要軸を、下記の**優先順**で重み付けする（詳細プロンプトは impl で確定）:

1. **規程準拠度**（needs_review タグの有無・規程目安との近さ）— 最優先
2. **実質コスト**（表示価格＋ポイント実質還元を加味した"お得さ"。参考値）
3. **会場近接**（会議会場からの移動しやすさ）
4. **出張適性アメニティ**（大浴場・朝食・Wi-Fi・ランドリー等、出張の疲労軽減に効くもの）
5. **キャンセル柔軟性**（出張は変更が起きやすいため）

> 軸 1 が最優先だが、`PolicyFilterNode` で明白な規程外は既に落ちているため、ここでの規程準拠度は
> 「グレーゾーン（needs_review）を下げる」程度の調整に留まる。純関数ソートでは軸 2〜5 の文脈統合が
> できない点が Agent 正当性の根拠。

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (status=retry, max 3 — 初版は基本未使用)
                                          pre_process

main（HotelRecommendationGraphNode.execute）内:
  [onboard_policy] PolicyKBBuild
  [recommend]      PolicyLoad → WebFetch → PolicyFilter → SoftRerank → OutputAssemble
```

### State Definition

**スコープ凡例**: `inner` = 内側 `HotelRecommendationWorkflow` の作業用 State（外側へ出さない）。
`outer` = `merge_output()` で外側 State に**明示的に昇格**するフィールド（外側 `PostProcessNode` の
S-3 が読む）。S-3 の ID 突合に必要な `filtered_candidates` と `ranked_recommendations` は
**必ず outer へ昇格**する（昇格しないと外側 S-3 が実装不能になるため）。

| Field | Type | Purpose | スコープ |
|-------|------|---------|----------|
| `user_input` | `str` | 生入力（出張条件の自然文/構造化） | outer（✅ 必須） |
| `validated_input` | `str` | 検証済み入力（pre_process が設定） | outer（✅ 必須） |
| `request_params` | `dict` | 正規化パラメータ（destination/dates/per_diem/tier/venue/mode） | outer（✅ 必須） |
| `policy_constraints` | `dict` | KB から retrieve し構造化した出張規程（該当条項・per-diem 目安・承認ベンダー・tier） | inner |
| `raw_candidates` | `list[dict]` | Web 横断取得の生ホテル候補（公開価格・URL・アメニティ） | inner |
| `filtered_candidates` | `list[dict]` | 一次フィルタ通過候補。**S-3 ID 突合の参照集合** | **outer**（merge_output で昇格） |
| `filter_notes` | `list[dict]` | 除外/needs_review の理由（監査・出典用） | inner（監査は S-4 で emit） |
| `ranked_recommendations` | `list[dict]` | LLM リランク済み TOP3-5（理由・予約 URL・参考価格付き） | **outer**（merge_output で昇格） |
| `compliance_notes` | `str` | 規程メモ（compliant目安 / needs_review。**完全保証でなく目安**） | **outer**（merge_output で昇格） |
| `kb_status` | `str` | onboard_policy 時の KB 構築結果 | **outer**（onboard 時のみ） |
| `result` | `dict\|str` | post_process が組む最終成果物 | outer（✅ 必須） |
| `output` | `dict` | finalize 射影の最終出力 | outer（✅ 必須） |
| `status` | `str` | AgentStatus 値（success / error / retry） | outer（✅ 必須） |

> **`merge_output()` 契約**: 内側 `get_output()` が `{filtered_candidates, ranked_recommendations,
> compliance_notes, status}`（onboard 時は `{kb_status, status}`）を返し、
> `HotelRecommendationGraphNode.merge_output()` がこれらを外側 State delta として返す
> （変更キーのみ・full state 返却禁止）。これにより外側 `PostProcessNode` の S-3 が
> `ranked_recommendations` の各 ID を `filtered_candidates` と突合できる。

**`request_params` の dict スキーマ（L-01）**

`PreProcessNode` が NL 入力から抽出し、以降のノードが参照するキー:

| key | type | 説明 | 例 |
|---|---|---|---|
| `mode` | `str` | `"recommend"`（既定）/ `"onboard_policy"` | `"recommend"` |
| `destination` | `str` | 出張先都市 | `"大阪市"` |
| `check_in` / `check_out` | `str`(ISO8601) | 泊数の起終点 | `"2026-07-15"` |
| `per_diem` | `int\|null` | per-diem 目安（円/泊。null=規程 KB から解決） | `11000` |
| `tier` | `str` | 役職 tier（規程の tier ルール参照キー） | `"standard"` / `"executive"` |
| `venue` | `str\|null` | 会議会場住所（近接スコアの基準・任意） | `"大阪駅前ビル"` |
| `amenities` | `list[str]` | 希望アメニティ（任意） | `["大浴場","朝食"]` |
| `tenant_id` | `str` | 規程 KB のテナント識別（`ctx` 由来を写す・PII 不可） | `"acme-corp"` |

> `onboard_policy` モードでは `mode` と `tenant_id` ＋ 規程文書（別途 `validated_input`）のみ必須。
> `destination` 等は不要（分岐で WebFetch を通らないため）。

**State Constraints (mandatory):**
- Flat TypedDict のみ（primitives + JSON-serializable。ネストは dict/list まで）
- JWT・API キー・credential を State に置かない（checkpoint DB 漏洩）
- InvocationContext は `config["configurable"]` 経由のみ（State に入れない）
- Pydantic model・dataclass・任意 Python オブジェクト禁止（msgpack 非互換）
- Web 検索ツール・KB のアクセスキーは State に入れず `ctx.secrets.require("...")` で取得

## Memory / Multi-turn

- **`memory_enabled: true`（初版から有効）**: checkpointer を有効化し、マルチターン精緻化
  （「もっと安いのは？」「駅近で」「大浴場ありで」）を成立させる。session_id 単位で候補・規程
  コンテキストを保持し、再取得を最小化。
- **HITL は無効**（`hitl.enabled` は設定しない）: 本エージェントは「AI が提示 → ユーザーが URL 先で
  選ぶ」であり、人間承認の interrupt ゲートは不要。よって schema 1.4 の HITL フィールド・
  HITL 準拠の観点は N/A（`hitl.enabled` 不在のため skip）。
- **checkpoint safety（PB-5）**: State に credential/Pydantic を入れないため checkpoint は
  primitives のみ。resume 後も同一 session の候補・規程を再利用。

## Framework Utilization

### Shared Components Used
- [x] InvocationContext（correlation_id, session_id, permissions, credential handle）
- [x] ConnectionPolicy（Web 取得の retry/timeout 戦略）
- [x] SecurityViolationError
- [x] **S-2**: `_extra_security_gate_input()` — 出張条件入力の PII スキャン（社員名・社員番号等が
      自然文入力に混じり得るため）＋入力サイズ上限。`PreProcessNode`（FunctionNode）で拡張フック実装。
      **egress 方向の注意**: Web 検索へ渡すのは正規化済み検索語（都市・日程・価格帯・アメニティ）
      のみで、社員名/番号などの PII を外部へ送出しない（`request_params` に PII を含めない）。
- [x] **S-3**: `_extra_security_gate_output()` — **保全検証型**（preservation variant）:
      出力に**一次フィルタで落とした候補が混入していないこと**と**PII が残っていないこと**を検証。
      規程メモの欠落は reject。`PostProcessNode`（FunctionNode）で拡張フック実装。
      **不変条件（invariant）**: 出力 `ranked_recommendations` の各候補 ID は、必ず
      `filtered_candidates`（＝一次フィルタ通過集合）の ID に含まれる。S-3 はこれを ID 突合で検証する。
      - **enforcement（信頼でなく検証）**: LLM SoftRerank はあくまで**並べ替えと理由生成**のみを行い、
        候補集合を増やせない設計だが、LLM が `filtered_candidates` に存在しないホテルを混ぜた
        （幻覚した）場合は S-3 が ID 突合で検出する。
      - **ID 突合失敗時の動作（L-03）**: S-3 は **`SecurityViolationError` を raise せず、幻覚候補
        （`filtered_candidates` に無い ID）のみを出力から除外して続行**する（fail-open でなく
        "sanitize and continue"）。除外が発生したら `emit_trace_event("s3_hallucination_dropped",
        {...})` で監査記録し、除外後に候補が 0 件になった場合のみ `compliance_notes=needs_review` +
        「該当なし・検索ページ URL 誘導」に縮退する。**PII 検出は別扱いで、検出時は
        `SecurityViolationError` を raise**（PII 混入は続行不可）。この使い分け（幻覚=除外続行 /
        PII=raise）は縮退パス（Web 取得失敗→success+needs_review）の"止めずに縮退"方針と一貫する。
- [x] **S-4**: `emit_trace_event()` — 副作用ノードで発火（Web 取得＝`web_fetch`、
      LLM 呼出＝`llm_rerank`、規程フィルタ判定＝`policy_filter`、KB 構築＝`policy_kb_build`）。
      `node_start`/`node_complete`/`node_error` は `__call__` 自動発火のため execute 内で重複させない。

> **S-2/S-3 gate behaviour（ADR-017）:**
> - `PreProcessNode` / `PostProcessNode` = `FunctionNode` subclass → framework `@final` ゲートが
>   自動実行。ドメイン拡張は `_extra_security_gate_input/output()` のみ（`_security_gate_*` 直定義は禁止）。
> - `HotelRecommendationGraphNode` = `GraphNode` → S-2/S-3 は意図的 no-op passthrough（内側ノードの
>   ゲートが適用済み）。
> - 内側 workflow の各ノード = `BaseNode`/`FunctionNode` subclass → 該当ノードでゲート実装。

### Composition Pattern

- **Pattern**: GraphNode (subgraph)
- **Composition target**: `HotelRecommendationWorkflow`（`src/graph/domain_workflow_graph.py`）
- **Error propagation strategy — 2 レイヤに分けて定義（矛盾なし）**:
  - **レイヤ①（内側ノード内での想定内失敗）**: Web 取得失敗・0 件は `WebFetchNode` 内で
    **例外を送出せず catch** し、「検索クエリ済み検索ページ URL」を 1 件の縮退候補として
    `raw_candidates` に積む（`degraded=true` フラグ付き）。この縮退候補も一次フィルタを通し、
    価格確認不能のため `compliance_notes=needs_review` とする。inner graph は正常終了
    （inner `get_output().status=success`）。
  - **レイヤ②（想定外の内側例外）**: 上記で catch されない予期せぬ例外は `GraphNode`
    `error_strategy="propagate"` により `SubgraphError` として外側へ再送出＝fail fast。
  - つまり「propagate」はレイヤ②の未捕捉例外に対する既定、「search URL 誘導」はレイヤ①の
    想定内失敗のノード内ハンドリングで、**別レイヤ**のため矛盾しない。
- **backbone の retry（`status="retry"`）と GraphNode `propagate` の関係**（別レイヤ）:
  - backbone 図の `main → {route} → pre_process (retry, max 3)` は **framework 固定の route**。
    これは `main` が **例外でなく** `status="retry"` を**正常に返した**ときだけ発火する。
    本テンプレ初版は retry を積極利用せず、取得失敗はレイヤ①の縮退で吸収するため `status="retry"`
    は基本返さない。
  - 一方 `error_strategy="propagate"` は**未捕捉例外**を `SubgraphError` として送出し graph を
    fail-fast 終了させる（route による retry は発火しない）。
  - よって「retry ループ」と「propagate」は**排他の別経路**（前者＝正常 status=retry、後者＝例外）で
    あり両立する。`main` は基本 `status="success"`（縮退時も success + needs_review）を返す。
- **縮退パスでも S-3 は有効**: 縮退候補は「検索ページ URL・価格未確認」と明示した
  **構造化候補**として出力に載る（架空の空室・価格を主張しない）。S-3 は ID 突合と
  「価格未確認である旨の規程メモが付いていること」を検証できる（縮退時こそ
  「規程準拠と誤認させない」ことをゲートで担保）。
- **pre→main 無条件辺**: `GraphNode.execute` で上流 `status="error"` は必ずパススルー。

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only（`agents/base/` 不要）
- [x] Web 検索クライアント・LLM クライアント・KB クライアントは `src/services/` に注入シームとして実装
      （`framework.llm` は wheel に無いため `src/services/llm_client.py` で自前化）

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | 決定論パイプライン＋単発 LLM リランク。自律ループ不要 |
| Composition pattern | Standalone（3 スロット直実装） | GraphNode（inner workflow） | **GraphNode** | 多段ドメインワークフローを inner graph に隠蔽 |
| ホテル取得手段 | 単一 OTA API（楽天等） | **Web 検索ツール横断** | **Web 検索横断** | 単一 OTA に閉じると在庫が偏る。横断で広く拾う。取得の規約リスクも低い（送客＝OTA/宿に利） |
| 規程フィルタの強さ | 精算却下を防ぐ厳格ハードゲート | **明白な規程外のみの一次フィルタ** | **一次フィルタ** | 最終価格・予約判断はユーザーが URL 先で行うため、完全保証は不要・過剰 |
| 規程データ供給 | 顧客が構造化 JSON 提供 | **文書→KB 構築の 2 モード** | **KB 2 モード** | 散文文書のまま投入でき、初回だけでそのテナントは以降不要 |
| 規程判定の主体 | LLM に規程解釈させる | **KB retrieve＋決定論フィルタ** | **KB retrieve＋決定論** | 規程解釈を LLM に丸投げしない（KB は条項を引くまで） |
| memory / HITL | 有効 / 有効 | — | **memory=有効 / HITL=無効** | マルチターン精緻化は核 UX。人間承認ゲートは不要（提示のみ） |
| 対象範囲 | ホテル＋航空券＋旅程 | **ホテルに集約** | **ホテル集約** | 旅行商品セマンティック検索の内包はホテルに限定。スコープを絞る |

## Open Design Questions（実装フェーズで確定）

1. **Web 検索ツールの選定**: 現在の production wiring には実ホテル在庫 API / Web 検索 provider が
   接続されていない。既定 `StaticHotelSearch` の catalog は空で、縮退 URL の host
   `example-hotel-search.invalid` は予約 RFC の `.invalid` ドメインである。接続先となる実 API がないため
   AGENTIC STAR の開発環境でも到達性試験の対象自体がなく、2026-08-31 のローカル確認でも名前解決不能
   `Could not resolve host`（curl exit 6）となった。したがって現状の Marketplace 経路は実在庫へ
   到達不能であり、架空データへの置換は行わない。実 provider の選定・接続と DEV 到達性確認が
   リリース前の blocker である。
2. **規程 KB スキーマ**: 顧客規程文書のチャンク化粒度・retrieve クエリ（役職×地域×条件）の
   構造化方針。onboard_policy モードの入力フォーマット。
3. **amenity weighting のサンプルプロンプト**: SoftRerank の価値判断プロンプトに、
   大浴場・ポイント還元・会場近接の重み付け例を追加する。
