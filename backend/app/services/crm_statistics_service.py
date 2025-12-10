"""
CRM统计服务
用于从现有的统计表中读取和处理销售人员的日报和周报数据
"""

from typing import Any, Dict, List, Optional
from datetime import date, datetime, timedelta
from sqlmodel import Session, select, and_
from app.models.crm_daily_account_statistics import CRMDailyAccountStatistics
from app.models.crm_account_assessment import CRMAccountAssessment
from app.models.crm_account_opportunity_assessment import CRMAccountOpportunityAssessment
from app.models.crm_department_daily_summary import CRMDepartmentDailySummary
from app.services.platform_notification_service import platform_notification_service
import logging
import requests

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.user_profile import UserProfile

logger = logging.getLogger(__name__)


class CRMStatisticsService:
    """CRM统计服务类 - 直接从现有统计表查询数据，支持日报和周报统计"""
    
    def __init__(self):
        pass
    
    def _get_departments_with_managers_from_oauth(self) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        """
        从 OAuth 服务获取所有部门及其负责人
        
        Returns:
            Dict[str, Optional[List[Dict[str, Any]]]]: 字典，键是部门名称，值是负责人信息列表（如果没有负责人则为None）
                负责人信息字典包含：name, openId, crmUserId, email, phone 等字段
        """
        from app.core.config import settings
        
        base_url = settings.OAUTH_BASE_URL.rstrip("/")
        url = f"{base_url}/organization/departments/leaders"
        
        # POST 请求，参数放在 body 中
        headers = {"Content-Type": "application/json"}
        body = {
            "include_leader_identity": True
        }
        
        try:
            response = requests.post(url, json=body, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if not isinstance(data, dict) or data.get("code") != 0:
                logger.error(f"OAuth service returned error: {data.get('message', 'Unknown error')}")
                return {}
            
            result = data.get("result", [])
            if not isinstance(result, list):
                logger.error(f"OAuth service returned invalid result format: {result}")
                return {}
            
            departments_with_managers = {}
            for dept_info in result:
                department_name = dept_info.get("departmentName")
                if not department_name:
                    continue
                
                leaders = dept_info.get("leaders", [])
                if leaders:
                    manager_list = []
                    for leader in leaders:
                        manager_list.append({
                            "open_id": leader["openId"],
                            "name": leader.get("name", ""),
                            "crmUserId": leader.get("crmUserId", ""),
                            # "email": leader.get("email", ""),
                            # "phone": leader.get("phone", ""),
                            "userId": leader.get("userId", ""),
                            "platform": leader.get("platform", "feishu"),
                            "type": "department_manager",
                            "department": department_name,
                            "receive_id_type": "open_id",
                        })
                    departments_with_managers[department_name] = manager_list
            
            logger.info(f"从 OAuth 服务获取到 {len(departments_with_managers)} 个部门信息")
            return departments_with_managers
            
        except requests.exceptions.RequestException as e:
            logger.error(f"调用 OAuth 服务获取部门负责人信息失败: {e}")
            return {}
        except Exception as e:
            logger.error(f"处理 OAuth 服务响应时出错: {e}")
            return {}

    def get_sales_daily_statistics(self, session: Session, target_date: date) -> List[Dict]:
        """
        获取指定日期的销售日报明细和统计数据
        通过查询指定日期的拜访记录，获取当日有哪些销售有拜访记录，
        并按客户/合作伙伴维度统计首次拜访和多次跟进数量，同时输出去重的商机列表和无商机客户列表。
        
        Args:
            session: 数据库会话
            target_date: 目标日期
            
        Returns:
            List[Dict]: 统计结果列表，每个元素包含：
                - recorder: 销售姓名
                - department: 所在团队
                - end_customer_total_follow_up: 跟进客户总数（按客户ID去重）
                - end_customer_total_first_visit: 首次拜访客户总数（去重，同一客户当天多次拜访只要有一次标记为首次即计入首次）
                - end_customer_total_multi_visit: 多次跟进客户总数（去重，且当天没有任何一次标记为首次）
                - partner_total_follow_up: 跟进合作伙伴总数（按合作伙伴ID去重）
                - partner_total_first_visit: 首次拜访合作伙伴总数（去重）
                - partner_total_multi_visit: 多次跟进合作伙伴总数（去重）
                - opportunities: 去重后的商机列表（当日该销售跟进过的商机），每个元素包含：
                    - opportunity_id: 商机ID
                    - opportunity_name: 商机名称
                - accounts_without_opportunity: 无任何商机关联的去重客户列表（当日该销售仅以客户维度跟进的客户），每个元素包含：
                    - account_id: 客户ID
                    - account_name: 客户名称
        """
        logger.info(f"开始获取 {target_date} 的销售日报统计数据")
        
        # 查询指定日期的拜访记录 - 仅选择后续统计所需的字段，减少无用列以提高查询效率
        query = select(
            CRMSalesVisitRecord.id,
            CRMSalesVisitRecord.record_id,
            CRMSalesVisitRecord.recorder,
            CRMSalesVisitRecord.recorder_id,
            CRMSalesVisitRecord.account_id,
            CRMSalesVisitRecord.account_name,
            CRMSalesVisitRecord.opportunity_id,
            CRMSalesVisitRecord.opportunity_name,
            CRMSalesVisitRecord.partner_id,
            CRMSalesVisitRecord.partner_name,
            CRMSalesVisitRecord.visit_communication_date,
            CRMSalesVisitRecord.is_first_visit,
            UserProfile.department,
        ).outerjoin(
                UserProfile,
                CRMSalesVisitRecord.recorder_id == UserProfile.user_id
            ).where(
            CRMSalesVisitRecord.visit_communication_date == target_date,
            CRMSalesVisitRecord.recorder_id.isnot(None)
        )
            
        visit_records = session.exec(query).all()
        
        if not visit_records:
            logger.info(f"{target_date} 没有找到任何拜访记录")
            return []
        
        logger.info(f"找到 {len(visit_records)} 条 {target_date} 的拜访记录")
        
        # 按销售分组，收集每个销售拜访的客户、商机和合作伙伴
        sales_dict: Dict[str, Dict[str, Any]] = {}
        
        for record in visit_records:
            recorder_id = str(record.recorder_id)
            recorder_name = record.recorder or ""
            department = record.department or ""
            
            # 初始化销售信息
            if recorder_id not in sales_dict:
                sales_dict[recorder_id] = {
                    'recorder_id': recorder_id,
                    'recorder': recorder_name,
                    'department': department,
                    'account_visit_status': {},      # {account_id: {'has_first': bool, 'has_non_first': bool}}
                    'partner_visit_status': {},      # {partner_id: {'has_first': bool, 'has_non_first': bool}}
                    'account_has_opportunity': {},   # {account_id: bool}
                    'account_names': {},             # {account_id: account_name}
                    'opportunity_map': {},           # {opportunity_id: {'opportunity_id', 'opportunity_name'}}
                }
            
            is_first_visit = bool(record.is_first_visit)
            sales_info = sales_dict[recorder_id]
            
            # 处理客户拜访记录
            if record.account_id:
                account_id = record.account_id
                account_name = record.account_name or ""
                
                # 记录客户名称（用于后续生成无商机客户列表）
                if account_id not in sales_info['account_names']:
                    sales_info['account_names'][account_id] = account_name
                
                # 记录客户在当日是否有商机关联（只要有一条记录有商机，即认为该客户有商机）
                has_opp_map = sales_info['account_has_opportunity']
                has_opp_map[account_id] = has_opp_map.get(account_id, False) or bool(record.opportunity_id)
                
                # 记录客户的首次/非首次拜访状态（按 account_id 维度去重）
                account_status = sales_info['account_visit_status'].setdefault(
                    account_id, {'has_first': False, 'has_non_first': False}
                )
                if is_first_visit:
                    account_status['has_first'] = True
                else:
                    account_status['has_non_first'] = True

                # 如果当前拜访关联了商机，则记录到去重商机映射中
                if record.opportunity_id:
                    opp_id = record.opportunity_id
                    if opp_id not in sales_info['opportunity_map']:
                        sales_info['opportunity_map'][opp_id] = {
                            'opportunity_id': opp_id,
                            'opportunity_name': record.opportunity_name or "",
                        }
            
            # 处理合作伙伴拜访记录：
            # 仅当「没有客户」且「有合作伙伴」时，才计入合作伙伴维度
            if record.partner_id and not record.account_id:
                partner_id = record.partner_id
                
                # 记录合作伙伴的首次/非首次拜访状态
                partner_status = sales_info['partner_visit_status'].setdefault(
                    partner_id, {'has_first': False, 'has_non_first': False}
                )
                if is_first_visit:
                    partner_status['has_first'] = True
                else:
                    partner_status['has_non_first'] = True
        
        # 转换为列表格式，并根据业务规则汇总统计：
        # 1. 客户和合作伙伴的首次拜访/多次跟进数量：
        #    - 同一客户/合作伙伴当天多次拜访，只要有一次标记为首次，即计入首次；否则计入多次跟进。
        # 2. 输出去重后的商机列表和无商机客户列表。
        statistics_results: List[Dict[str, Any]] = []
        for recorder_id, sales_info in sales_dict.items():
            account_visit_status = sales_info['account_visit_status']
            partner_visit_status = sales_info['partner_visit_status']
            account_has_opportunity = sales_info['account_has_opportunity']
            account_names = sales_info['account_names']
            
            # 客户维度统计
            first_visit_account_count = 0
            multi_visit_account_count = 0
            for account_id, status in account_visit_status.items():
                if status['has_first']:
                    first_visit_account_count += 1
                elif status['has_non_first']:
                    multi_visit_account_count += 1
            
            # 合作伙伴维度统计
            first_visit_partner_count = 0
            multi_visit_partner_count = 0
            for partner_id, status in partner_visit_status.items():
                if status['has_first']:
                    first_visit_partner_count += 1
                elif status['has_non_first']:
                    multi_visit_partner_count += 1
            
            # 去重后的商机列表（在循环过程中已累计到 opportunity_map 中）
            opportunities = list(sales_info['opportunity_map'].values())
            
            # 无任何商机关联的去重客户列表（当日该销售仅以客户维度跟进的客户）
            accounts_without_opportunity: List[Dict[str, Any]] = []
            for account_id, has_opp in account_has_opportunity.items():
                if not has_opp:
                    accounts_without_opportunity.append({
                        'account_id': account_id,
                        'account_name': account_names.get(account_id, ""),
                    })
            
            result = {
                'recorder_id': sales_info['recorder_id'],
                'recorder': sales_info['recorder'],
                'department': sales_info['department'],
                # 客户维度
                'end_customer_total_follow_up': len(account_visit_status),   # 跟进客户总数（按客户ID去重）
                'end_customer_total_first_visit': first_visit_account_count,  # 首次拜访客户总数
                'end_customer_total_multi_visit': multi_visit_account_count,  # 多次跟进客户总数
                # 合作伙伴维度
                'partner_total_follow_up': len(partner_visit_status),        # 跟进合作伙伴总数
                'partner_total_first_visit': first_visit_partner_count,      # 首次拜访合作伙伴总数
                'partner_total_multi_visit': multi_visit_partner_count,      # 多次跟进合作伙伴总数
                # 其他统计
                'opportunities': opportunities,
                'accounts_without_opportunity': accounts_without_opportunity,
            }
            statistics_results.append(result)
        
        # 汇总日志输出公司级别的统计概览（可选）
        total_first_accounts = sum(r['first_visit_account_count'] for r in statistics_results)
        total_multi_accounts = sum(r['multi_visit_account_count'] for r in statistics_results)
        total_first_partners = sum(r['first_visit_partner_count'] for r in statistics_results)
        total_multi_partners = sum(r['multi_visit_partner_count'] for r in statistics_results)
        
        logger.info(f"找到 {len(statistics_results)} 个销售")
        logger.info(f"统计：首次拜访客户 {total_first_accounts} 个，多次跟进客户 {total_multi_accounts} 个")
        logger.info(f"统计：首次拜访合作伙伴 {total_first_partners} 个，多次跟进合作伙伴 {total_multi_partners} 个")
        
        return statistics_results

    
    def get_sales_complete_daily_report(self, session: Session, target_date: date) -> List[Dict]:
        """
        获取完整的销售个人日报数据，包括：
        1. 每个销售的拜访统计数据（来自 get_sales_daily_statistics）
        2. 基于去重商机列表和无商机客户列表，从客户商机评估表获取的评估详情
        
        Args:
            session: 数据库会话
            target_date: 目标日期
            
        Returns:
            List[Dict]: 完整的销售个人日报数据列表
        """
        logger.info(f"开始获取 {target_date} 的完整销售个人日报数据")
        
        # 1. 获取指定日期的销售拜访统计数据
        statistics_records = self.get_sales_daily_statistics(session, target_date)
        
        if not statistics_records:
            logger.info(f"{target_date} 没有找到销售个人日报数据")
            return []
        
        # 2. 为每个销售获取评估详情（按去重商机列表 + 无商机客户列表查询）
        complete_reports: List[Dict[str, Any]] = []
        
        from app.core.config import settings
        for stats in statistics_records:
            opportunities = stats.get('opportunities', []) or []
            accounts_without_opportunity = stats.get('accounts_without_opportunity', []) or []
            
            # 基于去重后的商机列表和无商机客户列表，从客户商机评估表查询评估详情
            assessment_details = self._get_opportunity_assessments_for_sales(
                session=session,
                target_date=target_date,
                opportunities=opportunities,
                accounts_without_opportunity=accounts_without_opportunity,
            )
            
            # 填充评估详情中的链接信息（根据是否有商机名称拼接不同的URL）
            base_visit_url = (
                f"{settings.VISIT_DETAIL_PAGE_URL}"
                f"?start_date={target_date}&end_date={target_date}"
            )
            
            for assessment in assessment_details['first']:
                # assessment['sales_name'] = recorder_name
                # assessment['department_name'] = ""
                if assessment.get("opportunity_name"):
                    assessment['account_visit_details'] = (
                        f"{base_visit_url}"
                        f"&account_name={assessment['account_name']}"
                        f"&opportunity_name={assessment['opportunity_name']}"
                    )
                else:
                    assessment['account_visit_details'] = (
                        f"{base_visit_url}"
                        f"&account_name={assessment['account_name']}"
                    )
            
            for assessment in assessment_details['multi']:
                # assessment['sales_name'] = recorder_name
                # assessment['department_name'] = ""
                if assessment.get("opportunity_name"):
                    assessment['account_visit_details'] = (
                        f"{base_visit_url}"
                        f"&account_name={assessment['account_name']}"
                        f"&opportunity_name={assessment['opportunity_name']}"
                    )
                else:
                    assessment['account_visit_details'] = (
                        f"{base_visit_url}"
                        f"&account_name={assessment['account_name']}"
                    )
            
            # 对评估数据进行排序（红灯>黄灯-团队名称-销售名称）
            sorted_first_assessments = self._sort_assessments(assessment_details['first'])
            sorted_multi_assessments = self._sort_assessments(assessment_details['multi'])
            
            # 移除用于排序的临时字段
            for assessment in sorted_first_assessments:
                assessment.pop('assessment_flag_raw', None)
            for assessment in sorted_multi_assessments:
                assessment.pop('assessment_flag_raw', None)
            
            # 获取评估统计（按首次/多次、红黄绿灯计数）
            statistics = assessment_details.get('statistics', {
                "first": {"red": 0, "yellow": 0, "green": 0},
                "multi": {"red": 0, "yellow": 0, "green": 0}
            })
            
            # 添加新的统计字段（stats已包含部分统计数据）
            stats_with_assessment: Dict[str, Any] = {
                **stats,  # 包含部分统计数据
                'report_date': target_date,
                'first_visit_red_count': statistics.get('first', {}).get('red', 0),
                'first_visit_yellow_count': statistics.get('first', {}).get('yellow', 0),
                'first_visit_green_count': statistics.get('first', {}).get('green', 0),
                'multi_visit_red_count': statistics.get('multi', {}).get('red', 0),
                'multi_visit_yellow_count': statistics.get('multi', {}).get('yellow', 0),
                'multi_visit_green_count': statistics.get('multi', {}).get('green', 0),
            }
            complete_report = {
                **stats_with_assessment,  # 包含所有统计数据（包括首次和多次拜访的红黄绿灯统计）
                'first_assessment': sorted_first_assessments,
                'multi_assessment': sorted_multi_assessments,
                'visit_detail_page': (
                    f"{settings.VISIT_DETAIL_PAGE_URL}"
                    f"?start_date={target_date}&end_date={target_date}"
                )
            }
            
            complete_reports.append(complete_report)
            
            logger.info(
                f"销售 {stats['recorder']} 的完整日报数据已组装，"
                f"所在团队 {stats['department']}，"
                f"排除绿灯后，包含 {len(assessment_details['first'])} 个首次评估明细，"
                f"{len(assessment_details['multi'])} 个多次评估明细"
            )
        
        return complete_reports
    
    def get_assessment_by_correlation_id(self, session: Session, correlation_id: str) -> Dict[str, Any]:
        """
        通过correlation_id获取评估详情数据
        
        Args:
            session: 数据库会话
            correlation_id: 关联ID
            
        Returns:
            Dict: 包含first、multi和statistics三个键的字典
                - first: 首次拜访评估详情列表（不包含绿灯）
                - multi: 多次拜访评估详情列表（不包含绿灯）
                - statistics: 统计数据字典，包含first和multi的红黄绿灯数量（包含绿灯）
        """
        logger.debug(f"通过correlation_id获取评估数据: {correlation_id}")
        
        # 优化：只查询一次，获取所有数据（包括绿灯）
        query = select(CRMAccountAssessment).where(
            CRMAccountAssessment.correlation_id == correlation_id
        )
        
        all_assessment_records = session.exec(query).all()
        
        if not all_assessment_records:
            logger.debug(f"correlation_id {correlation_id} 没有找到评估记录")
            return {
                "first": [],
                "multi": [],
                "statistics": {
                    "first": {"red": 0, "yellow": 0, "green": 0},
                    "multi": {"red": 0, "yellow": 0, "green": 0}
                }
            }
        
        # 优化：一次遍历同时完成统计和列表构建
        first_stats = {"red": 0, "yellow": 0, "green": 0}
        multi_stats = {"red": 0, "yellow": 0, "green": 0}
        first_assessments = []
        multi_assessments = []
        
        for assessment in all_assessment_records:
            flag = (assessment.assessment_flag or "").lower()
            is_first_visit = assessment.is_first_visit
            
            # 统计所有记录（包括绿灯）
            if is_first_visit:
                if flag == "red":
                    first_stats["red"] += 1
                elif flag == "yellow":
                    first_stats["yellow"] += 1
                elif flag == "green":
                    first_stats["green"] += 1
            else:
                if flag == "red":
                    multi_stats["red"] += 1
                elif flag == "yellow":
                    multi_stats["yellow"] += 1
                elif flag == "green":
                    multi_stats["green"] += 1
            
            # 构建列表（只包含非绿灯记录）
            if flag != "green":
                assessment_data = {
                    'account_name': self._format_empty_value(assessment.account_name),
                    'opportunity_names': self._format_empty_value(self._format_opportunity_names(assessment.opportunity_names)),
                    'follow_up_note': self._format_empty_value(assessment.follow_up_note),
                    'follow_up_note_en': self._format_empty_value(assessment.follow_up_note_en),
                    'follow_up_next_step': self._format_empty_value(assessment.follow_up_next_step),
                    'follow_up_next_step_en': self._format_empty_value(assessment.follow_up_next_step_en),
                    'assessment_flag': self._convert_assessment_flag(assessment.assessment_flag),
                    'assessment_description': self._format_empty_value(assessment.assessment_description),
                    'assessment_description_en': self._format_empty_value(assessment.assessment_description_en),
                    'account_level': self._format_empty_value(assessment.account_level),
                    'sales_name': "",  # 这个字段将在上层填充
                    'department_name': "",  # 这个字段将在上层填充
                    'assessment_flag_raw': assessment.assessment_flag or ""  # 保留原始标志用于排序
                }
                
                if is_first_visit:
                    first_assessments.append(assessment_data)
                else:
                    multi_assessments.append(assessment_data)
        
        logger.debug(
            f"correlation_id {correlation_id} 找到 {len(all_assessment_records)} 条评估记录（包含绿灯），"
            f"首次拜访: 红{first_stats['red']} 黄{first_stats['yellow']} 绿{first_stats['green']}, "
            f"多次拜访: 红{multi_stats['red']} 黄{multi_stats['yellow']} 绿{multi_stats['green']}, "
            f"非绿灯记录: 首次{len(first_assessments)} 多次{len(multi_assessments)}"
        )
        
        # 按照指定规则排序：红灯>黄灯-团队名称-销售名称
        # 注意：这里的排序会在上层填充sales_name和department_name后进行
        
        return {
            "first": first_assessments,
            "multi": multi_assessments,
            "statistics": {
                "first": first_stats,
                "multi": multi_stats
            }
        }
    
    def _get_opportunity_assessments_for_sales(
        self,
        session: Session,
        target_date: date,
        opportunities: List[Dict[str, Any]],
        accounts_without_opportunity: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        基于去重的商机列表和无商机客户列表，从客户商机评估表获取评估详情。
        
        评估数据划分为：
        - first: 首次拜访评估详情列表（不包含绿灯）
        - multi: 多次跟进评估详情列表（不包含绿灯）
        - statistics: 统计数据字典，包含first和multi的红黄绿灯数量（包含绿灯）
        """
        # 收集目标商机ID和无商机客户ID
        opportunity_ids = {
            item.get("opportunity_id")
            for item in opportunities
            if item.get("opportunity_id")
        }
        account_ids_without_opp = {
            item.get("account_id")
            for item in accounts_without_opportunity
            if item.get("account_id")
        }
        
        if not opportunity_ids and not account_ids_without_opp:
            # 没有任何目标，直接返回空结果
            return {
                "first": [],
                "multi": [],
                "statistics": {
                    "first": {"red": 0, "yellow": 0, "green": 0},
                    "multi": {"red": 0, "yellow": 0, "green": 0},
                },
            }
        
        # 查询匹配的评估记录：
        # 1. assessment_date == target_date
        # 2. (opportunity_id 在去重商机列表中) OR (opportunity_id 为空 且 account_id 在无商机客户列表中)
        all_records: List[CRMAccountOpportunityAssessment] = []
        
        if opportunity_ids:
            query_opps = select(CRMAccountOpportunityAssessment).where(
                CRMAccountOpportunityAssessment.assessment_date == target_date,
                CRMAccountOpportunityAssessment.opportunity_id.in_(opportunity_ids),
            )
            all_records.extend(session.exec(query_opps).all())
        
        if account_ids_without_opp:
            query_accounts = select(CRMAccountOpportunityAssessment).where(
                CRMAccountOpportunityAssessment.assessment_date == target_date,
                CRMAccountOpportunityAssessment.opportunity_id.is_(None),
                CRMAccountOpportunityAssessment.account_id.in_(account_ids_without_opp),
            )
            all_records.extend(session.exec(query_accounts).all())
        
        if not all_records:
            return {
                "first": [],
                "multi": [],
                "statistics": {
                    "first": {"red": 0, "yellow": 0, "green": 0},
                    "multi": {"red": 0, "yellow": 0, "green": 0},
                },
            }
        
        # 统计与分组逻辑：与 get_assessment_by_correlation_id 保持一致的口径
        first_stats = {"red": 0, "yellow": 0, "green": 0}
        multi_stats = {"red": 0, "yellow": 0, "green": 0}
        first_assessments: List[Dict[str, Any]] = []
        multi_assessments: List[Dict[str, Any]] = []
        
        for assessment in all_records:
            flag = (assessment.assessment_flag or "").lower()
            is_first_visit = bool(assessment.is_first_visit)
            
            # 统计所有记录（包括绿灯）
            if is_first_visit:
                if flag == "red":
                    first_stats["red"] += 1
                elif flag == "yellow":
                    first_stats["yellow"] += 1
                elif flag == "green":
                    first_stats["green"] += 1
            else:
                if flag == "red":
                    multi_stats["red"] += 1
                elif flag == "yellow":
                    multi_stats["yellow"] += 1
                elif flag == "green":
                    multi_stats["green"] += 1
            
            # 列表仅保留非绿灯记录
            if flag != "green":
                assessment_data: Dict[str, Any] = {
                    "account_name": self._format_empty_value(assessment.account_name),
                    "opportunity_name": self._format_empty_value(assessment.opportunity_name),
                    "follow_up_note": self._format_empty_value(assessment.follow_up_note),
                    "follow_up_note_en": self._format_empty_value(assessment.follow_up_note_en or assessment.follow_up_note),
                    "follow_up_next_step": self._format_empty_value(assessment.follow_up_next_step),
                    "follow_up_next_step_en": self._format_empty_value(assessment.follow_up_next_step_en or assessment.follow_up_next_step),
                    "assessment_flag": self._convert_assessment_flag(assessment.assessment_flag),
                    "assessment_description": self._format_empty_value(assessment.assessment_description),
                    "assessment_description_en": self._format_empty_value(assessment.assessment_description_en),
                    "account_level": assessment.account_level,
                    "assessment_flag_raw": assessment.assessment_flag or "",
                }
                
                if is_first_visit:
                    first_assessments.append(assessment_data)
                else:
                    multi_assessments.append(assessment_data)
        
        return {
            "first": first_assessments,
            "multi": multi_assessments,
            "statistics": {
                "first": first_stats,
                "multi": multi_stats,
            },
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
    
    def _format_empty_value(self, value: Any) -> str:
        """
        统一处理空值，将None、空字符串等替换为"--"
        
        Args:
            value: 要处理的值
            
        Returns:
            处理后的字符串，空值返回"--"
        """
        if value is None:
            return "--"
        if isinstance(value, str) and not value.strip():
            return "--"
        return str(value).strip()
    
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
    

    def generate_sales_daily_statistics(self, session: Session, target_date: Optional[date] = None) -> int:
        """
        生成完整销售个人日报数据的主方法
        通过correlation_id关联两张表，获取完整的日报信息
        
        Args:
            session: 数据库会话
            target_date: 目标日期，默认为昨天
            
        Returns:
            int: 处理的销售人员数量
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"开始生成 {target_date} 的完整销售个人日报数据")
        
        try:
            # 获取完整的日报数据（包括通过correlation_id关联的评估详情）
            complete_reports = self.get_sales_complete_daily_report(session, target_date)
            
            sales_count = len(complete_reports)
            
            if sales_count > 0:
                logger.info(f"成功生成 {target_date} 的完整销售个人日报数据，包含 {sales_count} 个销售人员的数据")
                
                # 统计总的评估数量
                total_first_assessments = sum(len(report['first_assessment']) for report in complete_reports)
                total_multi_assessments = sum(len(report['multi_assessment']) for report in complete_reports)
                
                logger.info(f"总计: {total_first_assessments} 个销售个人首次拜访评估，{total_multi_assessments} 个销售个人多次拜访评估")
                
                # 推送个人日报（只在开关启用时发送卡片）
                from app.core.config import settings
                if settings.CRM_DAILY_REPORT_FEISHU_ENABLED:
                    self._send_sales_daily_report_notifications(session, complete_reports)
                else:
                    logger.info("CRM日报飞书推送功能已禁用，跳过个人日报卡片推送")
            else:
                logger.warning(f"{target_date} 没有找到任何销售个人日报数据")
            
            return sales_count
            
        except Exception as e:
            logger.error(f"生成完整销售个人日报数据失败: {e}")
            raise
    
    def _send_sales_daily_report_notifications(self, session: Session, complete_reports: List[Dict]) -> None:
        """
        向销售人员发送CRM日报飞书卡片通知
        
        Args:
            session: 数据库会话
            complete_reports: 完整的日报数据列表
        """
        
        total_notifications = 0
        successful_notifications = 0
        
        for report in complete_reports:
            try:
                # 转换日期格式为字符串，因为JSON序列化不支持date对象
                # 同时将sales_name字段重命名为recorder，以适配飞书卡片模板
                # 将统计数据组织成statistics数组，以适配飞书卡片模板
                statistics_data = {
                    'end_customer_total_follow_up': report.get('end_customer_total_follow_up', 0),
                    'end_customer_total_first_visit': report.get('end_customer_total_first_visit', 0),
                    'end_customer_total_multi_visit': report.get('end_customer_total_multi_visit', 0),
                    'partner_total_follow_up': report.get('partner_total_follow_up', 0),
                    'partner_total_first_visit': report.get('partner_total_first_visit', 0),
                    'partner_total_multi_visit': report.get('partner_total_multi_visit', 0),
                    'assessment_red_count': report.get('assessment_red_count', 0),
                    'assessment_yellow_count': report.get('assessment_yellow_count', 0),
                    'assessment_green_count': report.get('assessment_green_count', 0),
                    'first_visit_red_count': report.get('first_visit_red_count', 0),
                    'first_visit_yellow_count': report.get('first_visit_yellow_count', 0),
                    'first_visit_green_count': report.get('first_visit_green_count', 0),
                    'multi_visit_red_count': report.get('multi_visit_red_count', 0),
                    'multi_visit_yellow_count': report.get('multi_visit_yellow_count', 0),
                    'multi_visit_green_count': report.get('multi_visit_green_count', 0)
                }
                
                report_data = {
                    'recorder_id': report.get('recorder_id', ''),
                    'recorder': report.get('recorder', ''),
                    'department_name': report.get('department_name', ''),
                    'report_date': report['report_date'].isoformat() if hasattr(report.get('report_date'), 'isoformat') else str(report.get('report_date')),
                    'statistics': [statistics_data],  # 将统计数据组织成数组
                    'visit_detail_page': report.get('visit_detail_page', ''),
                    'first_assessment': report.get('first_assessment', []),
                    'multi_assessment': report.get('multi_assessment', [])
                }
                
                # 发送飞书通知
                result = platform_notification_service.send_sales_daily_report_notification(
                    db_session=session,
                    daily_report_data=report_data
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
    
    def _generate_and_send_department_daily_reports(self, session: Session, target_date: date) -> None:
        """
        生成并推送部门日报
        即使部门没有数据，也会给负责人发送空数据的卡片通知
        
        Args:
            session: 数据库会话
            target_date: 目标日期
        """
        from app.core.config import settings
        
        logger.info(f"[部门日报] 开始生成并推送 {target_date} 的部门日报")
        
        # 获取有数据的部门报告
        department_reports_with_data = self.aggregate_department_reports(session, target_date)
        
        # 从 OAuth 服务获取所有部门及其负责人
        all_departments_with_managers = self._get_departments_with_managers_from_oauth()
        
        # 创建有数据的部门名称集合
        departments_with_data = {report['department_name'] for report in department_reports_with_data}
        
        # 为没有数据的部门生成空数据的报告
        department_reports_no_data = []
        for department_name, managers in all_departments_with_managers.items():
            # 只处理有负责人的部门
            if managers and department_name not in departments_with_data:
                empty_report = self._aggregate_single_department(
                    department_name=department_name,
                    target_date=target_date,
                )
                department_reports_no_data.append(empty_report)
                logger.info(f"为部门 {department_name} 生成空数据报告（无销售数据）")
        
        # 合并有数据和没数据的部门报告
        all_department_reports = department_reports_with_data + department_reports_no_data
        
        if not all_department_reports:
            logger.warning(f"{target_date} 没有找到任何部门，跳过部门日报推送")
            return
        
        # 检查飞书推送开关（只在发送卡片时检查，不影响计算逻辑）
        if not settings.CRM_DAILY_REPORT_FEISHU_ENABLED:
            logger.info("CRM日报飞书推送功能已禁用，跳过部门日报卡片推送（但数据已生成）")
            return
        
        total_departments = 0
        successful_departments = 0

        for department_report in all_department_reports:
            try:
                # 获取该部门的负责人信息
                department_name = department_report.get('department_name')
                managers = all_departments_with_managers.get(department_name) if department_name else None
                
                # 发送部门日报飞书通知
                result = platform_notification_service.send_department_daily_report_notification(
                    db_session=session,
                    department_report_data=department_report,
                    recipients=managers
                )
                
                total_departments += 1
                
                if result["success"]:
                    successful_departments += 1
                    has_data = department_report['department_name'] in departments_with_data
                    data_status = "有数据" if has_data else "无数据"
                    logger.info(
                        f"成功为部门 {department_report['department_name']} ({data_status}) 发送日报飞书通知，"
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
            f"CRM部门日报飞书通知发送完成: {successful_departments}/{total_departments} 个部门的通知发送成功 "
            f"(有数据: {len(departments_with_data)}, 无数据: {len(department_reports_no_data)})"
        )
    
    def _generate_and_send_company_daily_report(self, session: Session, target_date: date) -> None:
        """
        生成并推送公司日报
        
        Args:
            session: 数据库会话
            target_date: 目标日期
        """
        from app.core.config import settings
        
        logger.info(f"[公司日报] 开始生成并推送 {target_date} 的公司日报")
        
        # 生成公司汇总报告（无论开关是否启用，都执行计算逻辑）
        company_report = self.aggregate_company_report(session, target_date)
        
        # 检查飞书推送开关（只在发送卡片时检查，不影响计算逻辑）
        if not settings.CRM_DAILY_REPORT_FEISHU_ENABLED:
            logger.info("CRM日报飞书推送功能已禁用，跳过公司日报卡片推送（但数据已生成）")
            return
        
        try:
            # 发送公司日报飞书通知
            result = platform_notification_service.send_company_daily_report_notification(
                db_session=session,
                company_report_data=company_report
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
        按部门汇总日报数据（基于 crm_department_daily_summary 表）
        
        Args:
            session: 数据库会话
            target_date: 目标日期，默认为昨天
            
        Returns:
            List[Dict]: 部门日报数据列表
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"开始从 crm_department_daily_summary 表汇总 {target_date} 的部门日报数据")
        
        from app.core.config import settings
        
        # 直接从部门/公司汇总表中查询部门级数据
        query = select(CRMDepartmentDailySummary).where(
            CRMDepartmentDailySummary.report_date == target_date,
            CRMDepartmentDailySummary.summary_type == "department",
        )
        records: List[CRMDepartmentDailySummary] = session.exec(query).all()
        
        if not records:
            logger.warning(f"{target_date} 在 crm_department_daily_summary 中没有找到任何部门级日报数据")
            return []
        
        department_reports: List[Dict[str, Any]] = []
        
        for record in records:
            department_name = record.department_name or ""
            
            # 统计字段：直接使用汇总表中的结果（对齐 CRMDepartmentDailySummary 新字段定义）
            total_stats = {
                # 最终客户 - 总体
                "assessment_red_count": record.end_customer_total_red_count or 0,
                "assessment_yellow_count": record.end_customer_total_yellow_count or 0,
                "assessment_green_count": record.end_customer_total_green_count or 0,
                "end_customer_total_follow_up": record.end_customer_total_count or 0,
                # 最终客户 - 首次跟进
                "first_visit_red_count": record.end_customer_first_visit_red_count or 0,
                "first_visit_yellow_count": record.end_customer_first_visit_yellow_count or 0,
                "first_visit_green_count": record.end_customer_first_visit_green_count or 0,
                "end_customer_total_first_visit": record.end_customer_first_visit_count or 0,
                # 最终客户 - 多次跟进
                "multi_visit_red_count": record.end_customer_regular_visit_red_count or 0,
                "multi_visit_yellow_count": record.end_customer_regular_visit_yellow_count or 0,
                "multi_visit_green_count": record.end_customer_regular_visit_green_count or 0,
                "end_customer_total_multi_visit": record.end_customer_regular_visit_count or 0,
                # 合作伙伴统计
                "partner_total_follow_up": record.partner_total_count or 0,
                "partner_total_first_visit": record.partner_first_visit_count or 0,
                "partner_total_multi_visit": record.partner_regular_visit_count or 0,
            }
            
            department_report = {
                "department_name": department_name,
                "report_date": record.report_date,
                # 统计数组：供卡片模板展示数值类指标
                "statistics": [total_stats],
                # 首次拜访汇总内容
                "first_assessment":[{
                    "assessment_description": record.summary_first_visit or "",
                    "assessment_description_en": record.summary_first_visit or "",
                    "account_visit_details": f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={target_date}&end_date={target_date}&department_name={department_name}&is_first_visit=true"
                }],
                # 多次跟进汇总内容
                "multi_assessment":[{
                    "assessment_description": record.summary_regular_visit or "",
                    "assessment_description_en": record.summary_regular_visit or "",
                    "account_visit_details": f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={target_date}&end_date={target_date}&department_name={department_name}&is_first_visit=false"
                }],
            }
            
            department_reports.append(department_report)
        
        logger.info(f"完成 {target_date} 的部门日报汇总（基于 crm_department_daily_summary），共 {len(department_reports)} 个部门")
        
        return department_reports
    
    def _aggregate_single_department(self, department_name: str, target_date: date) -> Dict[str, Any]:
        """
        为没有任何数据的部门生成空的部门日报结构（数值为0，文案为空）。
        该结构与 aggregate_department_reports 返回的元素保持一致，便于直接用于卡片推送。
        """
        from app.core.config import settings
        
        # 统计字段格式需与 aggregate_department_reports 中保持一致
        total_stats = {
            # 最终客户 - 总体（全为空数据）
            "assessment_red_count": 0,
            "assessment_yellow_count": 0,
            "assessment_green_count": 0,
            "end_customer_total_follow_up": 0,
            # 最终客户 - 首次跟进
            "first_visit_red_count": 0,
            "first_visit_yellow_count": 0,
            "first_visit_green_count": 0,
            "end_customer_total_first_visit": 0,
            # 最终客户 - 多次跟进
            "multi_visit_red_count": 0,
            "multi_visit_yellow_count": 0,
            "multi_visit_green_count": 0,
            "end_customer_total_multi_visit": 0,
            # 合作伙伴统计
            "partner_total_follow_up": 0,
            "partner_total_first_visit": 0,
            "partner_total_multi_visit": 0,
        }
        
        # 为了兼容飞书卡片的数据结构，即使没有数据，也构造一条“空”的首次/多次汇总记录
        base_visit_url = (
            f"{settings.VISIT_DETAIL_PAGE_URL}"
            f"?start_date={target_date}&end_date={target_date}"
        )
        
        department_report: Dict[str, Any] = {
            "department_name": department_name,
            "report_date": target_date,
            "statistics": [total_stats],
            # 首次拜访汇总内容（空）
            "first_assessment": [
                {
                    "assessment_description": "",
                    "assessment_description_en": "",
                    "account_visit_details": (
                        f"{base_visit_url}"
                        f"&department_name={department_name}&is_first_visit=true"
                    ),
                }
            ],
            # 多次跟进汇总内容（空）
            "multi_assessment": [
                {
                    "assessment_description": "",
                    "assessment_description_en": "",
                    "account_visit_details": (
                        f"{base_visit_url}"
                        f"&department_name={department_name}&is_first_visit=false"
                    ),
                }
            ],
        }
        
        return department_report
    
    def aggregate_company_report(self, session: Session, target_date: Optional[date] = None) -> Dict:
        """
        汇总公司级日报数据（基于 crm_department_daily_summary 表）
        
        Args:
            session: 数据库会话
            target_date: 目标日期，默认为昨天
            
        Returns:
            Dict: 公司汇总日报数据
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"开始从 crm_department_daily_summary 表汇总 {target_date} 的公司日报数据")
        
        from app.core.config import settings
        
        # 直接从部门/公司汇总表中查询公司级数据（summary_type = 'company'）
        query = select(CRMDepartmentDailySummary).where(
            CRMDepartmentDailySummary.report_date == target_date,
            CRMDepartmentDailySummary.summary_type == "company",
        )
        record: Optional[CRMDepartmentDailySummary] = session.exec(query).first()
        
        # 统计字段和结构与部门日报保持一致，便于共享同一飞书模板
        if not record:
            logger.warning(f"{target_date} 在 crm_department_daily_summary 中没有找到公司级日报数据，将返回空统计")
            total_stats = {
                # 最终客户 - 总体（全为空数据）
                "assessment_red_count": 0,
                "assessment_yellow_count": 0,
                "assessment_green_count": 0,
                "end_customer_total_follow_up": 0,
                # 最终客户 - 首次跟进
                "first_visit_red_count": 0,
                "first_visit_yellow_count": 0,
                "first_visit_green_count": 0,
                "end_customer_total_first_visit": 0,
                # 最终客户 - 多次跟进
                "multi_visit_red_count": 0,
                "multi_visit_yellow_count": 0,
                "multi_visit_green_count": 0,
                "end_customer_total_multi_visit": 0,
                # 合作伙伴统计
                "partner_total_follow_up": 0,
                "partner_total_first_visit": 0,
                "partner_total_multi_visit": 0,
            }
            summary_first_visit = ""
            summary_regular_visit = ""
        else:
            total_stats = {
                # 最终客户 - 总体
                "assessment_red_count": record.end_customer_total_red_count or 0,
                "assessment_yellow_count": record.end_customer_total_yellow_count or 0,
                "assessment_green_count": record.end_customer_total_green_count or 0,
                "end_customer_total_follow_up": record.end_customer_total_count or 0,
                # 最终客户 - 首次跟进
                "first_visit_red_count": record.end_customer_first_visit_red_count or 0,
                "first_visit_yellow_count": record.end_customer_first_visit_yellow_count or 0,
                "first_visit_green_count": record.end_customer_first_visit_green_count or 0,
                "end_customer_total_first_visit": record.end_customer_first_visit_count or 0,
                # 最终客户 - 多次跟进
                "multi_visit_red_count": record.end_customer_regular_visit_red_count or 0,
                "multi_visit_yellow_count": record.end_customer_regular_visit_yellow_count or 0,
                "multi_visit_green_count": record.end_customer_regular_visit_green_count or 0,
                "end_customer_total_multi_visit": record.end_customer_regular_visit_count or 0,
                # 合作伙伴统计
                "partner_total_follow_up": record.partner_total_count or 0,
                "partner_total_first_visit": record.partner_first_visit_count or 0,
                "partner_total_multi_visit": record.partner_regular_visit_count or 0,
            }
            summary_first_visit = record.summary_first_visit or ""
            summary_regular_visit = record.summary_regular_visit or ""

        base_visit_url = (
            f"{settings.VISIT_DETAIL_PAGE_URL}"
            f"?start_date={target_date}&end_date={target_date}"
        )
        
        # 公司日报结构与部门日报保持一致：
        # - statistics: 数值汇总
        # - first_assessment: 公司层面的首次拜访汇总文案
        # - multi_assessment: 公司层面的多次跟进汇总文案
        company_report = {
            "report_date": target_date,
            "statistics": [total_stats],
            "first_assessment": [
                {
                    "assessment_description": summary_first_visit,
                    "assessment_description_en": summary_first_visit,
                    "account_visit_details": (
                        f"{base_visit_url}&is_first_visit=true"
                    ),
                }
            ],
            "multi_assessment": [
                {
                    "assessment_description": summary_regular_visit,
                    "assessment_description_en": summary_regular_visit,
                    "account_visit_details": (
                        f"{base_visit_url}&is_first_visit=false"
                    ),
                }
            ],
        }
        
        logger.info("公司日报汇总完成（基于 crm_department_daily_summary）")
        
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


    def get_weekly_statistics(self, session: Session, start_date: date, end_date: date) -> List[Dict]:
        """
        获取指定日期范围的销售周报统计数据
        
        Args:
            session: 数据库会话
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            List[Dict]: 统计结果列表
        """
        logger.info(f"开始获取 {start_date} 到 {end_date} 的销售周报统计数据")
        
        # 从crm_daily_account_statistics表查询指定日期范围的数据
        query = select(CRMDailyAccountStatistics).where(
            and_(
                CRMDailyAccountStatistics.report_date >= start_date,
                CRMDailyAccountStatistics.report_date <= end_date
            )
        )
        
        statistics_records = session.exec(query).all()
        
        if not statistics_records:
            logger.info(f"{start_date} 到 {end_date} 没有找到任何统计记录")
            return []
        
        logger.info(f"找到 {len(statistics_records)} 条 {start_date} 到 {end_date} 的统计记录")
        
        # 转换为字典格式
        statistics_results = []
        for record in statistics_records:
            statistics_data = {
                'unique_id': record.unique_id,
                'report_date': record.report_date,
                'sales_id': record.sales_id,
                'sales_name': self._format_empty_value(record.sales_name),
                'department_name': self._format_empty_value(record.department_name),
                'assessment_red_count': record.assessment_red_count or 0,
                'assessment_yellow_count': record.assessment_yellow_count or 0,
                'assessment_green_count': record.assessment_green_count or 0,
                'end_customer_total_follow_up': record.end_customer_total_follow_up or 0,
                'end_customer_total_first_visit': record.end_customer_total_first_visit or 0,
                'end_customer_total_multi_visit': record.end_customer_total_multi_visit or 0,
                'partner_total_follow_up': record.partner_total_follow_up or 0,
                'partner_total_first_visit': record.partner_total_first_visit or 0,
                'partner_total_multi_visit': record.partner_total_multi_visit or 0,
            }
            statistics_results.append(statistics_data)
        
        return statistics_results
    
    def aggregate_department_weekly_reports(self, session: Session, start_date: date, end_date: date) -> List[Dict]:
        """
        按部门汇总销售周报数据
        
        Args:
            session: 数据库会话
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            List[Dict]: 部门周报数据列表
        """
        logger.info(f"开始汇总 {start_date} 到 {end_date} 的部门周报数据")
        
        # 获取指定日期范围的所有统计数据
        statistics_records = self.get_weekly_statistics(session, start_date, end_date)
        
        if not statistics_records:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何销售周报数据")
            return []
        
        # 按部门分组
        department_groups = {}
        
        for record in statistics_records:
            department_name = record.get('department_name', '未知部门')
            
            if department_name not in department_groups:
                department_groups[department_name] = []
            
            department_groups[department_name].append(record)
        
        # 生成部门汇总报告
        department_reports = []
        
        for department_name, sales_records in department_groups.items():
            department_report = self._aggregate_single_department_weekly(
                department_name=department_name,
                sales_records=sales_records,
                start_date=start_date,
                end_date=end_date,
                session=session
            )
            department_reports.append(department_report)
        
        logger.info(f"完成 {start_date} 到 {end_date} 的部门周报汇总，共 {len(department_reports)} 个部门")
        
        return department_reports
    
    def _aggregate_single_department_weekly(self, department_name: str, sales_records: List[Dict], start_date: date, end_date: date, session: Session = None) -> Dict:
        """
        汇总单个部门的周报数据
        
        Args:
            department_name: 部门名称
            sales_records: 该部门所有销售的周报数据
            start_date: 开始日期
            end_date: 结束日期
            session: 数据库会话（可选）
            
        Returns:
            Dict: 部门汇总周报数据
        """
        from app.core.config import settings
        
        # 获取该部门的销售人员数量（不包含leader）
        sales_count = self._get_department_sales_count(department_name, session)
        
        # 汇总统计数据（直接加和）
        total_stats = {
            'end_customer_total_follow_up': 0,
            'end_customer_total_first_visit': 0,
            'end_customer_total_multi_visit': 0,
            'partner_total_follow_up': 0,
            'partner_total_first_visit': 0,
            'partner_total_multi_visit': 0,
            'assessment_red_count': 0,
            'assessment_yellow_count': 0,
            'assessment_green_count': 0
        }
        
        for record in sales_records:
            # 累加统计数据
            for key in total_stats.keys():
                total_stats[key] += record.get(key, 0)
        
        # 构造统计数据，总计字段为整数，平均值字段为字符串
        avg_stats = {}
        for key, value in total_stats.items():
            if key in ['end_customer_total_follow_up', 'partner_total_follow_up']:
                # 计算平均值，保留1位小数，返回字符串
                avg_value = round(value / sales_count, 1) if sales_count > 0 else 0
                avg_stats[f"{key.replace('total', 'avg')}"] = str(avg_value)
                # 总计字段保持整数类型
                avg_stats[key] = value
            else:
                # 其他字段保持整数类型
                avg_stats[key] = value
        
        # 获取报告信息
        report_info_1 = self._get_weekly_report_info(session, 'review1s', end_date, department_name)
        report_info_5 = self._get_weekly_report_info(session, 'review5', end_date, department_name)
        
        # 获取销售四象限数据
        sales_quadrants = None
        if report_info_5 and report_info_5.get('execution_id'):
            sales_quadrants = self._get_sales_quadrants_data(report_info_5['execution_id'])
        
        # 构造部门周报数据
        department_report = {
            'department_name': department_name,
            'report_start_date': start_date,
            'report_end_date': end_date,
            'statistics': [avg_stats],  # 作为数组，包含平均值
            'visit_detail_page': f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={start_date}&end_date={end_date}",
            'account_list_page': f"{settings.ACCOUNT_LIST_PAGE_URL}?department={department_name}",
            'weekly_review_1_page': self._get_weekly_report_url(report_info_1['execution_id'], 'review1s') if report_info_1 and report_info_1.get('execution_id') else f"{settings.REVIEW_REPORT_HOST}",
            'weekly_review_5_page': self._get_weekly_report_url(report_info_5['execution_id'], 'review5') if report_info_5 and report_info_5.get('execution_id') else f"{settings.REVIEW_REPORT_HOST}",
            'sales_quadrants': [sales_quadrants] if sales_quadrants else [{"behavior_hh": "--", "behavior_hl": "--", "behavior_lh": "--", "behavior_ll": "--"}]
        }
        
        logger.info(
            f"部门 {department_name} 周报汇总完成: {len(sales_records)} 个销售记录, "
            f"销售人员数量: {sales_count}, 总跟进客户数: {total_stats['end_customer_total_follow_up']}"
        )
        
        return department_report
    
    def _get_department_sales_count(self, department_name: str, session: Session = None) -> int:
        """
        获取指定部门的销售人员数量（不包含leader）
        
        Args:
            department_name: 部门名称
            session: 数据库会话（可选）
            
        Returns:
            int: 销售人员数量
        """
        try:
            from app.repositories.user_profile import UserProfileRepo
            
            # 如果没有传入session，创建一个新的
            should_close_session = False
            if session is None:
                from app.core.db import get_db_session
                session = get_db_session()
                should_close_session = True
            
            try:
                user_profile_repo = UserProfileRepo()
                
                # 获取部门所有成员
                department_members = user_profile_repo.get_department_members(session, department_name)
                
                # 过滤掉leader（没有直属上级的用户被认为是leader）
                sales_count = 0
                for member in department_members:
                    if member.is_active and member.direct_manager_id:
                        # 有直属上级的活跃用户被认为是销售人员
                        sales_count += 1
                
                logger.info(f"部门 {department_name} 的销售人员数量: {sales_count}")
                return max(sales_count, 1)  # 至少返回1，避免除零错误
                
            finally:
                # 只有当我们创建了session时才关闭它
                if should_close_session:
                    session.close()
                    
        except Exception as e:
            logger.error(f"获取部门 {department_name} 销售人员数量失败: {e}")
            return 1  # 出错时返回1，避免除零错误


    def aggregate_company_weekly_report(self, session: Session, start_date: date, end_date: date) -> Dict:
        """
        汇总公司级周报数据
        
        Args:
            session: 数据库会话
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            Dict: 公司汇总周报数据
        """
        from app.core.config import settings
        
        logger.info(f"开始汇总 {start_date} 到 {end_date} 的公司周报数据")
        
        # 获取指定日期范围的所有统计数据
        statistics_records = self.get_weekly_statistics(session, start_date, end_date)
        
        if not statistics_records:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何销售周报数据")
            return None
        
        # 获取公司所有销售人员数量（不包含leader）
        total_sales_count = self._get_company_sales_count(session)
        
        # 汇总统计数据（直接加和）
        total_stats = {
            'end_customer_total_follow_up': 0,
            'end_customer_total_first_visit': 0,
            'end_customer_total_multi_visit': 0,
            'partner_total_follow_up': 0,
            'partner_total_first_visit': 0,
            'partner_total_multi_visit': 0,
            'assessment_red_count': 0,
            'assessment_yellow_count': 0,
            'assessment_green_count': 0
        }
        
        for record in statistics_records:
            # 累加统计数据
            for key in total_stats.keys():
                total_stats[key] += record.get(key, 0)
        
        # 构造统计数据，总计字段为整数，平均值字段为字符串
        avg_stats = {}
        for key, value in total_stats.items():
            if key in ['end_customer_total_follow_up', 'partner_total_follow_up']:
                # 计算平均值，保留1位小数，返回字符串
                avg_value = round(value / total_sales_count, 1) if total_sales_count > 0 else 0
                avg_stats[f"{key.replace('total', 'avg')}"] = str(avg_value)
                # 总计字段保持整数类型
                avg_stats[key] = value
            else:
                # 其他字段保持整数类型
                avg_stats[key] = value
        
        # 获取报告信息
        report_info_1 = self._get_weekly_report_info(session, 'review1', end_date, None)
        report_info_5 = self._get_weekly_report_info(session, 'review5', end_date, None)
        
        # 获取销售四象限数据
        sales_quadrants = None
        if report_info_5 and report_info_5.get('execution_id'):
            sales_quadrants = self._get_sales_quadrants_data(report_info_5['execution_id'])
        
        # 构造公司周报数据
        company_report = {
            'report_start_date': start_date,
            'report_end_date': end_date,
            'statistics': [avg_stats],  # 作为数组，包含平均值
            'visit_detail_page': f"{settings.VISIT_DETAIL_PAGE_URL}?start_date={start_date}&end_date={end_date}",
            'account_list_page': settings.ACCOUNT_LIST_PAGE_URL,
            'weekly_review_1_page': self._get_weekly_report_url(report_info_1['execution_id'], 'review1') if report_info_1 and report_info_1.get('execution_id') else f"{settings.REVIEW_REPORT_HOST}",
            'weekly_review_5_page': self._get_weekly_report_url(report_info_5['execution_id'], 'review5') if report_info_5 and report_info_5.get('execution_id') else f"{settings.REVIEW_REPORT_HOST}",
            'sales_quadrants': [sales_quadrants] if sales_quadrants else [{"behavior_hh": "--", "behavior_hl": "--", "behavior_lh": "--", "behavior_ll": "--"}]
        }
        
        logger.info(
            f"公司周报汇总完成: {len(statistics_records)} 个销售记录, "
            f"销售人员数量: {total_sales_count}, 总跟进客户数: {total_stats['end_customer_total_follow_up']}"
        )
        
        return company_report
    
    def _get_weekly_report_url(self, execution_id: str, report_type: str) -> str:
        """
        根据execution_id和report_type构建周报报告链接
        
        Args:
            execution_id: 报告执行ID
            report_type: 报告类型，如review1s, review1, review5
            
        Returns:
            str: 报告链接
        """
        try:
            from app.core.config import settings
            
            # 根据报告类型构建不同的URL
            if report_type == 'review1s' or report_type == 'review1':
                return f"{settings.REVIEW_REPORT_HOST}/review/weeklyDetail/{execution_id}"
            elif report_type == 'review5':
                return f"{settings.REVIEW_REPORT_HOST}/review/muban5Detail/{execution_id}"
            else:
                # 未知报告类型，使用默认URL
                logger.warning(f"未知的报告类型: {report_type}，使用默认链接")
                return f"{settings.REVIEW_REPORT_HOST}"
                
        except Exception as e:
            logger.error(f"构建周报报告链接失败: {e}")
            # 出错时使用默认URL
            return f"{settings.REVIEW_REPORT_HOST}"

    def _get_weekly_report_info(self, session: Session, report_type: str, report_date: str, department_name: str = None) -> Optional[Dict]:
        """
        获取周报报告信息（包括execution_id）
        
        Args:
            session: 数据库会话
            report_type: 报告类型，如review1s,review1,review5
            report_date: 报告日期
            department_name: 部门名称，None表示公司级报告
            
        Returns:
            Dict: 包含execution_id的报告信息，如果未找到返回None
        """
        try:
            from app.utils.date_utils import get_week_of_year
            from app.repositories.crm_report_index import CRMReportIndexRepo
            
            # 计算周数和年份
            week_of_year, year = get_week_of_year(report_date)
            
            # 查询报告索引
            report_repo = CRMReportIndexRepo()
            
            if department_name:
                # 部门级报告
                report = report_repo.get_weekly_report_by_department(
                    session, report_type, week_of_year, year, department_name
                )
                logger.info(f"部门级报告: {report}")
            else:
                # 公司级报告
                report = report_repo.get_weekly_report_by_company(
                    session, report_type, week_of_year, year
                )
                logger.info(f"公司级报告: {report}")
            
            if report and report.execution_id:
                return {
                    'execution_id': report.execution_id,
                    'report_type': report.report_type,
                    'department_name': report.department_name
                }
            else:
                logger.warning(f"未找到 {report_type} 报告")
                return None
                
        except Exception as e:
            logger.error(f"获取周报报告信息失败: {e}")
            return None

    def _get_sales_quadrants_data(self, execution_id: str) -> Optional[Dict]:
        """
        获取销售四象限数据
        
        Args:
            execution_id: 报告执行ID
            
        Returns:
            Dict: 销售四象限数据，如果获取失败返回None
        """
        try:
            from app.services.sales_quadrants_service import sales_quadrants_service
            
            # 调用外部接口获取销售四象限数据
            sales_quadrants = sales_quadrants_service.get_sales_quadrants(execution_id)
            
            if sales_quadrants:
                # 处理四象限数据，将每个数组中的销售名字用 | 拼接
                processed_quadrants = {}
                for quadrant_key, sales_list in sales_quadrants.items():
                    if isinstance(sales_list, list):
                        # 过滤空字符串并用 | 连接
                        filtered_list = [name.strip() for name in sales_list if name and name.strip()]
                        processed_quadrants[quadrant_key] = " | ".join(filtered_list) if filtered_list else "--"
                    else:
                        # 如果不是列表，用--填充
                        processed_quadrants[quadrant_key] = "--"
                
                logger.info(f"销售四象限数据处理完成: {processed_quadrants}")
                return processed_quadrants
            
            return sales_quadrants
                
        except Exception as e:
            logger.error(f"获取销售四象限数据失败: {e}")
            return None

    def _get_company_sales_count(self, session: Session) -> int:
        """
        获取公司所有销售人员数量（不包含leader）
        
        Args:
            session: 数据库会话
            
        Returns:
            int: 销售人员数量
        """
        try:
            from app.repositories.user_profile import UserProfileRepo
            
            user_profile_repo = UserProfileRepo()
            
            # 获取所有活跃用户
            all_members = user_profile_repo.get_all_active_profiles(session)
            
            # 过滤掉leader（没有直属上级的用户被认为是leader）
            sales_count = 0
            for member in all_members:
                if member.direct_manager_id:
                    # 有直属上级的活跃用户被认为是销售人员
                    sales_count += 1
            
            logger.info(f"公司销售人员数量: {sales_count}")
            return max(sales_count, 1)  # 至少返回1，避免除零错误
                
        except Exception as e:
            logger.error(f"获取公司销售人员数量失败: {e}")
            return 1  # 出错时返回1，避免除零错误


# 创建服务实例
crm_statistics_service = CRMStatisticsService()
