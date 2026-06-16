from typing import Any, List, Optional

from sqlmodel import Session, select

from app.models.crm_sales_visit_records_revisions import CRMSalesVisitRecordRevision


class VisitRecordRevisionsRepo:
    def create(
        self,
        session: Session,
        *,
        record_id: str,
        revision_seq: int,
        revised_by_id: str,
        revised_by_name: Optional[str],
        changes: List[dict[str, Any]],
        aldebaran_message_type: str,
        aldebaran_dedupe_key: str,
        card_push_status: Optional[str] = None,
    ) -> CRMSalesVisitRecordRevision:
        row = CRMSalesVisitRecordRevision(
            record_id=record_id,
            revision_seq=revision_seq,
            revised_by_id=revised_by_id,
            revised_by_name=revised_by_name,
            changes=changes,
            aldebaran_message_type=aldebaran_message_type,
            aldebaran_dedupe_key=aldebaran_dedupe_key,
            card_push_status=card_push_status,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return row

    def list_by_record_id(self, session: Session, record_id: str) -> List[CRMSalesVisitRecordRevision]:
        stmt = (
            select(CRMSalesVisitRecordRevision)
            .where(CRMSalesVisitRecordRevision.record_id == record_id)
            .order_by(CRMSalesVisitRecordRevision.revision_seq.asc())
        )
        return list(session.exec(stmt).all())

    def get_by_record_and_seq(
        self,
        session: Session,
        record_id: str,
        revision_seq: int,
    ) -> Optional[CRMSalesVisitRecordRevision]:
        return session.exec(
            select(CRMSalesVisitRecordRevision).where(
                CRMSalesVisitRecordRevision.record_id == record_id,
                CRMSalesVisitRecordRevision.revision_seq == revision_seq,
            )
        ).first()

    def update_card_push_status(
        self,
        session: Session,
        *,
        record_id: str,
        revision_seq: int,
        card_push_status: str,
        commit: bool = False,
    ) -> bool:
        row = self.get_by_record_and_seq(session, record_id, revision_seq)
        if not row:
            return False
        row.card_push_status = card_push_status
        session.add(row)
        if commit:
            session.commit()
            session.refresh(row)
        return True


visit_record_revisions_repo = VisitRecordRevisionsRepo()
