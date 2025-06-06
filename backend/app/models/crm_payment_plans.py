from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlmodel import Field, Column, Relationship, SQLModel, Text, JSON
from sqlalchemy import DECIMAL, Boolean


class CRMPaymentPlan(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
    
    """回款计划表"""
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    unique_id: Optional[str] = Field(nullable=True, max_length=255, description="唯一性ID（必填）")
    actual_payment_fiscal_quarter: Optional[str] = Field(nullable=True, max_length=512, description="实际回款日期-财年&季度")
    lock_rule: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="锁定规则")
    action_tag: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="Action tag-多选")
    extend_obj_data_id: Optional[str] = Field(nullable=True, max_length=255, description="extend_obj_data_id")
    remind_time: Optional[str] = Field(nullable=True, max_length=255, description="提前几日提醒")
    life_status_before_invalid: Optional[str] = Field(nullable=True, max_length=255, description="作废前生命状态")
    order_amount: Optional[str] = Field(nullable=True, max_length=255, description="销售订单金额")
    contract_party: Optional[str] = Field(nullable=True, max_length=255, description="签约方")
    plan_payment_status: Optional[str] = Field(nullable=True, max_length=255, description="状态")
    owner_department: Optional[str] = Field(nullable=True, max_length=255, description="负责人所在部门")
    latest_plan_payment_fiscal_quarter: Optional[str] = Field(nullable=True, max_length=512, description="最新计划回款日期-财年&季度")
    plan_payment_method: Optional[str] = Field(nullable=True, max_length=255, description="计划回款方式")
    plan_payment_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="计划回款金额（元）")
    pending_payment_amount: Optional[str] = Field(nullable=True, max_length=512, description="待回款金额")
    plan_payment_ratio: Optional[str] = Field(nullable=True, max_length=512, description="计划回款占比")
    lock_status: Optional[str] = Field(nullable=True, max_length=255, description="锁定状态")
    first_plan_payment_fiscal_quarter: Optional[str] = Field(nullable=True, max_length=512, description="首次计划回款日期-财年&季度")
    create_time: Optional[datetime] = Field(nullable=True, description="创建时间")
    booking_fiscal_year: Optional[str] = Field(nullable=True, max_length=512, description="Booking财年")
    version: Optional[str] = Field(nullable=True, max_length=255, description="version")
    created_by: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="创建人")
    relevant_team: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="相关团队")
    data_own_department: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="归属部门")
    next_plan_description: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划说明")
    name: Optional[str] = Field(nullable=True, max_length=255, description="回款计划编号")
    order_id: Optional[str] = Field(nullable=True, max_length=255, description="销售订单编号")
    data_refresh_flag: Optional[str] = Field(nullable=True, max_length=255, description="刷数据")
    first_payment_overdue_days: Optional[str] = Field(nullable=True, max_length=512, description="首次回款计划-逾期天数-后台计算")
    approve_employee_id: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="当前审批人")
    remark: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="备注")
    contract_date: Optional[str] = Field(nullable=True, max_length=512, description="签约日期")
    origin_source: Optional[str] = Field(nullable=True, max_length=255, description="数据来源")
    lock_user: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="加锁人")
    actual_payment_date: Optional[datetime] = Field(nullable=True, description="实际回款日期")
    partner_id: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    overdue_reason: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="逾期归因")
    is_deleted: Optional[bool] = Field(sa_column=Column(Boolean, nullable=True), description="is_deleted")
    attachment: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="附件")
    actual_payment_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="实际回款金额（元）")
    overdue_payment_reason: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="逾期回款原因")
    latest_payment_overdue_days: Optional[str] = Field(nullable=True, max_length=512, description="最新回款计划-逾期天数")
    latest_plan_payment_date: Optional[datetime] = Field(nullable=True, description="最新计划回款日期")
    backend_process_status: Optional[str] = Field(nullable=True, max_length=255, description="后台处理状态")
    next_plan: Optional[str] = Field(nullable=True, max_length=255, description="下一步计划")
    out_owner: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="外部负责人")
    out_resources: Optional[str] = Field(nullable=True, max_length=255, description="外部来源")
    owner: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="负责人")
    last_modified_time: Optional[datetime] = Field(nullable=True, description="最后修改时间")
    life_status: Optional[str] = Field(nullable=True, max_length=255, description="生命状态")
    last_modified_by: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="最后修改人")
    out_tenant_id: Optional[str] = Field(nullable=True, max_length=255, description="外部企业")
    record_type: Optional[str] = Field(nullable=True, max_length=255, description="业务类型")
    overdue_description_and_next_plan: Optional[str] = Field(nullable=True, max_length=2048, description="逾期说明及下一步计划-刷数据至下一步计划说明")
    account_id: Optional[str] = Field(nullable=True, max_length=255, description="客户id")
    account_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称")
    plan_payment_time: Optional[datetime] = Field(nullable=True, description="首次计划回款日期")
    target_payment_date: Optional[datetime] = Field(nullable=True, description="Target 回款日期")
    order_by: Optional[str] = Field(nullable=True, max_length=255, description="order_by")
    first_plan_overdue_month: Optional[str] = Field(nullable=True, max_length=512, description="首次计划回款-逾期月份")
    contract_entity: Optional[str] = Field(nullable=True, max_length=255, description="签约主体")

    __tablename__ = "crm_payment_plans"
    
    order: Optional["CRMOrder"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(CRMPaymentPlan.order_id==CRMOrder.unique_id)",
            "foreign_keys": "[CRMPaymentPlan.order_id]",
            "viewonly": True
        }
    )