from datetime import datetime

from sqlmodel import Session, and_, func, or_, select

from app.api.routes.crm.models import (
    DailyCustomerFollowupItemOut,
    DailyCustomerFollowupQueryRequest,
    DailyCustomerFollowupQueryResponse,
)
from app.models.crm_account_opportunity_assessment import CRMAccountOpportunityAssessment
from app.models.crm_accounts import CRMAccount
from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_user import CRMUser
from app.repositories.base_repo import BaseRepo


class CRMAccountOpportunityAssessmentRepo(BaseRepo):
    model_cls = CRMAccountOpportunityAssessment

    def query_daily_customer_followups(
        self,
        session: Session,
        request: DailyCustomerFollowupQueryRequest,
    ) -> DailyCustomerFollowupQueryResponse:
        page = max(int(request.page or 1), 1)
        page_size = max(int(request.page_size or 20), 1)
        page_size = min(page_size, 100)

        query = (
            select(
                CRMAccountOpportunityAssessment,
                CRMOpportunity.owner,
                CRMOpportunity.owner_id,
                CRMOpportunity.owner_department_name,
                CRMOpportunity.owner_department_id,
                CRMAccount.person_in_charge,
                CRMAccount.person_in_charge_id,
                CRMUser.department,
                CRMUser.department_id,
            )
            .outerjoin(
                CRMOpportunity,
                CRMOpportunity.unique_id == CRMAccountOpportunityAssessment.opportunity_id,
            )
            .outerjoin(
                CRMAccount,
                CRMAccount.unique_id == CRMAccountOpportunityAssessment.account_id,
            )
            .outerjoin(
                CRMUser,
                or_(
                    and_(
                        CRMAccountOpportunityAssessment.opportunity_id.isnot(None),
                        CRMUser.unique_id == CRMOpportunity.owner_id,
                    ),
                    and_(
                        CRMAccountOpportunityAssessment.opportunity_id.is_(None),
                        CRMUser.unique_id == CRMAccount.person_in_charge_id,
                    ),
                ),
            )
        )

        if request.customer_level:
            query = query.where(CRMAccountOpportunityAssessment.account_level.in_(request.customer_level))
        if request.account_name and request.partner_name:
            query = query.where(
                or_(
                    and_(
                        func.lower(CRMAccountOpportunityAssessment.customer_type) == "partner",
                        CRMAccountOpportunityAssessment.account_name.in_(request.partner_name),
                    ),
                    and_(
                        or_(
                            CRMAccountOpportunityAssessment.customer_type.is_(None),
                            func.lower(CRMAccountOpportunityAssessment.customer_type) != "partner",
                        ),
                        CRMAccountOpportunityAssessment.account_name.in_(request.account_name),
                    ),
                )
            )
        elif request.account_name:
            query = query.where(
                and_(
                    or_(
                        CRMAccountOpportunityAssessment.customer_type.is_(None),
                        func.lower(CRMAccountOpportunityAssessment.customer_type) != "partner",
                    ),
                    CRMAccountOpportunityAssessment.account_name.in_(request.account_name),
                )
            )
        if request.opportunity_name:
            query = query.where(CRMAccountOpportunityAssessment.opportunity_name.in_(request.opportunity_name))
        if request.assessment_flag:
            normalized_flags = [str(v).lower() for v in request.assessment_flag if str(v).strip()]
            if normalized_flags:
                query = query.where(func.lower(CRMAccountOpportunityAssessment.assessment_flag).in_(normalized_flags))
        if request.is_first_visit is not None:
            query = query.where(CRMAccountOpportunityAssessment.is_first_visit == request.is_first_visit)
        if request.assessment_date_start:
            try:
                start_date = datetime.strptime(request.assessment_date_start, "%Y-%m-%d").date()
                query = query.where(CRMAccountOpportunityAssessment.assessment_date >= start_date)
            except ValueError:
                pass
        if request.assessment_date_end:
            try:
                end_date = datetime.strptime(request.assessment_date_end, "%Y-%m-%d").date()
                query = query.where(CRMAccountOpportunityAssessment.assessment_date <= end_date)
            except ValueError:
                pass
        if request.partner_name and not request.account_name:
            query = query.where(
                and_(
                    func.lower(CRMAccountOpportunityAssessment.customer_type) == "partner",
                    CRMAccountOpportunityAssessment.account_name.in_(request.partner_name),
                )
            )
        if request.owner:
            query = query.where(
                or_(
                    and_(
                        CRMAccountOpportunityAssessment.opportunity_id.isnot(None),
                        CRMOpportunity.owner.in_(request.owner),
                    ),
                    and_(
                        CRMAccountOpportunityAssessment.opportunity_id.is_(None),
                        CRMAccount.person_in_charge.in_(request.owner),
                    ),
                )
            )
        if request.department:
            query = query.where(
                or_(
                    and_(
                        CRMAccountOpportunityAssessment.opportunity_id.isnot(None),
                        func.coalesce(CRMOpportunity.owner_department_name, CRMUser.department).in_(request.department),
                    ),
                    and_(
                        CRMAccountOpportunityAssessment.opportunity_id.is_(None),
                        CRMUser.department.in_(request.department),
                    ),
                )
            )
        # TODO: is_call_high 后续会在 CRMAccountOpportunityAssessment 增加字段；
        # 当前先不依赖 CRMSalesVisitRecord，待字段落表后直接从 assessment 读取并支持筛选。

        total = session.exec(select(func.count()).select_from(query.subquery())).one()
        total = int(total or 0)

        sort_fields = {
            "assessment_date": CRMAccountOpportunityAssessment.assessment_date,
            "customer_level": CRMAccountOpportunityAssessment.account_level,
            "account_name": CRMAccountOpportunityAssessment.account_name,
            "opportunity_name": CRMAccountOpportunityAssessment.opportunity_name,
            "assessment_flag": CRMAccountOpportunityAssessment.assessment_flag,
        }
        sort_field = sort_fields.get(request.sort_by, CRMAccountOpportunityAssessment.assessment_date)
        if (request.sort_direction or "").lower() == "asc":
            query = query.order_by(sort_field.asc(), CRMAccountOpportunityAssessment.id.asc())
        else:
            query = query.order_by(sort_field.desc(), CRMAccountOpportunityAssessment.id.desc())

        rows = session.exec(query.offset((page - 1) * page_size).limit(page_size)).all()

        items: list[DailyCustomerFollowupItemOut] = []
        for (
            assessment,
            opp_owner,
            _opp_owner_id,
            opp_owner_dept,
            _opp_owner_dept_id,
            account_owner,
            _account_owner_id,
            owner_department,
            _owner_department_id,
        ) in rows:
            customer_type = str(assessment.customer_type or "").lower()
            partner_name = assessment.account_name if customer_type == "partner" else None

            has_opportunity = bool(assessment.opportunity_id)
            recorder = opp_owner if has_opportunity else account_owner
            department = (opp_owner_dept or owner_department) if has_opportunity else owner_department

            items.append(
                DailyCustomerFollowupItemOut(
                    customer_level=assessment.account_level,
                    account_name=assessment.account_name if customer_type != "partner" else None,
                    partner_name=partner_name,
                    opportunity_name=assessment.opportunity_name,
                    is_first_visit=assessment.is_first_visit,
                    is_call_high=None,
                    assessment_date=str(assessment.assessment_date) if assessment.assessment_date else None,
                    recorder=recorder,
                    department=department,
                    assessment_flag=assessment.assessment_flag,
                )
            )

        pages = (total + page_size - 1) // page_size if total > 0 else 0
        return DailyCustomerFollowupQueryResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )


crm_account_opportunity_assessment_repo = CRMAccountOpportunityAssessmentRepo()
