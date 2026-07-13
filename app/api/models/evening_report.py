from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, JSON, Text, UniqueConstraint

from app.db.database import Base


class EveningReportRecord(Base):
    __tablename__ = "evening_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(Date, nullable=False)
    notes_text = Column(Text, nullable=True)
    morning_cash = Column(Float, nullable=True)
    day_cash = Column(Float, nullable=True)
    bn = Column(Float, nullable=True)
    new_advance = Column(Float, nullable=True)
    old_advance = Column(Float, nullable=True)
    surrendered = Column(Float, nullable=True)
    buybacks = Column(Float, nullable=True)
    wholesale = Column(Float, nullable=True)
    credit = Column(Float, nullable=True)
    nf_primary = Column(Float, nullable=True)
    nf_secondary = Column(Float, nullable=True)
    extra_items = Column(JSON, nullable=False, default=list)
    final_cash = Column(Float, nullable=True)
    report_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("report_date", name="uq_evening_reports_report_date"),)
