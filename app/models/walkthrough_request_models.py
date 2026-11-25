"""Free commercial-cleaning walkthrough requests (owner request 2026-11-22).

A facility or property manager who saw Mike's LinkedIn offer fills in the
public page /evosys-sales-public/walkthrough. Each request is one row here.
These people are NOT EvoSys Pro sales prospects (they want a cleaning quote,
not our software), so they are kept out of EvoSys BookIt opportunities and
never enter any automatic outreach. Mike matches each one to a cleaning
company by hand.
"""
from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


class WalkthroughRequest(Base):
    __tablename__ = "walkthrough_requests"
    id = Column(String, primary_key=True, default=gen_uuid)
    contact_name = Column(String(120), nullable=False)
    job_title = Column(String(120), nullable=True)
    company = Column(String(160), nullable=False)
    email = Column(String(160), nullable=False)
    phone = Column(String(25), nullable=False)
    property_address = Column(String(300), nullable=False)
    building_type = Column(String(60), nullable=True)
    square_feet = Column(String(40), nullable=True)
    current_service = Column(String(60), nullable=True)   # who cleans it today, in their words
    timing = Column(String(60), nullable=True)            # when they want service to start
    preferred_times = Column(String(300), nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(String(60), nullable=True)            # e.g. linkedin (from ?src=)
    status = Column(String(20), nullable=False, default="new")   # new / contacted / scheduled / closed
    created_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (Index("ix_walkthrough_requests_email", "email"),)
