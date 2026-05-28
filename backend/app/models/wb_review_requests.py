"""CRM 回写：与 review（cache 提交等）相关的请求体。

与 ``wb_visit_requests`` 中的拜访记录回写分离。是否调用网关由 ``CRM_WRITEBACK_REVIEW_ENABLED`` 控制；路径与单条 JSON 约定见 ``CRM_WRITEBACK_REVIEW_PATH`` 与
``CrmBusinessOpportunityUpdateBody`` 约定；具体 CRM 变体由网关在服务端路由，autoflow 不区分厂商前缀。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class ReviewOpportunityWritebackOp(BaseModel):
    """单条 review 商机可编辑字段变更（与 cache 提交等审计 ops 项结构一致）。"""

    op: str = Field(default="update", description="操作类型，固定为 update")
    opportunity_id: str = Field(..., description="商机唯一标识（CRM / 本地对齐键）")
    main_unique_id: str = Field(default="", description="主表 crm_review_opp_branch_snapshot.unique_id")
    cache_unique_id: str = Field(default="", description="草稿表 cache 行 unique_id")
    before_editable: Dict[str, Any] = Field(default_factory=dict, description="变更前可编辑字段")
    after_editable: Dict[str, Any] = Field(default_factory=dict, description="变更后可编辑字段")
    before_submit_sync: Dict[str, Any] = Field(
        default_factory=dict,
        description="变更前提交元数据（最后修改人、计数等）",
    )
    after_submit_sync: Dict[str, Any] = Field(
        default_factory=dict,
        description="变更后提交元数据",
    )


class CrmBusinessOpportunityUpdateBody(BaseModel):
    """网关 ``CRM_WRITEBACK_REVIEW_PATH`` 单条 POST 的字段约定（camelCase）；实际请求可仅含 ``id`` 与有变化的子集。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="商机 ID")
    sale_stage_id: str | None = Field(default=None, serialization_alias="saleStageId")
    prediction_type: str | None = Field(default=None, serialization_alias="predictionType")
    expected_sign_month: str | None = Field(default=None, serialization_alias="expectedSignMonth")
    money: float = Field(default=0.0, serialization_alias="money")
    reason: int | None = Field(default=None, serialization_alias="reason")
    reason_desc: str | None = Field(default=None, serialization_alias="reasonDesc")
    lost_order_competitors: str | None = Field(default=None, serialization_alias="lostOrderCompetitors")


class ReviewOpportunityWritebackBatchRequest(BaseModel):
    """Review 商机回写：进程内聚合 ``ops``；HTTP 对每条 op 单独 POST 网关约定 JSON（非批量包体）。"""

    session_id: str = Field(..., description="review session unique_id")
    snapshot_period: str = Field(..., description="快照周期，与 session.period 一致")
    ops: List[ReviewOpportunityWritebackOp] = Field(default_factory=list, description="本批有字段变化的项")
    partial_fail: bool = Field(
        default=True,
        description="历史字段；当前 HTTP 为逐条 POST，无批量包体。",
    )
    source: str = Field(
        default="autoflow_review_opportunity_writeback",
        description="调用来源（日志/审计）；网关路由不依赖此前缀。",
    )
