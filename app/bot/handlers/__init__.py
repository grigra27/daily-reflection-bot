"""Assemble the dispatcher router tree.

Routers with the authorization filter are registered first; the unauthorized
catch-all router is registered last so unknown users only ever see the private
notice.
"""

from __future__ import annotations

from aiogram import Router

from app.bot.handlers import daily, export, settings, start, stats, weekly


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(daily.router)
    root.include_router(stats.router)
    root.include_router(export.router)
    root.include_router(settings.router)
    root.include_router(weekly.router)
    # Last: only matches users who failed the allow-list filter above.
    root.include_router(start.unauthorized_router)
    return root
