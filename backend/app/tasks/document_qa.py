import logging
import json
from typing import List, Dict, Any, Optional

from sqlmodel import Session

from app.celery import app as celery_app
from app.core.config import settings
from app.core.db import engine
from app.repositories.document_content import DocumentContentRepo
from app.models.document_contents import DocumentContent
from app.utils.ark_llm import call_ark_llm
from app.repositories.visit_record import visit_record_repo


logger = logging.getLogger(__name__)


def _split_content_into_chunks(content: str, max_chars: int = 8000) -> List[str]:
    """
    将大文档按照段落尽量切成不超过 max_chars 的片段。
    优先按空行分段，不足时再按字符硬切。
    """
    if len(content) <= max_chars:
        return [content]

    paragraphs = content.split("\n\n")
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # 段落本身就很长，直接硬切
        if len(para) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue

        # 正常累积段落
        added_len = len(para) + (2 if current else 0)
        if current_len + added_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += added_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _extract_qa_pairs_for_chunk(
    chunk: str,
    max_pairs: int,
    idx: int,
    total: int,
    visit_context: Optional[dict[str, str]] = None,
    max_retries: int = 3,
) -> tuple[List[Dict[str, str]], bool]:
    """
    对单个片段调用 LLM 抽取问答对。
    
    返回:
        (qa_pairs, ok)
        - qa_pairs: 当前片段抽取出的问答对列表
        - ok: 是否成功完成抽取（调用成功且JSON解析成功）
    """
    context_block = ""
    if visit_context:
        # 只注入关键的、对理解问答有帮助的字段
        context_block = json.dumps(
            {
                "recorder": visit_context.get("recorder") or "",
                "account_name": visit_context.get("account_name") or "",
                "opportunity_name": visit_context.get("opportunity_name") or "",
                "contact_name": visit_context.get("contact_name") or "",
                "is_first_visit": visit_context.get("is_first_visit") or "",
                "is_call_high": visit_context.get("is_call_high") or "",
            },
            ensure_ascii=False,
        )

    prompt = f"""
你是一名资深售前顾问和知识整理专家，需要从销售与客户的会议内容中抽取若干高质量的“问答对”（QA）。

【会议背景（仅供理解，不要臆造超出这些信息的内容）】
{context_block}
上述 JSON 字段含义：
- recorder: 本次拜访的销售/记录人姓名
- account_name: 客户公司名称
- opportunity_name: 相关商机/项目名称
- contact_name: 客户联系人信息（格式：姓名（职位），多个联系人用逗号分隔，如"张三（CTO）, 李四（CEO）"）
- is_first_visit: 是否首次拜访（是/否）
- is_call_high: 是否拜访关键决策人（是/否）

当前处理的内容是整场会议文档拆分后的片段之一，片段编号：{idx}/{total}。
你只需基于【本片段】内容抽取问答，不要假设看到了其他片段的内容。

【任务要求】
1. 通读下面的会议片段内容，从中识别出对销售/客户成功有复用价值的问题及回答。
2. “问题（question）”可以来自：
   - 客户在会议中的提问（优先抽取客户提问）
   - 销售或其他人抛出的关键业务/技术问题
   - 文档中以疑问形式出现的句子
   - 片段中隐含的、但可以明确提炼出来的关键业务问题
3. “回答（answer）”需要：
   - 严格基于当前片段内容，给出尽可能完整、明确、可复用的回答
   - 严禁胡编乱造，不要引入片段中没有的信息
4. 忽略寒暄、问候、闲聊等不涉及业务/产品/合作推进的内容，不要为这些内容生成问答对。
5. 尽量覆盖不同主题的问答，避免为同一个问题抽取多个高度重复的问答。
6. 每个问答对只包含一个清晰的问题，不要把多个互不相关的问题合并成一条问答。
7. 问答对总数不超过 {max_pairs} 个；如果片段信息有限，可以少于该数量。

【输出格式（必须是合法 JSON）】
只输出一个 JSON 对象，结构如下：
{{
  "qa_pairs": [
    {{"question": "问题1", "answer": "回答1"}},
    {{"question": "问题2", "answer": "回答2"}}
  ]
}}

【重要约束】
- 只能输出 JSON，不能包含任何额外说明、注释或前后缀。
- 字符串必须使用双引号，不能使用单引号。
- 不能有尾随逗号，必须能被标准 JSON 解析器直接解析。
- 如果无法抽取到有效问答，对应的列表使用空数组 []。

【待分析片段内容】：
{chunk}
"""
    attempt = 0
    while attempt < max_retries:
        try:
            result = call_ark_llm(
                prompt,
                response_format={"type": "json_object"},
            )
            data = json.loads(result)
            raw_pairs = data.get("qa_pairs", []) or []
            break
        except Exception as e:
            attempt += 1
            logger.warning(
                f"Failed to extract document QA pairs for chunk {idx}/{total}, "
                f"attempt {attempt}/{max_retries}: {e}"
            )
            if attempt >= max_retries:
                # 达到最大重试次数，标记为失败
                return [], False

    qa_pairs: List[Dict[str, str]] = []
    for item in raw_pairs:
        try:
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
        except Exception:
            continue
        if not question or not answer:
            continue
        qa_pairs.append({"question": question, "answer": answer})

    logger.info(
        f"分片问答抽取完成: chunk {idx}/{total}, 抽取到 {len(qa_pairs[:max_pairs])} 条问答"
    )
    return qa_pairs[:max_pairs], True


def extract_document_qa_pairs(
    content: str,
    max_pairs: int = 30,
    visit_context: Optional[dict[str, str]] = None,
) -> tuple[List[Dict[str, str]], dict]:
    """
    从文档/会议内容中抽取问答对列表，支持对大文档进行切片处理。
    
    说明：
    - 仅作为增值能力，不影响主流程；调用方需要自行处理异常情况。
    - 返回结构为 [{"question": "...", "answer": "..."}, ...]，只保留非空问答对。
    """
    if not content:
        return []

    chunks = _split_content_into_chunks(content, max_chars=8000)
    total_chunks = len(chunks)
    all_pairs: List[Dict[str, str]] = []
    success_chunks = 0
    failed_chunks = 0

    # 为每个片段分配一个合理的上限，防止单个片段占满所有配额
    per_chunk_limit = max(1, max_pairs // total_chunks) if total_chunks > 1 else max_pairs

    for idx, chunk in enumerate(chunks, start=1):
        # 如果已经达到整体上限就提前停止
        if len(all_pairs) >= max_pairs:
            break

        remaining = max_pairs - len(all_pairs)
        chunk_limit = min(per_chunk_limit, remaining)
        if chunk_limit <= 0:
            break

        pairs, ok = _extract_qa_pairs_for_chunk(
            chunk,
            max_pairs=chunk_limit,
            idx=idx,
            total=total_chunks,
            visit_context=visit_context,
        )
        if ok:
            success_chunks += 1
            all_pairs.extend(pairs)
        else:
            failed_chunks += 1

    # 再次截断，保证不超过 max_pairs
    all_pairs = all_pairs[:max_pairs]

    stats = {
        "total_chunks": total_chunks,
        "success_chunks": success_chunks,
        "failed_chunks": failed_chunks,
    }
    logger.info(
        "文档问答抽取分片统计: total_chunks=%s, success_chunks=%s, failed_chunks=%s, qa_pairs=%s",
        total_chunks,
        success_chunks,
        failed_chunks,
        len(all_pairs),
    )
    return all_pairs, stats


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=settings.CELERY_HEAVY_TASK_SOFT_TIME_LIMIT,
    time_limit=settings.CELERY_HEAVY_TASK_TIME_LIMIT,
)
def extract_and_save_document_qa(self, document_content_id: int) -> Dict[str, Any]:
    """
    异步任务：从文档内容中抽取问答对并保存到 document_contents 表。
    
    说明：
    - 通过 document_content_id 加载文档内容，避免在消息队列中传输大文本。
    - 如因事务未提交导致短暂查不到记录，会使用 Celery 的重试机制。
    - 任何异常只影响本任务，不影响主业务流程。
    """
    try:
        with Session(engine) as session:
            repo = DocumentContentRepo()
            document: DocumentContent | None = session.get(DocumentContent, document_content_id)
            
            if not document:
                # 可能是主事务尚未提交，稍后重试
                logger.warning(
                    f"未找到文档内容记录，document_content_id={document_content_id}，准备重试"
                )
                raise self.retry(countdown=5)
            
            content = document.raw_content or ""
            if not content.strip():
                # 空内容直接写入失败状态
                repo.update_qa_pairs(
                    session=session,
                    document_content_id=document_content_id,
                    qa_pairs=[],
                    qa_status="failed",
                    auto_commit=True,
                )
                logger.info(
                    f"文档内容为空，跳过问答对抽取，document_content_id={document_content_id}"
                )
                return {
                    "success": True,
                    "document_content_id": document_content_id,
                    "qa_count": 0,
                    "message": "empty_content",
                }
            
            # 尝试加载拜访记录背景信息，提升问答对抽取质量（非必需，失败不影响主流程）
            visit_context: dict[str, str] = {}
            visit_record_id = document.visit_record_id
            if visit_record_id:
                try:
                    # visit_record_id 是我们在 CRM 表里生成的 record_id，不是自增主键 ID，
                    # 这里只做 best-effort 的反查；如果关联复杂，可以后续扩展专门的查询函数。                    
                    visit_record = visit_record_repo.get_visit_record_by_id(session, visit_record_id)
                    if visit_record:
                        # 处理联系人信息：优先使用contacts字段，否则使用旧字段
                        # 将每个联系人的姓名和职位放在一起，更有利于LLM理解
                        contact_name = ""
                        if visit_record.contacts and len(visit_record.contacts) > 0:
                            # 多个联系人：将每个联系人的姓名和职位合并，格式为 "姓名1（职位1）, 姓名2（职位2）"
                            contact_info_parts = []
                            for contact in visit_record.contacts:
                                name = contact.name or ""
                                position = contact.position or ""
                                if name:
                                    if position:
                                        contact_info_parts.append(f"{name}（{position}）")
                                    else:
                                        contact_info_parts.append(name)
                            contact_name = ", ".join(contact_info_parts) if contact_info_parts else ""
                        else:
                            # 兼容旧数据：使用单个联系人字段，将姓名和职位合并
                            name = visit_record.contact_name or ""
                            position = visit_record.contact_position or ""
                            if name:
                                if position:
                                    contact_name = f"{name}（{position}）"
                                else:
                                    contact_name = name
                        
                        visit_context = {
                            "recorder": visit_record.recorder or "",
                            "account_name": visit_record.account_name or visit_record.partner_name or "",
                            "opportunity_name": visit_record.opportunity_name or "",
                            "contact_name": contact_name,
                            "is_first_visit": "是" if visit_record.is_first_visit else "否",
                            "is_call_high": "是" if visit_record.is_call_high else "否",
                        }
                except Exception as e:
                    logger.warning(f"加载拜访记录背景信息失败: visit_record_id={visit_record_id}, error={e}")

            qa_pairs, stats = extract_document_qa_pairs(content, visit_context=visit_context)
            # 根据分片执行情况设置状态：
            # - success: 有问答对，且所有分片都成功
            # - partial: 有问答对，但存在分片抽取失败
            # - failed: 没有任何问答对（包括全部分片失败或模型认为无问答）
            if qa_pairs:
                if stats.get("failed_chunks", 0) > 0:
                    status = "partial"
                else:
                    status = "success"
            else:
                status = "failed"
            
            repo.update_qa_pairs(
                session=session,
                document_content_id=document_content_id,
                qa_pairs=qa_pairs,
                qa_status=status,
                auto_commit=True,
            )
            
            logger.info(
                "异步问答对抽取完成: document_content_id=%s, qa_count=%s, status=%s, stats=%s",
                document_content_id,
                len(qa_pairs),
                status,
                stats,
            )
            return {
                "success": True,
                "document_content_id": document_content_id,
                "qa_count": len(qa_pairs),
                "status": status,
            }
    except self.MaxRetriesExceededError as e:  # type: ignore[attr-defined]
        logger.error(
            f"文档问答对抽取任务重试次数耗尽，document_content_id={document_content_id}，错误: {e}"
        )
        return {
            "success": False,
            "document_content_id": document_content_id,
            "message": "max_retries_exceeded",
        }
    except Exception as e:
        logger.exception(
            f"执行文档问答对抽取任务时出错，document_content_id={document_content_id}，错误: {e}"
        )
        # 统一返回失败，不再向上抛出，避免任务无限重试
        return {
            "success": False,
            "document_content_id": document_content_id,
            "message": str(e),
        }

