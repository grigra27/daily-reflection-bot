"""Exact diary evidence and deterministic score contrasts for weekly reviews."""

from __future__ import annotations

import re
from datetime import date
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceQuote(StrictModel):
    source_id: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=220)


class DraftEvidence(StrictModel):
    text: str = Field(min_length=1, max_length=500, pattern=r"^[^\d]*$")
    sources: list[SourceQuote] = Field(min_length=1, max_length=4)


class DraftExperiment(DraftEvidence):
    check: str = Field(min_length=1, max_length=250, pattern=r"^[^\d]*$")


class DraftReview(StrictModel):
    observations: list[DraftEvidence] = Field(min_length=1, max_length=2)
    experiment: DraftExperiment
    question: DraftEvidence


class Citation(SourceQuote):
    date: date
    label: str


class Evidence(DraftEvidence):
    sources: list[Citation] = Field(min_length=1, max_length=4)

    @property
    def dates(self) -> list[date]:
        return sorted({s.date for s in self.sources if not s.source_id.startswith("week:")})


class Experiment(Evidence):
    check: str = Field(min_length=1, max_length=250)


class Review(StrictModel):
    observations: list[Evidence] = Field(min_length=1, max_length=2)
    experiment: Experiment
    question: Evidence


Issue = Literal[
    "unsupported_claim",
    "wrong_chronology",
    "retelling",
    "forced_choice",
    "generic_experiment",
    "leading_measurement",
    "unsupported_causality",
    "overgeneralization",
]


class Verdict(StrictModel):
    approved: bool
    issues: list[Issue] = Field(max_length=8)


def sources_for(payload: dict) -> dict[str, dict]:
    """IDs identify fields, not free-form events. Dates come only from the diary."""
    sources = {}
    outcomes = {
        "done": "выполнено",
        "partial": "частично",
        "not_done": "не выполнено",
        None: "итог не отмечен",
    }
    for d in payload["days"]:
        prefix = d["date"]
        if d.get("scores"):
            s = d["scores"]
            sources[f"{prefix}:scores"] = {
                "date": prefix,
                "label": "Оценки",
                "text": f"День: {s['day']}/5; настроение: {s['mood']}/5; энергия: {s['energy']}/5.",
            }
        if d.get("note"):
            sources[f"{prefix}:note"] = {"date": prefix, "label": "Заметка", "text": d["note"]}
        for i, intent in enumerate(d.get("intentions", [])):
            sources[f"{prefix}:intent{i}"] = {
                "date": prefix,
                "label": f"Намерение — {outcomes[intent['outcome']]}",
                "text": intent["text"],
            }
    for key, value in (payload.get("weekly_reflection") or {}).items():
        if value:
            sources[f"week:{key}"] = {
                "date": payload["week_start"],
                "label": "Итог недели (дата события не указана)",
                "text": value,
            }
    return sources


def contrasts_for(payload: dict) -> list[dict]:
    """Offer measured differences, never psychological/causal interpretations."""
    days = sorted((d for d in payload["days"] if d.get("scores")), key=lambda d: d["date"])
    contrasts = []
    for d in days:
        scores = d["scores"]
        if scores["day"] - scores["energy"] >= 2:
            contrasts.append(
                {
                    "kind": "day_energy_gap",
                    "sources": [f"{d['date']}:scores"],
                    "day_score": scores["day"],
                    "energy_score": scores["energy"],
                }
            )
    for prev, cur in pairwise(days):
        if (date.fromisoformat(cur["date"]) - date.fromisoformat(prev["date"])).days != 1:
            continue
        for key in ("mood", "energy"):
            if abs(cur["scores"][key] - prev["scores"][key]) >= 2:
                contrasts.append(
                    {
                        "kind": "adjacent_score_change",
                        "metric": key,
                        "sources": [f"{prev['date']}:scores", f"{cur['date']}:scores"],
                        "before": prev["scores"][key],
                        "after": cur["scores"][key],
                    }
                )
    return contrasts


def model_input(payload: dict) -> dict:
    return {
        "week_start": payload["week_start"],
        "through": payload["through"],
        "sources": sources_for(payload),
        "measured_contrasts": contrasts_for(payload),
    }


# Dates/numbers belong in exact source quotes, not regenerated factual prose.
_CALENDAR = re.compile(
    r"\d|\b(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяею]|июн\w*|июл\w*|август\w*|"
    r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|понедельник\w*|вторник\w*|среду|"
    r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|вчера|сегодня|завтра|накануне)\b",
    re.IGNORECASE,
)


def _normal(value: str) -> str:
    return " ".join(value.split())


def resolve_draft(raw: str, payload: dict) -> Review:
    draft = DraftReview.model_validate_json(raw)
    catalog = sources_for(payload)
    items = [(f"observations[{i}]", item) for i, item in enumerate(draft.observations)]
    items += [("experiment", draft.experiment), ("question", draft.question)]
    for name, item in items:
        if _CALENDAR.search(item.text):
            raise ValueError(f"{name}.text: put dates and scores only in exact source quotes")
        if len({s.source_id for s in item.sources}) != len(item.sources):
            raise ValueError("duplicate source")
    if _CALENDAR.search(draft.experiment.check):
        raise ValueError("experiment.check: put dates and scores only in exact source quotes")
    if re.search(r"\bили\b|что.{0,30}важнее|выбирай|выбери", draft.question.text, re.IGNORECASE):
        raise ValueError("question must not force a choice")
    if draft.question.text.count("?") != 1:
        raise ValueError("expected one open question")

    def resolve(item: DraftEvidence, cls):
        refs = []
        for ref in item.sources:
            source = catalog.get(ref.source_id)
            if source is None:
                raise ValueError("unknown source")
            quote = _normal(ref.quote)
            original = _normal(source["text"])
            if len(quote) < min(12, len(original)) or quote not in original:
                raise ValueError("quote must be an exact fragment of its source")
            refs.append(
                Citation(
                    source_id=ref.source_id,
                    quote=quote,
                    date=date.fromisoformat(source["date"]),
                    label=source["label"],
                )
            )
        fields = {"text": item.text, "sources": refs}
        if isinstance(item, DraftExperiment):
            fields["check"] = item.check
        return cls(**fields)

    return Review(
        observations=[resolve(item, Evidence) for item in draft.observations],
        experiment=resolve(draft.experiment, Experiment),
        question=resolve(draft.question, Evidence),
    )


def validate_cached(raw: str, payload: dict) -> Review:
    """Revalidate all source bindings; old v1/date-only cache cannot pass."""
    stored = Review.model_validate_json(raw)
    value = stored.model_dump(mode="json")
    for item in [*value["observations"], value["experiment"], value["question"]]:
        item["sources"] = [
            {"source_id": ref["source_id"], "quote": ref["quote"]} for ref in item["sources"]
        ]
    verified = resolve_draft(DraftReview.model_validate(value).model_dump_json(), payload)
    if verified != stored:
        raise ValueError("cached source context differs from diary")
    return verified
