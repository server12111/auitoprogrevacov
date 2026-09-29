"""Публикация ежедневной картинки в канал (обработчик задачи post)."""
import io
import random

from telethon.tl.types import InputPeerChannel

from app import settings
from app.db import repo
from app.logger import log
from app.services import image_gen, task_queue
from app.utils import day_number, today_str


async def _post(task: dict, client) -> None:
    ch_id = task["payload"].get("channel_id")
    channel = await repo.get_channel(ch_id)
    if channel is None or channel["status"] in ("transferred", "dead"):
        return  # канал удалён/передан/недоступен — молча пропускаем

    if channel["last_post_date"] == today_str():
        return  # уже постили сегодня

    day = day_number(channel["created_at"])
    img_bytes = await image_gen.render_day_image(day)

    caption_lines = settings.lines("post_captions")
    caption = random.choice(caption_lines) if caption_lines else ""

    peer = InputPeerChannel(channel["tg_channel_id"], channel["access_hash"])
    file = io.BytesIO(img_bytes)
    file.name = "day.jpg"

    await client.send_file(peer, file, caption=caption)
    await repo.mark_posted(ch_id, today_str())
    log.info("Канал %s: опубликован День %s", ch_id, day)


def setup() -> None:
    task_queue.register("post", _post)
