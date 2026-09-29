"""Сборка всех роутеров в один."""
from aiogram import Router

from app.handlers import (accounts, admin, channels, common, images,
                          settings_h, stats_h, transfer_h)


def build_router() -> Router:
    root = Router()
    root.include_router(common.router)
    root.include_router(stats_h.router)
    root.include_router(accounts.router)
    root.include_router(channels.router)
    root.include_router(transfer_h.router)
    root.include_router(images.router)
    root.include_router(settings_h.router)
    root.include_router(admin.router)
    return root
