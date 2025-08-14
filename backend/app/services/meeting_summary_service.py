"""
会议纪要生成服务

使用LLM对文档内容进行会议纪要总结
"""

import logging
import json
from typing import Optional, Dict, Any
from app.core.config import settings
from app.crm.save_engine import call_ark_llm

logger = logging.getLogger(__name__)


class MeetingSummaryService:
    """会议纪要生成服务"""
    
    def generate_meeting_summary(self, content: str, title: Optional[str] = None, sales_name: Optional[str] = None, account_name: Optional[str] = None, contact_name: Optional[str] = None, contact_position: Optional[str] = None) -> Dict[str, Any]:
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
            prompt = self._build_summary_prompt(content, title, sales_name, account_name, contact_name, contact_position)
            
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
    你是一位专业销售会议纪要生成专家，请基于上述背景信息与以下文档内容，生成一份可直接用于飞书卡片推送的会议纪要。

    **生成要求：**
    1. **内容重点**  
    - 首次拜访：需求挖掘、关系建立、探索方向  
    - 多次拜访：进展对比、新需求变化、成果推进  
    - Call High：高层决策、战略合作、关键决策点  
    - 普通拜访：业务细节、技术需求、执行计划  
    - 无商机：需求探索、关系维护、机会识别  
    - 有商机：推进状态、里程碑、执行计划

    2. **结构格式（严格遵守）**  
    **会议主题：** [主题]  
    **参会人员：** [姓名1]、[姓名2]  
    ---
    **客户需求与关注点：**  
    • [需求1] - [商机价值/时间节点]（如有）  
    • [需求2] - [商机价值/时间节点]（如有）  

    **达成的共识：**  
    • [共识1]  
    • [共识2]  

    **下一步计划：**  
    • [任务1]（负责人，截止时间）  
    • [任务2]（负责人，截止时间）  

    3. **输出规则**  
    - 字数控制在 300-400 字  
    - 仅输出纪要正文，不包含拜访记录原字段  
    - 使用简洁、专业的商务语言  
    - 避免编造信息，如无对应信息则省略该项  
    - 确保 Markdown 在飞书富文本中正常显示  
    - 内容需同时满足销售、Leader、VP 三个角色的关注点

    文档标题：{title or "未提供标题"}  
    文档内容：
    {content}
    """

