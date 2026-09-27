"""Food reflection message builders shared by handlers and notifications (v1.3).

Every screen is a pure function of the stored ``FoodDay`` (plus the user's
rule set), so a scheduled message, a /food tap and a re-render after a toggle
all show exactly the same thing — there is no FSM behind the food flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aiogram.types import InlineKeyboardMarkup

from app.bot import keyboards, texts
from app.database.models import FoodDay, User
from app.services import food_service


@dataclass(frozen=True)
class Screen:
    text: str
    markup: InlineKeyboardMarkup | None = None


def focus_prompt(user: User, target_date: date) -> Screen:
    return Screen(
        texts.FOOD_MORNING_PROMPT,
        keyboards.food_focus_keyboard(food_service.active_rules(user), target_date),
    )


def checklist(
    user: User, day: FoodDay | None, target_date: date, today: date, *, stale: bool = False
) -> Screen:
    focus = day.focus_rule if day is not None else None
    return Screen(
        texts.food_checklist_message(target_date, today, focus, stale=stale),
        keyboards.food_checklist_keyboard(
            food_service.ordered_rules(user, focus),
            food_service.violations(day),
            focus,
            target_date,
        ),
    )


def summary(day: FoodDay, target_date: date, today: date, *, streak: int | None = None) -> Screen:
    """Result of a submitted day; ``rules`` come from the stored results so a
    later change of the rule set never rewrites how that day is shown."""
    return Screen(
        texts.food_summary_message(
            target_date,
            today,
            rules=[r.rule_code for r in day.results],
            broken=food_service.violations(day),
            focus=day.focus_rule,
            triggers=food_service.triggers_of(day),
            streak=streak,
        )
    )


def triggers(day: FoodDay, target_date: date, today: date) -> Screen:
    base = summary(day, target_date, today)
    return Screen(
        f"{base.text}\n\n{texts.FOOD_TRIGGERS_QUESTION}",
        keyboards.food_triggers_keyboard(food_service.triggers_of(day), target_date),
    )
