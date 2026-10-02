"""Conversation Intelligence - record-grounded memory for every conversation.

    from app.services import conversation_intel as ci
    ctx = ci.build_context(db, lead)          # facts / inferences / unknowns, open questions,
                                              # objections, follow-up, state, next best action
    ci.decide_ai(ctx, "sms")                  # may automation speak now? why not?
    ci.check_reply(ctx, text, "sms")          # quality gate before anything automated is sent
    ci.suggest(db, lead, ctx, channel="sms")  # Smart Composer draft (never sends)

See engine.py for the memory rules and extract.py for what a message is read as.
"""
from app.services.conversation_intel.engine import (  # noqa: F401
    build_context, decide_ai, decide_outreach, get_state, set_mode, sync)
from app.services.conversation_intel.quality import check_reply  # noqa: F401
from app.services.conversation_intel.compose import prompt_block, suggest  # noqa: F401
