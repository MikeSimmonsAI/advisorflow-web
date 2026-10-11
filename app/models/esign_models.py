"""EvoSys e-signature: envelopes, signers, and the audit trail.

An ENVELOPE is one document sent for signature: the exact HTML that was sent
(and its SHA-256), who must sign in what order, and - once everyone has
signed - the final PDF and its SHA-256. A SIGNER holds a hashed one-time link
token, an emailed verification code (hashed), their consent and their
signature. Every step is an EVENT with time, IP and browser, which is what the
certificate page of the signed PDF prints.

Built to the federal ESIGN Act / Texas UETA elements: consent to do business
electronically, intent (an explicit sign action), the signature tied to the
exact record, and a retainable copy for every party.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text

from app.models.models import Base, gen_uuid


class EsignEnvelope(Base):
    __tablename__ = "esign_envelopes"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    deal_id = Column(String, nullable=True, index=True)
    document_id = Column(String, nullable=True, index=True)      # wholesale_documents.id
    kind = Column(String, nullable=True)                          # purchase_agreement, ...
    title = Column(String, nullable=False)
    status = Column(String, nullable=False, default="sent")      # sent|completed|declined|voided|expired
    message = Column(Text, nullable=True)
    document_html = Column(Text, nullable=False)
    document_sha256 = Column(String, nullable=False)
    consent_version = Column(String, nullable=False, default="2026-10-10")
    sender_name = Column(String, nullable=True)
    sender_email = Column(String, nullable=True)
    created_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    void_reason = Column(String, nullable=True)
    final_pdf = Column(LargeBinary, nullable=True)
    final_sha256 = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EsignSigner(Base):
    __tablename__ = "esign_signers"
    __table_args__ = (Index("ix_esign_signers_token_hash", "token_hash", unique=True),)

    id = Column(String, primary_key=True, default=gen_uuid)
    envelope_id = Column(String, ForeignKey("esign_envelopes.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String, nullable=False)
    sign_order = Column(Integer, nullable=False, default=1)
    role = Column(String, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=False)
    status = Column(String, nullable=False, default="waiting")   # waiting|sent|viewed|verified|signed|declined
    token_hash = Column(String, nullable=True)                   # sha256 of the emailed link token
    link_sent_at = Column(DateTime, nullable=True)
    links_sent = Column(Integer, nullable=False, default=0)
    viewed_at = Column(DateTime, nullable=True)
    code_hash = Column(String, nullable=True)
    code_expires_at = Column(DateTime, nullable=True)
    code_attempts = Column(Integer, nullable=False, default=0)
    codes_sent = Column(Integer, nullable=False, default=0)
    code_sent_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    consent_at = Column(DateTime, nullable=True)
    signed_at = Column(DateTime, nullable=True)
    signature_kind = Column(String, nullable=True)               # typed|drawn
    signature_text = Column(String, nullable=True)               # the typed name (also kept for drawn)
    signature_image = Column(LargeBinary, nullable=True)         # PNG, drawn signatures only
    sign_ip = Column(String, nullable=True)
    sign_user_agent = Column(String, nullable=True)
    declined_at = Column(DateTime, nullable=True)
    decline_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EsignEvent(Base):
    __tablename__ = "esign_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    envelope_id = Column(String, ForeignKey("esign_envelopes.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String, nullable=False)
    signer_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    detail = Column(Text, nullable=True)
