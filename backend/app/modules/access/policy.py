from sqlalchemy import and_, exists, or_, select

from app.modules.auth.models import Channel
from app.modules.videos.models import Video, VideoAccess


def public_listing():
    """SQL public-only branch of the video policy, shared by feed and search."""
    return and_(
        Video.status == "ready", Video.moderation_state == "visible", Video.visibility == "public"
    )


def can_watch(db, user_id, video):
    if video.status != "ready" or video.moderation_state != "visible":
        return False
    if video.visibility == "public":
        return True
    if user_id is None:
        return False
    owner = db.scalar(select(Channel.user_id).where(Channel.id == video.channel_id))
    return user_id == owner or db.get(VideoAccess, (video.id, user_id)) is not None


def watch_listing(user_id):
    """SQL equivalent of can_watch, filtering before pagination/counting."""
    return and_(
        Video.status == "ready",
        Video.moderation_state == "visible",
        or_(
            Video.visibility == "public",
            exists(
                select(Channel.id).where(Channel.id == Video.channel_id, Channel.user_id == user_id)
            ),
            exists(
                select(VideoAccess.video_id).where(
                    VideoAccess.video_id == Video.id, VideoAccess.user_id == user_id
                )
            ),
        ),
    )
