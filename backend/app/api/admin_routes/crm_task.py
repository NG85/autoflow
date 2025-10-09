import logging
from typing import Optional
from app.api.deps import CurrentSuperuserDep
from app.core.config import settings, WritebackMode
from fastapi import APIRouter, Body

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/crm/daily-reports/trigger-task")
def trigger_daily_statistics_task(
    user: CurrentSuperuserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天"),
    enable_feishu_push: Optional[bool] = Body(None, description="是否启用飞书推送，不传则使用系统配置")
):
    """
    手动触发CRM日报统计定时任务
    
    直接调用Celery任务，与定时任务执行相同的逻辑：
    1. 从crm_daily_account_statistics表查询销售统计数据
    2. 通过correlation_id关联crm_account_assessment表获取评估详情
    3. 组合成完整的日报数据
    4. 向每个销售人员本人推送个人日报飞书卡片
    
    注意：
    - 这是异步任务，会立即返回任务ID，可通过任务ID查询执行状态
    - 个人日报只推送给销售人员本人，不会推送给上级或群聊
    """
    if not user.is_superuser:
        return {
            "code": 403,
            "message": "权限不足，只有超级管理员可以触发此任务",
            "data": {}
        }
    try:
        from app.tasks.cron_jobs import generate_crm_daily_statistics
        from datetime import datetime, timedelta
        
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                return {
                    "code": 400,
                    "message": "日期格式错误，请使用YYYY-MM-DD格式",
                    "data": {}
                }
        else:
            # 默认为昨天
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 手动触发CRM日报统计任务，目标日期: {parsed_date}")
        
        # 如果用户指定了飞书推送设置，需要临时修改配置
        if enable_feishu_push is not None:
            logger.info(f"用户指定飞书推送设置: {enable_feishu_push}")
        
        # 触发异步任务，传递日期参数
        task = generate_crm_daily_statistics.delay(target_date_str=parsed_date.isoformat())
        
        return {
            "code": 0,
            "message": "CRM日报统计任务已触发",
            "data": {
                "task_id": task.id,
                "target_date": parsed_date.isoformat(),
                "status": "PENDING",
                "description": f"已提交 {parsed_date} 的CRM日报统计任务到队列，任务ID: {task.id}"
            }
        }
        
    except Exception as e:
        logger.exception(f"触发CRM日报统计任务失败: {e}")
        return {
            "code": 500,
            "message": f"触发任务失败: {str(e)}",
            "data": {}
        }


@router.post("/crm/weekly-reports/trigger-task")
def trigger_weekly_report_task(
    user: CurrentSuperuserDep,
    start_date: Optional[str] = Body(None, description="开始日期，格式YYYY-MM-DD，不传则默认为上周日"),
    end_date: Optional[str] = Body(None, description="结束日期，格式YYYY-MM-DD，不传则默认为本周六"),
    enable_feishu_push: Optional[bool] = Body(None, description="是否启用飞书推送，不传则使用系统配置")
):
    """
    手动触发CRM周报推送任务
    
    用于测试或手动执行周报推送功能，默认处理上周日到本周六的数据
    """
    if not user.is_superuser:
        return {
            "code": 403,
            "message": "权限不足，只有超级管理员可以触发此任务",
            "data": {}
        }

    try:
        from app.tasks.cron_jobs import generate_crm_weekly_report
        from datetime import datetime, timedelta
        
        # 解析日期范围
        if start_date and end_date:
            try:
                parsed_start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                parsed_end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            except ValueError:
                return {
                    "code": 400,
                    "message": "日期格式错误，请使用YYYY-MM-DD格式",
                    "data": {}
                }
        else:
            # 默认处理上周日到本周六的数据
            today = datetime.now().date()
            # 计算上周日（今天往前推7天，然后找到最近的周日）
            days_since_sunday = (today.weekday() + 1) % 7  # 0=周一，1=周二，...，6=周日
            last_sunday = today - timedelta(days=days_since_sunday + 7)
            this_saturday = last_sunday + timedelta(days=6)
            
            parsed_start_date = last_sunday
            parsed_end_date = this_saturday
        
        logger.info(f"用户 {user.id} 手动触发CRM周报推送任务，日期范围: {parsed_start_date} 到 {parsed_end_date}")
        
        # 如果用户指定了飞书推送设置，需要临时修改配置
        if enable_feishu_push is not None:
            logger.info(f"用户指定飞书推送设置: {enable_feishu_push}")
        
        # 触发异步任务，传递日期参数
        task = generate_crm_weekly_report.delay(
            start_date_str=parsed_start_date.isoformat(),
            end_date_str=parsed_end_date.isoformat()
        )
        
        return {
            "code": 0,
            "message": "CRM周报推送任务已触发",
            "data": {
                "task_id": task.id,
                "start_date": parsed_start_date.isoformat(),
                "end_date": parsed_end_date.isoformat(),
                "status": "PENDING",
                "description": f"已提交 {parsed_start_date} 到 {parsed_end_date} 的CRM周报推送任务到队列，任务ID: {task.id}"
            }
        }
        
    except Exception as e:
        logger.exception(f"触发CRM周报推送任务失败: {e}")
        return {
            "code": 500,
            "message": f"触发任务失败: {str(e)}",
            "data": {}
        }


@router.post("/crm/writeback/trigger-task")
def trigger_crm_writeback_task(
    user: CurrentSuperuserDep,
    start_date: Optional[str] = Body(None, description="开始日期，格式YYYY-MM-DD，不传则默认为上周日"),
    end_date: Optional[str] = Body(None, description="结束日期，格式YYYY-MM-DD，不传则默认为本周六"),
    writeback_mode: Optional[str] = Body(None, description="回写模式，支持 'CBG'（内容回写）或 'APAC'（任务创建），不传则使用配置中的默认值")
):
    """
    手动触发CRM拜访记录回写任务
    
    用于测试或手动执行CRM数据回写功能，默认处理上周日到本周六的拜访记录数据
    
    工作流程：
    1. 计算指定时间范围内的拜访记录
    2. 根据回写模式选择处理方式：
       - CBG模式：按客户和商机分组处理拜访记录，生成格式化的回写内容
       - APAC模式：为每条拜访记录创建对应的任务
    3. 调用相应的API进行回写或任务创建
    """
    if not user.is_superuser:
        return {
            "code": 403,
            "message": "权限不足，只有超级管理员可以触发此任务",
            "data": {}
        }

    try:
        from app.tasks.cron_jobs import crm_visit_records_writeback
        from datetime import datetime, timedelta
        
        # 解析日期范围
        if start_date and end_date:
            try:
                parsed_start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                parsed_end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            except ValueError:
                return {
                    "code": 400,
                    "message": "日期格式错误，请使用YYYY-MM-DD格式",
                    "data": {}
                }
        else:
            # 默认处理上周日到本周六的数据
            today = datetime.now().date()
            # 计算上周日（今天往前推7天，然后找到最近的周日）
            days_since_sunday = (today.weekday() + 1) % 7  # 0=周一，1=周二，...，6=周日
            last_sunday = today - timedelta(days=days_since_sunday + 7)
            this_saturday = last_sunday + timedelta(days=6)
            
            parsed_start_date = last_sunday
            parsed_end_date = this_saturday
        
        logger.info(f"用户 {user.id} 手动触发CRM拜访记录回写任务，日期范围: {parsed_start_date} 到 {parsed_end_date}，回写模式: {writeback_mode}")
        
        # 如果没有指定回写模式，使用配置中的默认值
        if writeback_mode is None:
            writeback_mode = settings.CRM_WRITEBACK_DEFAULT_MODE.value
        
        # 验证回写模式参数
        valid_modes = [mode.value for mode in WritebackMode]
        if writeback_mode not in valid_modes:
            return {
                "code": 400,
                "message": f"无效的回写模式，支持的模式: {valid_modes}",
                "data": {}
            }
        
        # 触发异步任务，传递日期参数和回写模式
        task = crm_visit_records_writeback.delay(
            start_date_str=parsed_start_date.isoformat(),
            end_date_str=parsed_end_date.isoformat(),
            writeback_mode=writeback_mode
        )
        
        return {
            "code": 0,
            "message": "CRM拜访记录回写任务已触发",
            "data": {
                "task_id": task.id,
                "start_date": parsed_start_date.isoformat(),
                "end_date": parsed_end_date.isoformat(),
                "writeback_mode": writeback_mode,
                "status": "PENDING",
                "description": f"已提交 {parsed_start_date} 到 {parsed_end_date} 的CRM拜访记录回写任务到队列，回写模式: {writeback_mode}，任务ID: {task.id}"
            }
        }
        
    except Exception as e:
        logger.exception(f"触发CRM拜访记录回写任务失败: {e}")
        return {
            "code": 500,
            "message": f"触发任务失败: {str(e)}",
            "data": {}
        }


@router.get("/crm/daily-reports/task-status/{task_id}")
def get_task_status(
    task_id: str,
    user: CurrentSuperuserDep,
):
    """
    查询CRM日报统计任务的执行状态
    
    Args:
        task_id: 任务ID（由trigger-task接口返回）
        
    Returns:
        任务状态信息
    """
    if not user.is_superuser:
        return {
            "code": 403,
            "message": "权限不足，只有超级管理员可以触发此任务",
            "data": {}
        }
    try:
        from app.celery import app
        
        # 获取任务状态
        task_result = app.AsyncResult(task_id)
        
        status = task_result.status
        result = task_result.result
        
        response_data = {
            "task_id": task_id,
            "status": status,
        }
        
        if status == "PENDING":
            response_data["message"] = "任务正在等待执行"
        elif status == "SUCCESS":
            response_data["message"] = "任务执行成功"
            response_data["result"] = result
        elif status == "FAILURE":
            response_data["message"] = "任务执行失败"
            response_data["error"] = str(result) if result else "未知错误"
        elif status == "RETRY":
            response_data["message"] = "任务正在重试"
        else:
            response_data["message"] = f"任务状态: {status}"
        
        return {
            "code": 0,
            "message": "success",
            "data": response_data
        }
        
    except Exception as e:
        logger.exception(f"查询任务状态失败: {e}")
        return {
            "code": 500,
            "message": f"查询任务状态失败: {str(e)}",
            "data": {}
        }