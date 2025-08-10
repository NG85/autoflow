"""
CRM日报统计服务
用于从现有的统计表中读取和处理销售人员的日报数据
"""

from typing import Dict, List, Optional
from datetime import date, datetime, timedelta
from sqlmodel import Session, select, and_
from app.models.crm_daily_account_statistics import CRMDailyAccountStatistics
from app.models.crm_account_assessment import CRMAccountAssessment
import logging

logger = logging.getLogger(__name__)


class CRMDailyStatisticsService:
    """CRM日报统计服务类 - 直接从现有统计表查询数据"""
    
    def __init__(self):
        pass
    
    def get_daily_statistics(self, session: Session, target_date: date) -> List[Dict]:
        """
        获取指定日期的销售日报统计数据
        
        Args:
            session: 数据库会话
            target_date: 目标日期
            
        Returns:
            List[Dict]: 统计结果列表
        """
        logger.info(f"开始获取 {target_date} 的销售日报统计数据")
        
        # 直接从crm_daily_account_statistics表查询数据
        query = select(CRMDailyAccountStatistics).where(
            CRMDailyAccountStatistics.report_date == target_date
        )
        
        statistics_records = session.exec(query).all()
        
        if not statistics_records:
            logger.info(f"{target_date} 没有找到任何统计记录")
            return []
        
        logger.info(f"找到 {len(statistics_records)} 条 {target_date} 的统计记录")
        
        # 转换为字典格式
        statistics_results = []
        for record in statistics_records:
            statistics_data = {
                'unique_id': record.unique_id,
                'report_date': record.report_date,
                'sales_id': record.sales_id,
                'sales_name': record.sales_name,
                'department_id': record.department_id,
                'department_name': record.department_name,
                'assessment_red_count': record.assessment_red_count or 0,
                'assessment_yellow_count': record.assessment_yellow_count or 0,
                'assessment_green_count': record.assessment_green_count or 0,
                'end_customer_total_follow_up': record.end_customer_total_follow_up or 0,
                'end_customer_total_first_visit': record.end_customer_total_first_visit or 0,
                'end_customer_total_multi_visit': record.end_customer_total_multi_visit or 0,
                'parter_total_follow_up': record.parter_total_follow_up or 0,
                'parter_total_first_visit': record.parter_total_first_visit or 0,
                'parter_total_multi_visit': record.parter_total_multi_visit or 0,
            }
            statistics_results.append(statistics_data)
        
        return statistics_results
    
    def get_complete_daily_report(self, session: Session, target_date: date) -> List[Dict]:
        """
        获取完整的日报数据，包括统计数据和通过correlation_id关联的评估详情
        
        Args:
            session: 数据库会话
            target_date: 目标日期
            
        Returns:
            List[Dict]: 完整的日报数据列表
        """
        logger.info(f"开始获取 {target_date} 的完整日报数据")
        
        # 1. 获取统计数据
        statistics_records = self.get_daily_statistics(session, target_date)
        
        if not statistics_records:
            logger.info(f"{target_date} 没有找到统计数据")
            return []
        
        # 2. 为每个统计记录获取关联的评估详情
        complete_reports = []
        
        for stats in statistics_records:
            # 通过correlation_id获取评估详情（假设correlation_id就是unique_id）
            correlation_id = stats['unique_id']
            
            # 获取评估详情
            assessment_details = self.get_assessment_by_correlation_id(session, correlation_id)
            
            # 填充评估详情中的销售人员和部门信息
            for assessment in assessment_details['first']:
                assessment['sales_name'] = stats['sales_name']
                assessment['department_name'] = stats['department_name']
            
            for assessment in assessment_details['multi']:
                assessment['sales_name'] = stats['sales_name']
                assessment['department_name'] = stats['department_name']
            
            # 对评估数据进行排序（红灯>黄灯-团队名称-销售名称）
            sorted_first_assessments = self._sort_assessments(assessment_details['first'])
            sorted_multi_assessments = self._sort_assessments(assessment_details['multi'])
            
            # 移除用于排序的临时字段
            for assessment in sorted_first_assessments:
                assessment.pop('assessment_flag_raw', None)
            for assessment in sorted_multi_assessments:
                assessment.pop('assessment_flag_raw', None)
            
            # 组合完整数据
            from app.core.config import settings
            
            complete_report = {
                **stats,  # 包含所有统计数据
                'first_assessment': sorted_first_assessments,
                'multi_assessment': sorted_multi_assessments,
                'visit_detail_page': f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={target_date}&end_date={target_date}",
                'account_list_page': settings.ACCOUNT_LIST_PAGE_URL
            }
            
            complete_reports.append(complete_report)
            
            logger.info(f"销售 {stats['sales_name']} 的完整日报数据已组装，包含 {len(assessment_details['first'])} 个首次评估，{len(assessment_details['multi'])} 个多次评估")
        
        return complete_reports
    
    def get_assessment_by_correlation_id(self, session: Session, correlation_id: str) -> Dict[str, List]:
        """
        通过correlation_id获取评估详情数据
        
        Args:
            session: 数据库会话
            correlation_id: 关联ID
            
        Returns:
            Dict: 包含first和multi两个键的评估详情列表
        """
        logger.debug(f"通过correlation_id获取评估数据: {correlation_id}")
        
        # 从crm_account_assessment表查询数据，过滤掉绿灯评估
        query = select(CRMAccountAssessment).where(
            and_(
                CRMAccountAssessment.correlation_id == correlation_id,
                CRMAccountAssessment.assessment_flag != 'green'  # 过滤掉绿灯
            )
        )
        
        assessment_records = session.exec(query).all()
        
        if not assessment_records:
            logger.debug(f"correlation_id {correlation_id} 没有找到非绿灯评估记录")
            return {"first": [], "multi": []}
        
        logger.debug(f"correlation_id {correlation_id} 找到 {len(assessment_records)} 条非绿灯评估记录")
        
        # 按首次/多次拜访分组
        first_assessments = []
        multi_assessments = []
        
        for assessment in assessment_records:
            assessment_data = {
                'account_name': assessment.account_name or "",
                'opportunity_names': self._format_opportunity_names(assessment.opportunity_names),
                'follow_up_note': assessment.follow_up_note or "",
                'follow_up_next_step': assessment.follow_up_next_step or "",
                'assessment_flag': self._convert_assessment_flag(assessment.assessment_flag),
                'assessment_description': assessment.assessment_description or "",
                'account_level': assessment.account_level or "",
                'sales_name': "",  # 这个字段将在上层填充
                'department_name': "",  # 这个字段将在上层填充
                'assessment_flag_raw': assessment.assessment_flag or ""  # 保留原始标志用于排序
            }
            
            if assessment.is_first_visit:
                first_assessments.append(assessment_data)
            else:
                multi_assessments.append(assessment_data)
        
        # 按照指定规则排序：红灯>黄灯-团队名称-销售名称
        # 注意：这里的排序会在上层填充sales_name和department_name后进行
        
        return {
            "first": first_assessments,
            "multi": multi_assessments
        }
    
    def _format_opportunity_names(self, opportunity_names_json: str) -> str:
        """
        格式化商机名称，从JSON数组转换为用 | 分隔的字符串
        """
        if not opportunity_names_json:
            return ""
        
        try:
            import json
            opportunity_list = json.loads(opportunity_names_json)
            if isinstance(opportunity_list, list):
                if not opportunity_list:  # 空数组
                    return ""
                # 过滤空字符串并用 | 连接
                filtered_list = [name.strip() for name in opportunity_list if name and name.strip()]
                if not filtered_list:  # 所有元素都是空字符串
                    return ""
                return " | ".join(filtered_list)
            else:
                result = str(opportunity_list).strip()
                return result
        except (json.JSONDecodeError, TypeError):
            # 如果解析失败，直接返回原字符串
            return opportunity_names_json.strip() if opportunity_names_json else ""
    
    def _convert_assessment_flag(self, flag: str) -> str:
        """
        将评估标志转换为emoji
        """
        flag_mapping = {
            "red": "🔴",
            "yellow": "🟡", 
            "green": "🟢"
        }
        return flag_mapping.get(flag.lower() if flag else "", "")
    
    def _sort_assessments(self, assessments: List[Dict]) -> List[Dict]:
        """
        按照指定规则排序评估数据：红灯>黄灯-团队名称-销售名称
        
        Args:
            assessments: 评估数据列表
            
        Returns:
            排序后的评估数据列表
        """
        def sort_key(assessment):
            # 评估灯光优先级：red=1, yellow=2, 其他=3
            flag_priority = {
                'red': 1,
                'yellow': 2
            }
            assessment_flag_raw = assessment.get('assessment_flag_raw', '').lower()
            flag_order = flag_priority.get(assessment_flag_raw, 3)
            
            # 部门名称
            department_name = assessment.get('department_name', '')
            
            # 销售名称
            sales_name = assessment.get('sales_name', '')
            
            return (flag_order, department_name, sales_name)
        
        return sorted(assessments, key=sort_key)
    

    def generate_daily_statistics(self, session: Session, target_date: Optional[date] = None) -> int:
        """
        生成完整日报数据的主方法
        通过correlation_id关联两张表，获取完整的日报信息
        
        Args:
            session: 数据库会话
            target_date: 目标日期，默认为昨天
            
        Returns:
            int: 处理的销售人员数量
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"开始生成 {target_date} 的完整日报数据")
        
        try:
            # 获取完整的日报数据（包括通过correlation_id关联的评估详情）
            complete_reports = self.get_complete_daily_report(session, target_date)
            
            sales_count = len(complete_reports)
            
            if sales_count > 0:
                logger.info(f"成功生成 {target_date} 的完整日报数据，包含 {sales_count} 个销售人员的数据")
                
                # 统计总的评估数量
                total_first_assessments = sum(len(report['first_assessment']) for report in complete_reports)
                total_multi_assessments = sum(len(report['multi_assessment']) for report in complete_reports)
                
                logger.info(f"总计: {total_first_assessments} 个首次拜访评估，{total_multi_assessments} 个多次拜访评估")
                
                # 推送飞书卡片通知（如果启用）
                from app.core.config import settings
                if settings.CRM_DAILY_STATISTICS_FEISHU_ENABLED:
                    # 推送个人日报
                    self._send_feishu_notifications(session, complete_reports)
                    
                    # 生成并推送部门日报
                    self._generate_and_send_department_reports(session, target_date)
                    
                    # 生成并推送公司日报
                    self._generate_and_send_company_report(session, target_date)
                else:
                    logger.info("CRM日报飞书推送功能已禁用，跳过推送")
            else:
                logger.warning(f"{target_date} 没有找到任何销售人员的日报数据")
            
            return sales_count
            
        except Exception as e:
            logger.error(f"生成完整日报数据失败: {e}")
            raise
    
    def _send_feishu_notifications(self, session: Session, complete_reports: List[Dict]) -> None:
        """
        向销售人员发送CRM日报飞书卡片通知
        
        Args:
            session: 数据库会话
            complete_reports: 完整的日报数据列表
        """
        from app.services.feishu_notification_service import FeishuNotificationService
        
        notification_service = FeishuNotificationService()
        
        total_notifications = 0
        successful_notifications = 0
        
        for report in complete_reports:
            try:
                # 转换日期格式为字符串，因为JSON序列化不支持date对象
                # 同时将sales_name字段重命名为recorder，以适配飞书卡片模板
                report_data = {
                    **report,
                    'recorder': report.get('sales_name', ''),  # 将sales_name重命名为recorder
                    'report_date': report['report_date'].isoformat() if hasattr(report.get('report_date'), 'isoformat') else str(report.get('report_date'))
                }
                
                # 发送飞书通知
                result = notification_service.send_daily_report_notification(
                    db_session=session,
                    daily_report_data=report_data,
                    external=False  # 默认内部应用
                )
                
                total_notifications += 1
                
                if result["success"]:
                    successful_notifications += 1
                    logger.info(
                        f"成功为销售 {report['sales_name']} 发送个人日报飞书通知，"
                        f"推送给本人 {result['success_count']}/{result['recipients_count']} 次"
                    )
                else:
                    logger.warning(
                        f"销售 {report['sales_name']} 的日报飞书通知发送失败: {result['message']}"
                    )
                    
            except Exception as e:
                logger.error(f"为销售 {report.get('sales_name', 'Unknown')} 发送飞书通知时出错: {str(e)}")
                total_notifications += 1
        
        logger.info(
            f"CRM个人日报飞书通知发送完成: {successful_notifications}/{total_notifications} 个销售人员的个人通知发送成功"
        )
    
    def _generate_and_send_department_reports(self, session: Session, target_date: date) -> None:
        """
        生成并推送部门日报
        
        Args:
            session: 数据库会话
            target_date: 目标日期
        """
        from app.services.feishu_notification_service import FeishuNotificationService
        
        logger.info(f"开始生成并推送 {target_date} 的部门日报")
        
        # 生成部门汇总报告
        department_reports = self.aggregate_department_reports(session, target_date)
        
        if not department_reports:
            logger.warning(f"{target_date} 没有找到任何部门数据，跳过部门日报推送")
            return
        
        notification_service = FeishuNotificationService()
        
        total_departments = 0
        successful_departments = 0
        
        for department_report in department_reports:
            try:
                # 发送部门日报飞书通知
                result = notification_service.send_department_report_notification(
                    db_session=session,
                    department_report_data=department_report,
                    external=False  # 默认内部应用
                )
                
                total_departments += 1
                
                if result["success"]:
                    successful_departments += 1
                    logger.info(
                        f"成功为部门 {department_report['department_name']} 发送日报飞书通知，"
                        f"推送给部门负责人 {result['success_count']}/{result['recipients_count']} 次"
                    )
                else:
                    logger.warning(
                        f"部门 {department_report['department_name']} 的日报飞书通知发送失败: {result['message']}"
                    )
                    
            except Exception as e:
                logger.error(f"为部门 {department_report.get('department_name', 'Unknown')} 发送飞书通知时出错: {str(e)}")
                total_departments += 1
        
        logger.info(
            f"CRM部门日报飞书通知发送完成: {successful_departments}/{total_departments} 个部门的通知发送成功"
        )
    
    def _generate_and_send_company_report(self, session: Session, target_date: date) -> None:
        """
        生成并推送公司日报
        
        Args:
            session: 数据库会话
            target_date: 目标日期
        """
        from app.services.feishu_notification_service import FeishuNotificationService
        
        logger.info(f"开始生成并推送 {target_date} 的公司日报")
        
        # 生成公司汇总报告
        company_report = self.aggregate_company_report(session, target_date)
        
        if not company_report:
            logger.warning(f"{target_date} 没有找到任何数据，跳过公司日报推送")
            return
        
        notification_service = FeishuNotificationService()
        
        try:
            # 发送公司日报飞书通知
            result = notification_service.send_company_report_notification(
                db_session=session,
                company_report_data=company_report,
                external=False  # 默认内部应用
            )
            
            if result["success"]:
                logger.info(
                    f"成功发送公司日报飞书通知，"
                    f"推送成功 {result['success_count']}/{result['recipients_count']} 次"
                )
            else:
                logger.warning(f"公司日报飞书通知发送失败: {result['message']}")
                
        except Exception as e:
            logger.error(f"发送公司日报飞书通知时出错: {str(e)}")
        
        logger.info("CRM公司日报飞书通知发送完成")
    
    def aggregate_department_reports(self, session: Session, target_date: Optional[date] = None) -> List[Dict]:
        """
        按部门汇总销售日报数据
        
        Args:
            session: 数据库会话
            target_date: 目标日期，默认为昨天
            
        Returns:
            List[Dict]: 部门日报数据列表
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"开始汇总 {target_date} 的部门日报数据")
        
        # 获取所有销售的完整日报数据
        complete_reports = self.get_complete_daily_report(session, target_date)
        
        if not complete_reports:
            logger.warning(f"{target_date} 没有找到任何销售日报数据")
            return []
        
        # 按部门分组
        department_groups = {}
        
        for report in complete_reports:
            department_name = report.get('department_name', '未知部门')
            
            if department_name not in department_groups:
                department_groups[department_name] = []
            
            department_groups[department_name].append(report)
        
        # 生成部门汇总报告
        department_reports = []
        
        for department_name, sales_reports in department_groups.items():
            department_report = self._aggregate_single_department(
                department_name=department_name,
                sales_reports=sales_reports,
                target_date=target_date
            )
            department_reports.append(department_report)
        
        logger.info(f"完成 {target_date} 的部门日报汇总，共 {len(department_reports)} 个部门")
        
        return department_reports
    
    def _aggregate_single_department(self, department_name: str, sales_reports: List[Dict], target_date: date) -> Dict:
        """
        汇总单个部门的日报数据
        
        Args:
            department_name: 部门名称
            sales_reports: 该部门所有销售的日报数据
            target_date: 目标日期
            
        Returns:
            Dict: 部门汇总日报数据
        """
        from app.core.config import settings
        
        # 汇总统计数据（直接加和）
        total_stats = {
            'end_customer_total_follow_up': 0,
            'end_customer_total_first_visit': 0,
            'end_customer_total_multi_visit': 0,
            'parter_total_follow_up': 0,
            'parter_total_first_visit': 0,
            'parter_total_multi_visit': 0,
            'assessment_red_count': 0,
            'assessment_yellow_count': 0,
            'assessment_green_count': 0
        }
        
        # 汇总评估数据（直接合并）
        all_first_assessments = []
        all_multi_assessments = []
        
        for report in sales_reports:
            # 累加统计数据
            for key in total_stats.keys():
                total_stats[key] += report.get(key, 0)
            
            # 合并评估数据（已经过滤掉绿灯且已排序）
            all_first_assessments.extend(report.get('first_assessment', []))
            all_multi_assessments.extend(report.get('multi_assessment', []))
        
        # 对部门汇总后的评估数据重新排序
        sorted_dept_first_assessments = self._sort_assessments(all_first_assessments)
        sorted_dept_multi_assessments = self._sort_assessments(all_multi_assessments)
        
        # 构造部门日报数据（与个人日报字段保持一致，除了去掉recorder）
        department_report = {
            'department_name': department_name,
            'report_date': target_date,
            'statistics': [total_stats],  # 作为数组，与个人日报保持一致
            'visit_detail_page': f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={target_date}&end_date={target_date}",
            'account_list_page': settings.ACCOUNT_LIST_PAGE_URL,
            'first_assessment': sorted_dept_first_assessments,
            'multi_assessment': sorted_dept_multi_assessments
        }
        
        logger.info(
            f"部门 {department_name} 日报汇总完成: {len(sales_reports)} 个销售, "
            f"{len(all_first_assessments)} 个首次评估, {len(all_multi_assessments)} 个多次评估"
        )
        
        return department_report
    
    def aggregate_company_report(self, session: Session, target_date: Optional[date] = None) -> Dict:
        """
        汇总公司级日报数据
        
        Args:
            session: 数据库会话
            target_date: 目标日期，默认为昨天
            
        Returns:
            Dict: 公司汇总日报数据
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"开始汇总 {target_date} 的公司日报数据")
        
        # 获取所有销售的完整日报数据
        complete_reports = self.get_complete_daily_report(session, target_date)
        
        if not complete_reports:
            logger.warning(f"{target_date} 没有找到任何销售日报数据")
            return None
        
        return self._aggregate_company_data(complete_reports, target_date)
    
    def _aggregate_company_data(self, sales_reports: List[Dict], target_date: date) -> Dict:
        """
        汇总公司级数据
        
        Args:
            sales_reports: 所有销售的日报数据
            target_date: 目标日期
            
        Returns:
            Dict: 公司汇总日报数据
        """
        from app.core.config import settings
        
        # 汇总统计数据（直接加和）
        total_stats = {
            'end_customer_total_follow_up': 0,
            'end_customer_total_first_visit': 0,
            'end_customer_total_multi_visit': 0,
            'parter_total_follow_up': 0,
            'parter_total_first_visit': 0,
            'parter_total_multi_visit': 0,
            'assessment_red_count': 0,
            'assessment_yellow_count': 0,
            'assessment_green_count': 0
        }
        
        # 汇总评估数据（直接合并，但移除跟进记录字段）
        all_first_assessments = []
        all_multi_assessments = []
        
        for report in sales_reports:
            # 累加统计数据
            for key in total_stats.keys():
                total_stats[key] += report.get(key, 0)
            
            # 合并评估数据，但移除跟进字段
            for assessment in report.get('first_assessment', []):
                company_assessment = self._convert_to_company_assessment(assessment)
                all_first_assessments.append(company_assessment)
            
            for assessment in report.get('multi_assessment', []):
                company_assessment = self._convert_to_company_assessment(assessment)
                all_multi_assessments.append(company_assessment)
        
        # 对公司汇总后的评估数据重新排序
        sorted_company_first_assessments = self._sort_assessments(all_first_assessments)
        sorted_company_multi_assessments = self._sort_assessments(all_multi_assessments)
        
        # 移除用于排序的临时字段
        for assessment in sorted_company_first_assessments:
            assessment.pop('assessment_flag_raw', None)
        for assessment in sorted_company_multi_assessments:
            assessment.pop('assessment_flag_raw', None)
        
        # 构造公司日报数据
        company_report = {
            'report_date': target_date,
            'statistics': [total_stats],  # 作为数组，与其他日报保持一致
            'visit_detail_page': f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={target_date}&end_date={target_date}",
            'account_list_page': settings.ACCOUNT_LIST_PAGE_URL,
            'first_assessment': sorted_company_first_assessments,
            'multi_assessment': sorted_company_multi_assessments
        }
        
        logger.info(
            f"公司日报汇总完成: {len(sales_reports)} 个销售, "
            f"{len(all_first_assessments)} 个首次评估, {len(all_multi_assessments)} 个多次评估"
        )
        
        return company_report
    
    def _convert_to_company_assessment(self, assessment: Dict) -> Dict:
        """
        将完整评估详情转换为公司级评估详情（移除跟进记录字段）
        
        Args:
            assessment: 完整的评估详情
            
        Returns:
            Dict: 公司级评估详情（不包含跟进字段）
        """
        return {
            'account_name': assessment.get('account_name', ''),
            'opportunity_names': assessment.get('opportunity_names', ''),
            'assessment_flag': assessment.get('assessment_flag', ''),
            'assessment_description': assessment.get('assessment_description', ''),
            'account_level': assessment.get('account_level', ''),
            'sales_name': assessment.get('sales_name', ''),
            'department_name': assessment.get('department_name', ''),
            'assessment_flag_raw': assessment.get('assessment_flag_raw', '')  # 保留用于排序
            # 移除: follow_up_note, follow_up_next_step
        }


# 创建服务实例
crm_daily_statistics_service = CRMDailyStatisticsService()
