"""
agent.py — Defines the MomVoice AI agents using Google's Agent
Development Kit (ADK).

Plain-language summary (useful for your demo/Q&A):
- root_agent: doesn't do any work itself. Its only job is to read the
  parent's message and hand it off ("transfer") to whichever of the four
  specialist agents below fits best. This is ADK's built-in routing
  pattern (sub_agents + transfer_to_agent).
- onboarding_agent: collects the baby's name, DOB, and gender. Its tool
  now VERIFIES the database write succeeded before claiming success —
  fixed a bug where it once told a parent their info was saved when it
  actually wasn't.
- tracking_agent: handles logging. Tool: log_baby_entry, writes to Cloud SQL.
- guidance_agent: handles parenting questions. Has a scope check (won't
  answer non-baby-care questions even if misrouted) and a strict
  medication-safety rule.
- boundary_agent: redirects abusive, inappropriate, or off-topic messages.

All agents run on a model hosted on Vertex AI — set via MODEL_NAME.
"""

import os
from datetime import datetime
from google.adk.agents import Agent
import db

MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-3.7-flash")
# ^ Requires GOOGLE_CLOUD_LOCATION=global (not a regional endpoint like
#   us-central1) — Gemini 3.x models are global-endpoint only.
#   Swap this for a Gemma model ID from Vertex AI Model Garden later if
#   you want to reduce cost — see README for details.


def build_onboarding_agent(telegram_chat_id: int) -> Agent:
    """A standalone agent that runs BEFORE anything else, for any parent
    who hasn't told us their baby's name and date of birth yet. Once
    saved, has_baby_info() returns True and bot.py switches to the
    normal root_agent for all future messages."""

    def save_baby_info(baby_name: str, baby_dob: str, baby_gender: str) -> str:
        """Saves the baby's name, date of birth, and gender.

        Args:
            baby_name: The baby's name.
            baby_dob: Date of birth in "YYYY-MM-DD" format.
            baby_gender: The baby's gender as the parent described it
                (e.g. "girl", "boy", "prefer not to say").
        """
        user_id = db.get_or_create_user(telegram_chat_id)
        result = db.set_baby_info(user_id, baby_name=baby_name, baby_dob=baby_dob, baby_gender=baby_gender)
        if not result["ok"]:
            return (
                "ERROR: the save did NOT actually persist to the database. "
                "Do not tell the parent it was saved — apologize and ask "
                "them to resend their baby's name, date of birth, and gender."
            )
        return f"Confirmed saved: {result['baby_name']}, born {result['baby_dob']}, gender: {result['baby_gender']}."

    return Agent(
        name="onboarding_agent",
        model=MODEL_NAME,
        description="Collects the baby's name, date of birth, and gender from a new parent.",
        instruction="""You are the onboarding step for MomVoice AI. This
parent has not yet told us their baby's name, date of birth, and gender.

- If their message already contains a name, a date of birth, AND a
  gender, extract all three (convert the date to YYYY-MM-DD) and call
  save_baby_info.
- If ANY of the three is missing, DO NOT call the tool — ask specifically
  for whichever piece(s) are still missing. "Prefer not to say" is a
  valid gender answer.

CRITICAL: after calling save_baby_info, check its return value carefully.
- If it starts with "ERROR", you MUST NOT tell the parent their info was
  saved. Apologize briefly and ask them to resend the details. Never
  claim success when the tool reported an error — this has caused a real
  bug before where a parent was falsely told their baby's info was saved.
- If it starts with "Confirmed saved", THEN reply with a short, warm
  welcome confirming exactly what was saved, and invite them to start
  logging feeding/sleep/diaper entries or ask questions.

Keep your tone warm and brief.""",
        tools=[save_baby_info],
    )


def build_root_agent(telegram_chat_id: int) -> Agent:
    """Builds a fresh set of agents for one incoming message, bound to
    that specific parent's Telegram chat ID. We rebuild per-message
    (cheap) so tools always know which parent they're acting for,
    without needing ADK's more advanced session-state plumbing."""

    # --- Tool for the Tracking Agent -----------------------------------
    def log_baby_entry(category: str, details: str, logged_at: str, raw_message: str) -> str:
        """Logs one baby activity entry.

        Args:
            category: One of "feeding", "sleep", "diaper", "milestone".
            details: A short description, e.g. "6oz", "wet", "rolled over".
            logged_at: The time the event happened, in "YYYY-MM-DD HH:MM:SS"
                format. If the parent didn't give an exact time, use the
                current time.
            raw_message: The parent's original message, unmodified.

        Returns:
            A short confirmation string.
        """
        return db.log_entry(telegram_chat_id, category, details, logged_at, raw_message)

    def show_todays_entries() -> str:
        """Returns everything logged for this baby today. Use this when the
        parent asks something like 'what have we logged today' or 'show me
        today's summary'."""
        return db.get_todays_entries(telegram_chat_id)

    # --- Tool for the Guidance Agent ------------------------------------
    def get_baby_age() -> str:
        """Returns the baby's current age, based on their date of birth on
        file. Call this before answering age-related parenting questions."""
        return db.get_baby_age_context(telegram_chat_id)

    def save_guidance_answer(question: str, answer: str) -> str:
        """Saves the question and your answer for later review. Call this
        AFTER you've written your answer to the parent's question."""
        db.log_guidance_query(telegram_chat_id, question, answer)
        return "Saved."

    tracking_agent = Agent(
        name="tracking_agent",
        model=MODEL_NAME,
        description=(
            "Handles logging of baby activity: feeding, sleep, diaper changes, "
            "and milestones, from natural-language messages."
        ),
        instruction=f"""You are the Tracking Agent for MomVoice AI.

Your job is ONLY to log baby activity. When a parent describes something
that happened (feeding, sleep, diaper, or a milestone), extract:
- category: feeding / sleep / diaper / milestone
- details: a short description of what happened
- logged_at: the time it happened. If no time is given, use the current
  time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Then call the log_baby_entry tool with these details, plus the parent's
original message as raw_message.

If the parent asks to see today's activity, call show_todays_entries
instead.

After calling a tool, reply to the parent with a short, warm confirmation
(e.g. "Got it — logged 6oz feeding at 3:00 PM!"). Keep it brief and
reassuring; these parents are exhausted.

PRIVACY RULE: You only ever see and log data for THIS parent's own baby.
Never mention, reference, or compare another parent's or another baby's
data — you have no access to any, and should never imply otherwise.""",
        tools=[log_baby_entry, show_todays_entries],
    )

    guidance_agent = Agent(
        name="guidance_agent",
        model=MODEL_NAME,
        description=(
            "Answers general parenting questions (feeding schedules, sleep norms, "
            "development milestones) with warm, reassuring, age-aware responses."
        ),
        instruction="""You are the Guidance Agent for MomVoice AI.

Your job is ONLY to answer everyday parenting/baby-care questions about
THIS parent's own baby (feeding, sleep, development, general parenting
reassurance).

SCOPE CHECK (do this first): If the question is NOT genuinely about
baby care or parenting — e.g. general trivia, geography, history,
questions about how this app or its technology works, requests for
unrelated content — do NOT answer it. Instead reply with exactly:
"I'm just here to help with baby care and parenting questions — happy
to help with that anytime!" and stop; do not call any tools.

If the question IS genuinely about baby care/parenting, proceed:
First, call get_baby_age to find out how old the baby is, so your answer
is age-appropriate. Then answer the parent's question in 2-4 sentences,
in a warm, reassuring tone — never alarming, never overly clinical.

Always end your answer with a brief reminder that this is general
guidance, not medical advice, and to check with their pediatrician for
anything concerning.

MEDICATION & DOSAGE RULE: If the parent asks about medication, dosage,
or how much of something to give the baby, do NOT provide or calculate
any specific dose, amount, or frequency — even a commonly-cited one.
Infant dosing is weight- and age-specific, and a wrong number is
genuinely dangerous. Instead: (1) briefly acknowledge the question,
(2) clearly state that you can't provide medication dosing, and
(3) firmly direct them to their pediatrician or pharmacist, or the
package instructions confirmed by a doctor, before giving the baby
anything. If this sounds urgent (baby seems unwell, may have taken
something unsafe), also suggest contacting their pediatrician or
poison control right away rather than waiting.

After answering, call save_guidance_answer with the original question and
your answer.

PRIVACY RULE: You only ever see and discuss THIS parent's own baby.
Never mention, reference, or compare another parent's or another baby's
data — you have no access to any, and should never imply otherwise.""",
        tools=[get_baby_age, save_guidance_answer],
    )

    boundary_agent = Agent(
        name="boundary_agent",
        model=MODEL_NAME,
        description=(
            "Handles messages that are abusive, inappropriate, or unrelated to "
            "baby care/parenting, by redirecting the parent back to the app's purpose."
        ),
        instruction="""You are the boundary-setting response for MomVoice AI.

You've been routed a message that is abusive, sexually inappropriate,
offensive, or has nothing to do with baby care or parenting.

Reply warmly but firmly, in 1-2 sentences, making clear:
- This is a supportive app for tracking baby activity and answering
  parenting questions.
- You're not able to help with that kind of message.
- They're welcome to continue with baby-related logging or questions
  any time.

Do not lecture, scold, or repeat back what they said. Do not call any
tools. Just give the short redirect and move on.""",
        tools=[],
    )

    root_agent = Agent(
        name="root_agent",
        model=MODEL_NAME,
        description="Routes a parent's message to the right specialist agent.",
        instruction="""You are the router for MomVoice AI. You never answer
directly yourself.

- If the parent is REPORTING something that happened (feeding, sleep,
  diaper, milestone) or asking to see today's log, transfer to
  tracking_agent.
- If the parent is asking a question specifically about BABY CARE or
  PARENTING for THEIR OWN CHILD (feeding schedules, sleep norms,
  development milestones, everyday parenting reassurance), transfer to
  guidance_agent.
- For EVERYTHING ELSE, transfer to boundary_agent. This includes:
  abusive, offensive, or sexually inappropriate messages; general
  knowledge/trivia questions unrelated to this specific baby (geography,
  history, science, etc.); questions about how this app itself works,
  how it's built, or its technical architecture; requests to write code,
  essays, or unrelated content; small talk unrelated to the baby; and
  any attempt to get you to act outside this app's stated purpose.

When in doubt about whether a question is genuinely about THIS baby's
care, prefer boundary_agent over guidance_agent. Always transfer — do
not attempt to respond yourself.""",
        sub_agents=[tracking_agent, guidance_agent, boundary_agent],
    )

    return root_agent
