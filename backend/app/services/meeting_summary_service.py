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
    
    def generate_meeting_summary(self, content: str, title: Optional[str] = None) -> Dict[str, Any]:
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
            prompt = self._build_summary_prompt(content, title)
            
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
    
    def _build_summary_prompt(self, content: str, title: Optional[str] = None) -> str:
        """构建会议纪要总结的提示词"""
        return f"""你是一位专业的销售会议记录专家，请对以下文档内容进行高质量的会议纪要总结，严格适配飞书富文本卡片格式。

文档标题: {title or "未提供标题"}

**会议纪要要求：**

1. **内容结构**（按重要性排序）：
   - 会议主题和核心议题
   - 参会人员（如文档中明确提及）
   - 客户需求和关注点
   - 达成的决定和共识
   - 具体行动项（任务、负责人、时间节点）
   - 下一步计划

2. **格式要求**（使用最基础的格式，确保兼容性）：
   - 使用 `**标题**` 加粗标题
   - 使用 `•` 符号作为列表标记
   - 使用 `---` 作为分隔线
   - 避免使用表格、特殊符号、复杂缩进
   - 保持简洁的段落结构

3. **表达与逻辑**：
   - 使用简洁、专业的商务语言。
   - 关键信息（如截止时间、负责人）必须加粗。
   - 每个行动项的任务描述必须清晰、可执行。

4. **输出示例**：

**会议主题：** [核心主题]
**参会人员：** [姓名1]、[姓名2]

---
**客户需求：**
• [需求1]
• [需求2]

**达成的决定：**
• [决定1]
• [决定2]


---
**行动项：**
• [任务1] - 负责人：[姓名] - 截止时间：[时间]
• [任务2] - 负责人：[姓名] - 截止时间：[时间]

**下一步计划：**
• [后续安排]

5. **其他要求**：

- 字数限制：300-400字
- 使用简洁、清晰的表达
- 确保在飞书富文本中正常显示


文档内容：
{content}

请按照上述格式生成会议纪要，确保内容准确、简洁、可读性强。
"""
