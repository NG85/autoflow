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
    
    def _build_summary_prompt(self, content: str, title: Optional[str] = None,
                         sales_name: Optional[str] = None,
                         account_name: Optional[str] = None,
                         contact_name: Optional[str] = None,
                         contact_position: Optional[str] = None) -> str:
        """构建会议纪要总结的提示词"""
        
        # 构建核心背景信息
        background_info = ""
        if any([sales_name, account_name, contact_name, contact_position]):
            background_info = "**背景信息：**\n"
            if sales_name:
                background_info += f"• 销售人员：{sales_name}\n"
            if account_name:
                background_info += f"• 拜访客户：{account_name}\n"
            if contact_name:
                contact_info = f"• 拜访对象：{contact_name}"
                if contact_position:
                    contact_info += f"（{contact_position}）"
                background_info += contact_info + "\n"
            background_info += "• 文档类型：销售拜访记录中的会议文件\n"
            background_info += "• 用途：推送给销售人员、销售Leader和销售VP的拜访记录信息卡片\n\n"
        
        return f"""你是一位专业的销售会议记录专家，请基于以下背景信息，对销售拜访记录中的会议文档内容进行高质量的会议纪要总结。

{background_info}文档标题: {title or "未提供标题"}

**重要说明：**
这是一份销售拜访记录中的会议文档，将推送给销售人员本人、其Leader以及销售VP。请结合背景信息，生成一份信息完整、层次清晰、便于各层级快速理解的会议纪要。

**会议纪要要求：**

1. **内容结构**（按重要性排序）：
   - 会议主题和核心议题（结合拜访背景理解）
   - 参会人员（如文档中明确提及，结合拜访对象信息）
   - 客户需求和关注点（重点关注商机价值和紧迫性）
   - 达成的共识（体现销售推进成果）
   - 下一步计划（明确责任人、时间节点，便于跟进）

2. **格式要求**（使用最基础的格式，确保兼容性）：
   - 使用 `**标题**` 加粗标题
   - 使用 `•` 符号作为列表标记
   - 使用 `---` 作为分隔线
   - 避免使用表格、特殊符号、复杂缩进
   - 保持简洁的段落结构

3. **表达与逻辑**：
   - 使用简洁、专业的商务语言
   - 结合拜访记录背景，准确理解文档内容
   - 下一步计划格式：任务内容（负责人，截止时间）
   - 每个任务描述必须清晰、可执行

4. **输出示例**：

**会议主题：** [结合拜访背景的核心主题]
**参会人员：** [姓名1]、[姓名2]

---
**客户需求与关注点：**
• [需求1]
• [需求2]

**达成的共识：**
• [共识1]
• [共识2]

---
**下一步计划：**
• [计划1]（[负责人]，[截止时间]）
• [计划2]（[负责人]，[截止时间]）

5. **其他要求**：

- 字数限制：300-400字
- 使用简洁、清晰的表达
- 确保在飞书富文本中正常显示
- 内容要便于三个层级快速理解：销售关注具体任务，Leader关注推进状态，VP关注商机价值

文档内容：
{content}

请结合背景信息，按照上述格式生成会议纪要，确保内容准确、简洁、可读性强，并便于各层级人员快速了解项目进展和后续安排。
"""
