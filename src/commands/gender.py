"""``/Gender <name>`` — gender + nationality prediction (no AI).

Combines Genderize (https://api.genderize.io/) and Nationalize
(https://api.nationalize.io/). Display rules:

* show the highest-probability gender with its probability and sample count,
* show the highest-probability nationality, and also the second one when the
  two are within 5 percentage points,
* if one provider fails, show the other and report the failure honestly.
"""

from __future__ import annotations

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.utils import format_count, format_percent
from src.lib.validation import clean_name


def handle_gender(ctx: CommandContext, args: str) -> CommandResult:
    name = clean_name(args, "/Gender <name>")
    # Genderize works best on first names; pass the first token as the
    # primary name but keep the full input visible in the reply.
    first_name = name.split()[0]

    gender, nationality = ctx.services.demographics.analyze(first_name)

    lines = [f"Prediction for {first_name!r} (statistical estimate, not a fact):", ""]

    if gender.error:
        lines.append(f"Gender: unavailable — {gender.error}")
    elif gender.gender is None:
        lines.append("Gender: unknown — too few samples for this name.")
    else:
        detail = f"{gender.gender} ({format_percent(gender.probability)} confidence"
        if gender.count is not None:
            detail += f", sample {format_count(gender.count)}"
        detail += ")"
        lines.append(f"Gender: {detail}")

    if nationality.error:
        lines.append(f"Nationality: unavailable — {nationality.error}")
    elif not nationality.countries:
        lines.append("Nationality: unknown — too few samples for this name.")
    else:
        shown = nationality.top_pair()
        parts = [
            f"{code} {format_percent(prob)}" for code, prob in shown
        ]
        lines.append("Likely nationality: " + " or ".join(parts))
        top_count_note = ""
        if len(shown) == 2:
            top_count_note = " (both within 5 points — shown together)"
        if top_count_note:
            lines[-1] += top_count_note
        others = [c for c in nationality.countries[:3] if c not in shown]
        if others:
            lines.append(
                "Also possible: "
                + ", ".join(f"{code} {format_percent(prob)}" for code, prob in others)
            )

    if gender.error and nationality.error:
        lines.append("")
        lines.append("Both providers failed — please try again later.")

    return CommandResult(text="\n".join(lines))


register(
    Command(
        name="Gender",
        handler=handle_gender,
        usage="/Gender <name>",
        description="Predict gender and likely nationality from a first name (Genderize + Nationalize).",
        examples=["/Gender Maria", "/Gender Aarav"],
        category="Fun data",
        troubleshooting="Uses optional GENDERIZE_API_KEY / NATIONALIZE_API_KEY for bigger quotas.",
    )
)
