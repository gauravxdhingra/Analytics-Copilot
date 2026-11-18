"""Prints, as JSON, how many booked transactions each demo user is entitled to see.

The browser tests compare what the UI shows against this, computed from the seeded world
rather than from the system under test.
"""

import json

from halcyon_world import generate_world

WORLD = generate_world(seed=7)


def booked(*entities: str) -> int:
    return sum(
        1
        for t in WORLD.transactions
        if t.is_current and t.status == "booked" and t.entity_id in entities
    )


print(
    json.dumps(
        {
            "ana.analyst": booked("HAL-DE"),
            "ben.analyst": booked("HAL-UK"),
            "maria.manager": booked("HAL-DE", "HAL-UK"),
        }
    )
)
