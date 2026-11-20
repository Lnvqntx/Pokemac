"""Persist daily side quest offers and choose fresh templates."""

import json
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from pathlib import Path


RESET_HOUR = 5
OFFER_COUNT = 3
MATCH_COUNT = 2
HISTORY_DAYS = 7


@lru_cache
def templates():
    return json.loads(Path(__file__).with_name("side_quests.json").read_text(encoding="utf-8"))


def quest_day(now):
    return now.date() if now.hour >= RESET_HOUR else now.date() - timedelta(days=1)


def picks(db, day):
    return [dict(row) for row in db.execute("""
        SELECT tasks.*, daily_quest_picks.pokemon_type, daily_quest_picks.template_id
        FROM daily_quest_picks JOIN tasks ON tasks.id = daily_quest_picks.task_id
        WHERE day = ? AND retired = 0 ORDER BY task_id
    """, (day.isoformat(),))]


def rerolled(db, day):
    return db.execute("SELECT 1 FROM daily_quest_picks WHERE day = ? AND retired = 1 LIMIT 1",
                      (day.isoformat(),)).fetchone() is not None


def draw(db, day, lead_types, rng, *, kept=()):
    used = {row[0] for row in db.execute("""
        SELECT template_id FROM daily_quest_picks WHERE day BETWEEN ? AND ?
    """, ((day - timedelta(days=HISTORY_DAYS)).isoformat(), day.isoformat()))}
    matches, others = [], []
    for template in templates():
        if template["id"] not in used:
            (matches if template["type"] in lead_types else others).append(template)
    available = OFFER_COUNT - len(kept)
    preferred = MATCH_COUNT - sum(quest["pokemon_type"] in lead_types for quest in kept)
    chosen = rng.sample(matches, min(available, max(0, preferred), len(matches)))
    remaining = available - len(chosen)
    # Keep the third slot varied and reserve matching templates for future rotations.
    if len(others) < remaining:
        chosen_ids = {template["id"] for template in chosen}
        others.extend(template for template in matches if template["id"] not in chosen_ids)
    for _ in range(remaining):
        # Draw neutral offers from the least-used categories so a later lead can still match.
        supply = Counter(template["type"] for template in others)
        largest = max(supply.values())
        picked, = rng.sample([template for template in others if supply[template["type"]] == largest], 1)
        chosen.append(picked)
        others.remove(picked)
    return chosen
