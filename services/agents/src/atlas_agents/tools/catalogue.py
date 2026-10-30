"""Every tool name in the brief's catalogue (§6.2, plus the Orchestrator's set from §7.9), each
registered with its input model and a stub that answers ``not_implemented``.

The stubs exist so the §7.10 access matrix (atlas_agents.access) can be built and tested against the
whole catalogue now. A later task replaces a stub by registering the real tool under the same
server and name with ``@atlas_tool`` (in its own module, imported from ``atlas_agents.tools``); the
input model here is the contract it should keep or deliberately change.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas_agents.tools.framework import (
    NoInput,
    ToolContext,
    ToolDef,
    ToolError,
    ToolInput,
    ToolResult,
    register,
)

Bar = Literal["1m", "5m", "15m", "1H", "4H", "1D"]
InstType = Literal["SPOT", "SWAP", "FUTURES", "OPTION"]
NOT_IMPLEMENTED: Final = "not_implemented"


class StubOutput(BaseModel):
    """Placeholder output model; real tools declare their own."""

    model_config = ConfigDict(extra="allow")


class InstIdInput(ToolInput):
    inst_id: str = Field(description="OKX instId, e.g. BTC-USDT-SWAP")


# -- okx_market -------------------------------------------------------------------------------------


class ListInstrumentsInput(ToolInput):
    inst_type: InstType | None = None
    asset_class: Literal["crypto", "stock", "etf", "index", "commodity", "other"] | None = None
    state: str | None = Field(default="live")
    min_24h_volume_usd: float | None = Field(default=None, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class GetTickersInput(ToolInput):
    inst_type: InstType


class GetCandlesInput(InstIdInput):
    bar: Bar
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=300, ge=1, le=1000)


class GetOrderbookInput(InstIdInput):
    depth: int = Field(default=50, ge=1, le=400)


class HistoryDaysInput(InstIdInput):
    history_days: int = Field(default=7, ge=0, le=365)


class CcyPeriodInput(ToolInput):
    ccy: str = Field(description="Currency, e.g. BTC")
    period: Literal["5m", "1H", "1D"] = "1H"


# -- atlas_data ----------------------------------------------------------------------------------


class GetWatchlistInput(ToolInput):
    date: Date | None = Field(default=None, description="Asia/Baghdad date; default today")


class GetCorrelationsInput(ToolInput):
    inst_ids: list[str] = Field(min_length=2, max_length=50)
    lookback_days: int = Field(default=30, ge=5, le=365)


# -- news -------------------------------------------------------------------------------------------


class SearchNewsInput(ToolInput):
    query: str | None = None
    inst_ids: list[str] = Field(default_factory=list, max_length=50)
    since: datetime | None = None
    min_importance: int = Field(default=1, ge=1, le=5)
    limit: int = Field(default=20, ge=1, le=200)


class GetStoryClusterInput(ToolInput):
    cluster_id: str


class CalendarInput(ToolInput):
    from_: datetime = Field(alias="from")
    to: datetime
    min_importance: int = Field(default=1, ge=1, le=5)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EarningsCalendarInput(ToolInput):
    from_: Date = Field(alias="from")
    to: Date
    symbols: list[str] = Field(default_factory=list, max_length=100)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SinceInput(ToolInput):
    since: datetime | None = None


class FetchArticleInput(ToolInput):
    url: str = Field(max_length=2048)


class WebSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)


# -- history -----------------------------------------------------------------------------------------


class HistorySeriesInput(InstIdInput):
    """One candle series ending at as_of. Omitted bar/lookback come from agents.history.<tool>."""

    bar: Bar | None = None
    lookback: int | None = Field(default=None, ge=2, le=20000, description="bars")


class StatsInput(HistorySeriesInput):
    pass


class FindAnalogsInput(InstIdInput):
    # ATL-87: explicit fields replace ATL-84's free-form pattern_spec.
    bar: Bar | None = None
    window: int | None = Field(default=None, ge=2, le=500, description="bars in the pattern window")
    k: int | None = Field(default=None, ge=1, le=100)
    horizons: list[str] | None = Field(default=None, max_length=8, description='e.g. ["1h","4h","24h"]')
    lookback_years: float = Field(default=3, gt=0, le=20)
    include_funding: bool = True


class EventStudyInput(ToolInput):
    inst_id: str | None = None
    asset_class: str | None = None
    event_type: str
    window: tuple[int, int] = Field(default=(-24, 72), description="hours before/after the event")
    bar: Bar | None = None
    lookback_years: float = Field(default=2, gt=0, le=20)


class SeasonalityInput(HistorySeriesInput):
    by: Literal["hour", "weekday", "month", "funding"] = "hour"


class BacktestRuleInput(ToolInput):
    rule_spec: dict[str, Any] = Field(description="atlas_quant RuleSpec: name, side, indicators, entry, exit")
    inst_ids: list[str] = Field(min_length=1, max_length=20)
    period: tuple[Date, Date] | None = Field(default=None, description="UTC dates, end inclusive")
    costs: dict[str, float] = Field(default_factory=dict, description="taker_fee_rate, slippage_bps")
    bar: Bar | None = None
    splits: int | None = Field(default=None, ge=1, le=10)


class VolForecastInput(HistorySeriesInput):
    horizon: int = Field(default=24, ge=1, le=720, description="hours")


class LeadLagInput(ToolInput):
    a: str
    b: str
    max_lag: int = Field(default=24, ge=1, le=500, description="bars")
    bar: Bar | None = None
    lookback: int | None = Field(default=None, ge=10, le=20000, description="bars")


# -- warroom -----------------------------------------------------------------------------------------


AgentKind = Literal[
    "text", "watchlist", "research_report", "news_alert", "vote_summary", "post_mortem", "system_alert"
]
"""The brief §12 message kinds an agent may author (ATL-88)."""


class PostMessageInput(ToolInput):
    kind: AgentKind
    content_md: str = Field(max_length=20000)
    card: dict[str, Any] | None = None


class ReplyInput(ToolInput):
    message_id: str
    content_md: str = Field(max_length=20000)


class MentionInput(ToolInput):
    agent: str
    request: str = Field(max_length=4000)


class SubmitProposalInput(ToolInput):
    action: Literal["submit", "revise", "withdraw"] = Field(
        default="submit",
        description="submit a new proposal; revise proposal_id once (a new version); withdraw proposal_id",
    )
    kind: Literal["entry", "amend_stop", "amend_targets", "partial_close", "close"] = Field(
        default="entry",
        description="entry (Strategist) or a position action (Portfolio Manager only)",
    )
    proposal: dict[str, Any] | None = Field(
        default=None,
        description="entry: TradeProposal JSON (brief §6.3, stop required); position actions: "
        "{inst_id, stop?, take_profits?, close_pct?, rule, rationale_md, evidence}. Not for withdraw.",
    )
    proposal_id: str | None = Field(default=None, description="the proposal to revise or withdraw")
    summary_md: str | None = Field(
        default=None, max_length=2000, description="the human summary shown on the War Room card"
    )


class CastVoteInput(ToolInput):
    proposal_id: str
    vote: Literal["take", "skip", "change"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=4000)
    fix: str | None = Field(default=None, max_length=2000, description="the fix a 'change' vote asks for")


class RequestHumanInput(ToolInput):
    question: str = Field(max_length=4000)
    options: list[str] | None = None
    timeout_s: int = Field(default=600, ge=10, le=86400)


# -- memory ------------------------------------------------------------------------------------------


class ReadJournalInput(ToolInput):
    inst_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)


class WriteJournalInput(ToolInput):
    entry: dict[str, Any]


class ReadNotebookInput(ToolInput):
    date: Date | None = None


class AppendNotebookInput(ToolInput):
    note: str = Field(max_length=8000)


class ScoresInput(ToolInput):
    days: int = Field(default=7, ge=1, le=365)


# -- orchestration (brief §7.9) -------------------------------------------------------------------
# §7.9 also names open_vote/close_vote; the vote is opened and closed by code (ATL-154/ATL-160), so
# no agent has them and they are not in the catalogue (ATL-161).


class StartCycleInput(ToolInput):
    kind: str


class AssignInput(ToolInput):
    agent: str
    task: str = Field(max_length=4000)
    deadline: datetime


class SetAgentStateInput(ToolInput):
    agent: str
    state: Literal["paused", "active"]


class ThreadIdInput(ToolInput):
    thread_id: str


CATALOGUE: Final[dict[str, dict[str, type[ToolInput]]]] = {
    "okx_market": {
        "list_instruments": ListInstrumentsInput,
        "get_tickers": GetTickersInput,
        "get_candles": GetCandlesInput,
        "get_orderbook": GetOrderbookInput,
        "get_funding": HistoryDaysInput,
        "get_open_interest": HistoryDaysInput,
        "get_long_short_ratio": CcyPeriodInput,
        "get_taker_volume": CcyPeriodInput,
        "get_mark_price": InstIdInput,
        "get_instrument_spec": InstIdInput,
        "get_ta_table": InstIdInput,  # ATL-135: the Technical Analyst's pre-computed table
        "get_positioning_features": InstIdInput,  # ATL-136: the Derivatives & Sentiment features
    },
    "atlas_data": {
        "get_account_summary": NoInput,
        "get_positions": NoInput,
        "get_open_orders": NoInput,
        "get_settings_snapshot": NoInput,
        "get_today_budget_left": NoInput,
        "get_watchlist": GetWatchlistInput,
        "get_correlations": GetCorrelationsInput,
    },
    "news": {
        "search_news": SearchNewsInput,
        "get_story_cluster": GetStoryClusterInput,
        "get_economic_calendar": CalendarInput,
        "get_earnings_calendar": EarningsCalendarInput,
        "get_okx_announcements": SinceInput,
        "fetch_article": FetchArticleInput,
        "web_search": WebSearchInput,
    },
    "history": {
        "compute_stats": StatsInput,
        "detect_regime": HistorySeriesInput,
        "find_analogs": FindAnalogsInput,
        "event_study": EventStudyInput,
        "seasonality": SeasonalityInput,
        "backtest_rule": BacktestRuleInput,
        "volatility_forecast": VolForecastInput,
        "drawdown_profile": HistorySeriesInput,
        "cross_asset_lead_lag": LeadLagInput,
    },
    "warroom": {
        "post_message": PostMessageInput,
        "reply": ReplyInput,
        "mention": MentionInput,
        "submit_proposal": SubmitProposalInput,
        "cast_vote": CastVoteInput,
        "request_human_input": RequestHumanInput,
    },
    "memory": {
        "read_journal": ReadJournalInput,
        "write_journal": WriteJournalInput,
        "read_notebook": ReadNotebookInput,
        "append_notebook": AppendNotebookInput,
        "get_my_scores": ScoresInput,
    },
    "orchestration": {
        "start_cycle": StartCycleInput,
        "assign": AssignInput,
        "set_agent_state": SetAgentStateInput,
        "summarise_thread": ThreadIdInput,
    },
}


def _stub(server: str, name: str) -> Any:
    async def not_implemented(ctx: ToolContext, args: Any) -> ToolResult:
        raise ToolError(NOT_IMPLEMENTED, f"{server}.{name} is not implemented yet")

    return not_implemented


def register_catalogue() -> None:
    """Register a stub for every catalogue tool not already registered for real. Idempotent."""
    for server, tools in CATALOGUE.items():
        for name, input_model in tools.items():
            register(
                ToolDef(
                    server=server,
                    name=name,
                    description=f"{server}.{name} (not implemented yet: returns error not_implemented).",
                    input_model=input_model,
                    output_model=StubOutput,
                    fn=_stub(server, name),
                    implemented=False,
                )
            )


register_catalogue()
