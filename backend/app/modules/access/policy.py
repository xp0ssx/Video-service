from sqlalchemy import select

from app.modules.auth.models import Channel
from app.modules.videos.models import VideoAccess


def can_watch(db, user_id, video):
    if video.status != "ready" or video.moderation_state != "visible":
        return False
    if video.visibility == "public":
        return True
    if user_id is None:
        return False
    owner = db.scalar(select(Channel.user_id).where(Channel.id == video.channel_id))
    return user_id == owner or db.get(VideoAccess, (video.id, user_id)) is not None
