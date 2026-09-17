from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User
from app.services.notification_service import (NOTIFICATION_PAGE_SIZE,
                                              get_unread_notifications,
                                              mark_notification_read,
                                              unread_notification_count)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/")
def list_notifications(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The bell's payload: a capped page of unread notifications plus the true count.

    THE SHAPE CHANGED, AND THE CLIENT ACCEPTS BOTH. This used to return a bare
    JSON array and the bell counted it with `.length`, which meant the badge's
    number was paid for by materialising every unread row - once a minute, per
    signed-in client, forever. `unread_count` is now a SQL count and `items` is
    capped at NOTIFICATION_PAGE_SIZE.

    The frontend reads either shape (see NotificationBell.jsx), so the backend
    and the frontend can deploy in any order without the bell throwing.
    """
    items = get_unread_notifications(db, current_user.id)
    total = unread_notification_count(db, current_user.id)
    return {
        "items": items,
        "unread_count": total,
        "page_size": NOTIFICATION_PAGE_SIZE,
        "has_more": total > len(items),
    }


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    success = mark_notification_read(db, notification_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"marked_read": True}
