from typing import Optional, Dict, Any
from datetime import datetime
from sqlmodel import Field, Column, Relationship, SQLModel, Text, JSON
from sqlalchemy import DECIMAL


class CRMOrder(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
    
    """订单表"""
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    unique_id: Optional[str] = Field(nullable=True, max_length=255, description="唯一性ID（必填）")
    signing_date: Optional[datetime] = Field(nullable=True, description="签约日期")
    service_duration_months: Optional[str] = Field(nullable=True, max_length=255, description="服务时长/月（订阅/维保）")
    total_margin_percentage: Optional[str] = Field(nullable=True, max_length=255, description="Margin%(总代+经销）")
    customer_id: Optional[str] = Field(nullable=True, max_length=255, description="客户id")
    customer_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称（必填）")
    arr: Optional[str] = Field(nullable=True, max_length=255, description="ARR")
    maintenance_ratio: Optional[str] = Field(nullable=True, max_length=255, description="维保比例")
    renew_arr: Optional[str] = Field(nullable=True, max_length=255, description="Renew ARR")
    sales_type: Optional[str] = Field(nullable=True, max_length=255, description="销售类型")
    product_subscription_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="产品订阅金额")
    shipping_status: Optional[str] = Field(nullable=True, max_length=255, description="发货状态")
    shipping_address: Optional[str] = Field(nullable=True, max_length=255, description="收货地址")
    sales_order_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="销售订单金额")
    third_level_distributor: Optional[str] = Field(nullable=True, max_length=255, description="经销商三级")
    planned_payment_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="已计划回款金额")
    created_by: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="创建人")
    service_end_date_subscription_maintenance: Optional[datetime] = Field(nullable=True, description="服务结束日期（订阅/维保）")
    outsourcing_cost: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="外采（外包）成本")
    performance_accounting_sales_department: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="业绩核算销售所在部门")
    project_name: Optional[str] = Field(nullable=True, max_length=255, description="项目名称")
    expected_renewal_time_fy: Optional[str] = Field(nullable=True, max_length=255, description="应续约时间(FY)")
    new_arr: Optional[str] = Field(nullable=True, max_length=255, description="New ARR")
    owner: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="负责人")
    product_perpetual_license_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="产品永久授权金额")
    split_ratio: Optional[str] = Field(nullable=True, max_length=255, description="拆分比例")
    contract_archiving_status: Optional[str] = Field(nullable=True, max_length=255, description="合同归档状态")
    pending_payment_amount: Optional[str] = Field(nullable=True, max_length=512, description="待回款金额")
    invoice_status: Optional[str] = Field(nullable=True, max_length=512, description="开票状态")
    order_type: Optional[str] = Field(nullable=True, max_length=255, description="订单类型")
    is_general_agent: Optional[str] = Field(nullable=True, max_length=512, description="是否总代理")
    source: Optional[str] = Field(nullable=True, max_length=255, description="数据来源")
    contract_type: Optional[str] = Field(nullable=True, max_length=255, description="合同类型")
    reported_partner_name: Optional[str] = Field(nullable=True, max_length=255, description="报备合作伙伴名称")
    commission_info: Optional[str] = Field(nullable=True, max_length=255, description="提成信息")
    has_sub_agents: Optional[str] = Field(nullable=True, max_length=255, description="是否有下级代理商")
    man_day_service_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="人天服务金额")
    old_service_end_date: Optional[datetime] = Field(nullable=True, description="服务结束日期（旧）")
    owning_department: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="归属部门")
    maintenance_service_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="维保服务金额")
    partner_id: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    payment_status: Optional[str] = Field(nullable=True, max_length=512, description="回款状态")
    sales_order_amount_excluding_tax: Optional[str] = Field(nullable=True, max_length=255, description="销售订单金额（不含税）")
    arr_excluding_tax: Optional[str] = Field(nullable=True, max_length=255, description="ARR（不含税）")
    settle_type: Optional[str] = Field(nullable=True, max_length=255, description="结算方式")
    contract_name: Optional[str] = Field(nullable=True, max_length=255, description="合同名称")
    second_level_distributor: Optional[str] = Field(nullable=True, max_length=255, description="经销商二级")
    opportunity_id: Optional[str] = Field(nullable=True, max_length=255, description="商机id")
    opportunity_name: Optional[str] = Field(nullable=True, max_length=255, description="商机名称")
    quote_id: Optional[str] = Field(nullable=True, max_length=255, description="报价单编号")
    total_payment_amount: Optional[float] = Field(sa_column=Column(DECIMAL(18, 2), nullable=True), description="累计回款金额")
    is_framework_order: Optional[str] = Field(nullable=True, max_length=255, description="是否框架下订单")
    first_level_distributor: Optional[str] = Field(nullable=True, max_length=255, description="经销商一级")
    service_start_date_subscription_maintenance: Optional[datetime] = Field(nullable=True, description="服务开始日期（订阅/维保）")
    previous_name: Optional[str] = Field(nullable=True, max_length=255, description="曾用名")
    owner_department: Optional[str] = Field(nullable=True, max_length=255, description="负责人主属部门")
    creation_time: Optional[datetime] = Field(nullable=True, description="创建时间")
    sales_order_number: Optional[str] = Field(nullable=True, max_length=255, description="销售订单编号")
    invoice_completion_status: Optional[str] = Field(nullable=True, max_length=255, description="开票完成状态")
    contracting_party: Optional[str] = Field(nullable=True, max_length=255, description="签约主体")
    delivery_acceptance_progress: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="交付/验收进展")
    product_type: Optional[str] = Field(nullable=True, max_length=255, description="产品类型")
    renewal_type: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="续约类型")
    delivery_comment: Optional[str] = Field(nullable=True, max_length=255, description="发货备注")
    profit_statement: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="利润表")
    opportunity_number: Optional[str] = Field(nullable=True, max_length=255, description="商机编号")
    last_modified_time: Optional[datetime] = Field(nullable=True, description="最后修改时间")
    life_status: Optional[str] = Field(nullable=True, max_length=255, description="生命状态")
    framework_agreement_id: Optional[str] = Field(nullable=True, max_length=255, description="框架协议名称")
    acv: Optional[str] = Field(nullable=True, max_length=255, description="ACV")
    contracting_partner: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴（签约方）")
    renewal_status: Optional[str] = Field(nullable=True, max_length=255, description="续约状态")
    resource: Optional[str] = Field(nullable=True, max_length=255, description="来源")
    contract_attribute: Optional[str] = Field(nullable=True, max_length=255, description="合同属性")
    remark: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="备注")
    last_modified_by: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON, nullable=True), description="最后修改人")
    delivery_time: Optional[datetime] = Field(nullable=True, description="发货时间")
    sales_organization: Optional[str] = Field(nullable=True, max_length=255, description="销售组织")

    __tablename__ = "crm_orders"
    
    opportunity: Optional["CRMOpportunity"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(CRMOrder.opportunity_id==CRMOpportunity.unique_id)",
            "foreign_keys": "[CRMOrder.opportunity_id]",
            "viewonly": True
        }
    )