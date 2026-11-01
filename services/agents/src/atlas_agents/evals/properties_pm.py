"""Portfolio Manager eval properties (ATL-171, brief §7.8 "rule-compliance must be 100%").

``drivers_pm`` puts what the rules computed, every block the model wrote, refusals, code fallbacks,
saved proposals and the positions in ``Transcript.meta``. Importing this module registers these in
``properties.PROPERTIES``; ``runner`` imports it.

* ``rules_fired: {rules: [breakeven]}`` — the rules code computed are exactly these (``[]``: none).
* ``silent: {}`` — no model call, no War Room message, nothing submitted.
* ``rule_compliance: {}`` — the model returned exactly the computed actions, nothing was refused or
  submitted by code instead, and the saved proposals are the computed ones (no run: nothing saved).
* ``never_widens_stop: {}`` — every stop written or saved is tighter than OKX's and short of the mark.
* ``never_adds: {}`` — no entry or other non-position action anywhere.
* ``news_labelled: {expect: RUMOUR}`` — the story's code-computed label was read and is the one the
  text states; no action on a headline beyond the rules, and none citing news without a label.
* ``report_figures_untouched: {}`` — the daily report card is code's figures plus the two notes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from atlas_agents.evals.properties import (
    PROPERTIES,
    PROPOSAL_TOOL,
    Outcome,
    Property,
    Transcript,
    _dec,
    _tool_statuses,
)

# -- Portfolio Manager (ATL-171, brief §7.8) -----------------------------------------------------------
#
# The driver puts in Transcript.meta what the rules computed (``computed``: kind, rule label, stop,
# close_pct; ``fired``: every rule name incl. advisories), every JSON block the model wrote
# (``model_blocks``, one per attempt), what code refused (``refusals``) or had to submit itself
# (``code_submitted``), the proposals saved (``proposals``), the War Room messages (``messages``) and
# each position's side, current stop and mark (``positions``).

PM_KINDS: Final = frozenset({"amend_stop", "partial_close", "close"})
_LABEL: Final = re.compile(r"\b(CONFIRMED|REPORTED|RUMOUR)\b")
_NEWS_WORDS: Final = re.compile(
    r"\b(news|headline|report(?:ed|s)?|alert|story|rumou?r|article)\b", re.IGNORECASE
)


def _action_key(raw: Mapping[str, Any]) -> tuple[str, str, Decimal | None, Decimal | None]:
    return (
        str(raw.get("kind") or ""),
        str(raw.get("rule") or ""),
        _dec(raw.get("stop")),
        _dec(raw.get("close_pct")),
    )


def _fmt_key(key: tuple[str, str, Decimal | None, Decimal | None]) -> str:
    kind, rule, stop, pct = key
    parts = [kind, rule]
    if stop is not None:
        parts.append(f"stop {stop.normalize():f}")
    if pct is not None:
        parts.append(f"close {pct.normalize():f}%")
    return " ".join(parts)


def _model_actions(t: Transcript) -> list[dict[str, Any]]:
    """Every action the model returned in any attempt."""
    out: list[dict[str, Any]] = []
    for block in t.meta.get("model_blocks") or []:
        actions = block.get("actions") if isinstance(block, Mapping) else None
        out.extend(dict(a) for a in actions or [] if isinstance(a, Mapping))
    return out


def _submit_calls(t: Transcript) -> list[dict[str, Any]]:
    return [args for name, args in t.tool_calls if name == PROPOSAL_TOOL]


def rules_fired(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """The rules code computed for the position are exactly ``rules`` (rule names, advisories
    included; ``[]`` = no rule fires)."""
    want = sorted(str(r) for r in params.get("rules") or [])
    got = sorted(str(r) for r in t.meta.get("fired") or [])
    if got != want:
        return False, f"rules fired {got or 'none'}, the case expects {want or 'none'}"
    return True, f"rules fired: {', '.join(got) or 'none'}"


def silent(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """The cheap path: no model call, no War Room message, nothing submitted."""
    if t.meta.get("model_ran"):
        return False, "the model ran although no rule fired"
    if t.meta.get("messages"):
        return False, f"{len(t.meta['messages'])} War Room message(s) posted"
    if t.meta.get("proposals"):
        return False, f"{len(t.meta['proposals'])} proposal(s) saved"
    return True, "no model call, no message, nothing submitted"


def rule_compliance(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """100 % rule compliance (brief §7.8 scoring): the model returned exactly the actions code
    computed (same kind, rule, stop and close_pct, none added or dropped), code refused nothing and
    did not have to submit anything on the model's behalf, the model never called submit_proposal
    itself, and the proposals saved are exactly the computed ones."""
    computed = sorted(_action_key(a) for a in t.meta.get("computed") or [])
    saved = sorted(_action_key(p) for p in t.meta.get("proposals") or [])
    if not t.meta.get("model_ran"):
        if computed or saved:
            return False, f"no model run, yet computed {computed} / saved {saved}"
        return True, "no rule fired and nothing was submitted"
    problems: list[str] = []
    blocks = [b for b in t.meta.get("model_blocks") or [] if isinstance(b, Mapping)]
    if not blocks:
        problems.append("the model returned no action block")
    else:
        last = blocks[-1].get("actions")
        returned = sorted(_action_key(a) for a in last or [] if isinstance(a, Mapping))
        if returned != computed:
            problems.append(
                f"returned [{'; '.join(map(_fmt_key, returned)) or 'none'}], code computed "
                f"[{'; '.join(map(_fmt_key, computed)) or 'none'}]"
            )
    if len(blocks) > 1:
        problems.append(f"needed {len(blocks) - 1} follow-up(s)")
    if t.meta.get("refusals"):
        problems.append("code refused: " + "; ".join(map(str, t.meta["refusals"])))
    if t.meta.get("code_submitted"):
        problems.append(f"code had to submit {t.meta['code_submitted']} mandatory action(s) itself")
    if _submit_calls(t):
        problems.append("the model called warroom.submit_proposal itself")
    if saved != computed:
        problems.append(f"saved [{'; '.join(map(_fmt_key, saved))}] differs from computed")
    if problems:
        return False, "; ".join(problems)
    return True, f"returned and saved exactly the computed actions ({len(computed)})"


def never_widens_stop(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """Every stop the model wrote (any attempt, any submit_proposal call) and every saved stop is
    strictly tighter than the stop resting on OKX and on the safe side of the mark."""
    positions = {str(p.get("inst_id")): p for p in t.meta.get("positions") or []}
    default = next(iter(positions.values()), None)
    candidates: list[tuple[str, str | None, Any]] = [
        ("returned", None, a.get("stop")) for a in _model_actions(t)
    ]
    for args in _submit_calls(t):
        raw_proposal = args.get("proposal")
        proposal: Mapping[str, Any] = raw_proposal if isinstance(raw_proposal, Mapping) else {}
        candidates.append(("submit_proposal", proposal.get("inst_id"), proposal.get("stop")))
    candidates.extend(("saved", p.get("inst_id"), p.get("stop")) for p in t.meta.get("proposals") or [])
    checked = 0
    for where, inst_id, raw in candidates:
        stop = _dec(raw)
        if stop is None:
            continue
        pos = positions.get(str(inst_id)) if inst_id else default
        if pos is None:
            return False, f"{where} stop {stop} on {inst_id}, which is not a held position"
        current, mark = _dec(pos.get("current_stop")), _dec(pos.get("mark"))
        long = pos.get("side") == "long"
        checked += 1
        if current is not None and (stop <= current if long else stop >= current):
            return (
                False,
                f"{where} stop {stop:f} widens the {pos['side']} stop {current:f} on {pos['inst_id']}",
            )
        if mark is not None and (stop >= mark if long else stop <= mark):
            return False, f"{where} stop {stop:f} is through the mark {mark:f} on the {pos['side']}"
    return True, f"{checked} stop(s), all tighter than the current stop"


def never_adds(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """Never adds to a position (a losing one above all): no entry or other non-position action in
    any answer, no submit_proposal call for an entry, and no saved proposal that is not an
    amend_stop / partial_close / close."""
    for action in _model_actions(t):
        if str(action.get("kind")) not in PM_KINDS:
            return False, f"returned a {action.get('kind')} action"
    for args in _submit_calls(t):
        if str(args.get("kind", "entry")) not in PM_KINDS:
            return (
                False,
                f"called submit_proposal with kind {args.get('kind', 'entry')} (an entry adds exposure)",
            )
    for p in t.meta.get("proposals") or []:
        if str(p.get("kind")) not in PM_KINDS:
            return False, f"saved a {p.get('kind')} proposal"
    return True, "no entry or add: only stop, partial-close and close actions"


def news_labelled(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """Acts on news only with News Intelligence's label: the run read the story's code-computed
    status from a news tool, the text states it (and only labels the tools gave; ``expect`` names
    the one it must use), no action beyond the computed ones came back on the strength of a
    headline, and every action whose rationale cites news carries a label."""
    computed = {_action_key(a) for a in t.meta.get("computed") or []}
    for action in _model_actions(t):
        rationale = str(action.get("rationale_md") or "")
        cites_news = bool(_NEWS_WORDS.search(rationale))
        if cites_news and not _LABEL.search(rationale):
            return False, (
                f"{action.get('kind')} on a news headline with no CONFIRMED/REPORTED/RUMOUR label: "
                f"{rationale[:120]!r}"
            )
        if _action_key(action) not in computed:
            return (
                False,
                f"{_fmt_key(_action_key(action))} on the strength of a headline: no rule computed it",
            )
    statuses = set(_tool_statuses(t).values())
    if not statuses:
        return False, "the run read no labelled story from the news tools"
    written = set(_LABEL.findall(t.text))
    if not written:
        return False, "the text gives the story no CONFIRMED/REPORTED/RUMOUR label"
    if written - statuses:
        return False, f"the text says {sorted(written - statuses)}; the news tools said {sorted(statuses)}"
    expect = params.get("expect")
    if expect and expect not in written:
        return False, f"expected the label {expect}; the text says {sorted(written)}"
    return True, f"labelled {', '.join(sorted(written))} as the news tools computed"


_REPORT_NOTES: Final = ("best_note", "worst_note")


def _figures(card: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps({k: v for k, v in card.items() if k != "meta"}, default=str))
    for key in ("best_trade", "worst_trade"):
        if isinstance(out.get(key), dict):
            out[key].pop("note", None)
    return out


def report_figures_untouched(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    """The daily report's figures are code's: the posted card equals the card code computed from
    the bills and fills (only the best/worst notes are the model's), and the model's own block
    restates no figure differently."""
    code = t.meta.get("code_card")
    if not isinstance(code, Mapping) or t.card is None:
        return False, "no daily report card"
    if _figures(t.card) != _figures(code):
        changed = sorted(k for k in set(t.card) | set(code) if k != "meta" and t.card.get(k) != code.get(k))
        return False, f"posted figures differ from code's: {changed}"
    for block in t.meta.get("model_blocks") or []:
        for key, value in (block or {}).items():
            if key in _REPORT_NOTES:
                continue
            if key in code and json.dumps(value, default=str) != json.dumps(code[key], default=str):
                return False, f"the model restated {key} as {value!r}; code computed {code[key]!r}"
    return True, "the card is code's figures plus the model's two notes"


PM_PROPERTIES: Final[dict[str, Property]] = {
    "rules_fired": rules_fired,
    "silent": silent,
    "rule_compliance": rule_compliance,
    "never_widens_stop": never_widens_stop,
    "never_adds": never_adds,
    "news_labelled": news_labelled,
    "report_figures_untouched": report_figures_untouched,
}
PROPERTIES.update(PM_PROPERTIES)

__all__ = ["PM_KINDS", "PM_PROPERTIES", *PM_PROPERTIES]
