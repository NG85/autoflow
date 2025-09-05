"""
会议纪要生成服务

使用LLM对文档内容进行会议纪要总结
"""

import logging
from typing import Optional, Dict, Any
from app.crm.save_engine import call_ark_llm

logger = logging.getLogger(__name__)


class MeetingSummaryService:
    """会议纪要生成服务"""
    
    def generate_meeting_summary(
        self, 
        content: str,
        title: Optional[str] = None,
        sales_name: Optional[str] = None,
        account_name: Optional[str] = None,
        contact_name: Optional[str] = None,
        contact_position: Optional[str] = None,
        visit_date: Optional[str] = None,
        opportunity_name: Optional[str] = None,
        is_first_visit: Optional[bool] = None,
        is_call_high: Optional[bool] = None) -> Dict[str, Any]:
        """
        使用LLM生成会议纪要总结
        
        Args:
            content: 文档原文内容
            title: 文档标题（可选）
            
        Returns:
            Dict: 包含总结内容和状态的结果
        """
        try:
            # 构建提示词
            prompt = self._build_summary_prompt(content, title, sales_name, account_name, contact_name, contact_position, visit_date, opportunity_name, is_first_visit, is_call_high)
            
            # 调用LLM生成总结
            summary = call_ark_llm(prompt)
            
            return {
                "success": True,
                "summary": summary
            }
                
        except Exception as e:
            logger.error(f"生成会议纪要失败: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "summary": None
            }

    def _build_summary_prompt(
        self,
        content: str,
        title: Optional[str] = None,
        sales_name: Optional[str] = None,
        account_name: Optional[str] = None,
        contact_name: Optional[str] = None,
        contact_position: Optional[str] = None,
        visit_date: Optional[str] = None,
        opportunity_name: Optional[str] = None,
        is_first_visit: Optional[bool] = None,
        is_call_high: Optional[bool] = None
    ) -> str:
        """构建销售拜访会议纪要提示词（飞书卡片专用）"""

        background_info = ""
        if any([sales_name, account_name, contact_name, contact_position, visit_date, opportunity_name, is_first_visit, is_call_high]):
            background_info = "**背景信息（仅供理解，不在输出中显示）：**\n"
            if sales_name:
                background_info += f"• 销售人员：{sales_name}\n"
            if account_name:
                background_info += f"• 拜访客户：{account_name}\n"
            if contact_name:
                contact_info = f"• 拜访对象：{contact_name}"
                if contact_position:
                    contact_info += f"（{contact_position}）"
                background_info += contact_info + "\n"
            if visit_date:
                background_info += f"• 拜访日期：{visit_date}\n"
            if opportunity_name:
                background_info += f"• 商机名称：{opportunity_name}\n"
            if is_first_visit is not None:
                background_info += f"• 拜访类型：{'首次拜访' if is_first_visit else '多次拜访'}\n"
            if is_call_high is not None:
                background_info += f"• 拜访层级：{'Call High' if is_call_high else '普通拜访'}\n"
            background_info += "• 文档类型：销售拜访记录会议文件\n\n"

        return f"""{background_info}
## 角色定义
你是一位专业的销售会议纪要生成专家，专门为销售团队生成高质量的会议纪要，用于飞书卡片推送。

## 核心任务
基于提供的背景信息和文档内容，生成一份结构清晰、信息准确的会议纪要。

## 输出格式（严格遵守）
**会议主题：** [从文档中提取的核心主题]
**参会人员：** [提取的参会人员姓名]
---
**客户需求与关注点：**
• [具体需求1] - [商机价值/预算范围]（仅当原文明确提及预算/金额时标注）
• [具体需求2] - [商机价值/预算范围]（仅当原文明确提及预算/金额时标注）

**达成的共识：**
• [双方确认的共识点1]
• [双方确认的共识点2]

**下一步计划：**
• [具体任务1]（仅当原文明确提及负责人时标注，仅当原文明确提及时间时标注截止时间）
• [具体任务2]（仅当原文明确提及负责人时标注，仅当原文明确提及时间时标注截止时间）

## 内容生成规则
1. **内容重点**（根据背景信息调整）：
   - 首次拜访：重点挖掘客户需求、建立关系、探索合作方向
   - 多次拜访：对比进展、识别新需求变化、推进具体成果
   - Call High：关注高层决策、战略合作、关键决策点
   - 普通拜访：聚焦业务细节、技术需求、执行计划
   - 有商机：突出推进状态、关键里程碑、执行计划

2. **语言要求**：
   - 使用简洁、专业的商务语言
   - 避免口语化表达和冗余描述
   - 确保信息准确、逻辑清晰

3. **缺失信息处理**：
   - 如果某个部分完全没有相关信息，可以省略该部分
   - 如果只有部分信息，只输出确实存在的内容
   - 不要为了完整性而编造或推测信息

4. **字数控制**：300-400字

## 严格约束
- **禁止编造**：严格禁止编造任何时间、日期、截止时间、金额、人员、预算等信息
- **信息完整性**：如原文无相关信息，请省略对应字段，宁可信息不完整也不要编造
- **准确性优先**：确保所有信息都来源于原文，不得添加任何推测、假设或推断内容
- **格式规范**：确保Markdown格式在飞书富文本中正常显示
- **缺失处理**：如果某个部分完全没有相关信息，可以省略该部分或标注"暂无相关信息"

## 输出示例
**会议主题：** 产品演示与技术交流  
**参会人员：** 张三、李四、王五  
---
**客户需求与关注点：**
• 系统性能优化需求 - 预算50-80万
• 数据安全升级需求 - 预算30万  

**达成的共识：**
• 同意进行系统性能评估
• 确认数据安全是核心关注点

**下一步计划：**
• 提供详细技术方案（张三）
• 安排现场调研（李四）

---

## 待处理文档
**文档标题：** {title or "未提供标题"}  
**文档内容：**
{content}
"""