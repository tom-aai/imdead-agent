"""
Tests for InterviewAgent behavior.

Focus: The agent must NOT repeat or echo back what the user just said.
Instead, it should acknowledge briefly and move the conversation forward
with a follow-up question or new angle.
"""

import pytest
from livekit.agents import AgentSession, inference, mock_tools

from agent import InterviewAgent
from services.user_data import UserProfileData


def _make_agent(agent_name="Ashley"):
    """Create an InterviewAgent with sensible test defaults."""
    return InterviewAgent(
        job_context=None,
        agent_name=agent_name,
        session_state={"call_type": "interview", "original_session_id": None},
        is_resume_session=False,
        previous_conversation=None,
    )


MOCKED_TOOLS = {
    "check_messages": lambda: "No pending messages.",
}

USER_PROFILE = UserProfileData(
    myers_briggs_code="INTJ",
    personality="Analytical and strategic thinker who enjoys deep exploration of ideas.",
    expertise_description="Software engineering and system design",
)


@pytest.mark.asyncio
async def test_does_not_repeat_user_response_short():
    """Agent must NOT parrot back the user's statement. Should acknowledge briefly and ask a follow-up."""
    async with (
        inference.LLM(model="openai/gpt-5.2-chat-latest") as llm,
        AgentSession(llm=llm, userdata=USER_PROFILE) as session,
    ):
        agent = _make_agent()

        with mock_tools(InterviewAgent, MOCKED_TOOLS):
            await session.start(agent)

            # First turn: greeting
            result1 = await session.run(user_input="Hi, nice to meet you")
            result1.expect.next_event().is_message(role="assistant")

            # Second turn: user shares a substantive point
            result2 = await session.run(
                user_input="I think the biggest mistake junior developers make is not understanding the fundamentals of data structures"
            )

            await result2.expect.next_event().is_message(role="assistant").judge(
                llm,
                intent="""
                The agent should NOT repeat or echo back what the user said (e.g. should NOT say
                'So you think the biggest mistake junior developers make is not understanding data structures'
                or anything that restates the user's words).

                Instead, the agent should acknowledge briefly (1-3 words like 'Interesting', 'Got it', 'That's a great point')
                and then ask a follow-up question or offer a new angle to explore.
                """,
            )


@pytest.mark.asyncio
async def test_does_not_repeat_user_response_detailed():
    """Agent must not echo back a longer, more detailed user statement."""
    async with (
        inference.LLM(model="openai/gpt-5.2-chat-latest") as llm,
        AgentSession(llm=llm, userdata=USER_PROFILE) as session,
    ):
        agent = _make_agent()

        with mock_tools(InterviewAgent, MOCKED_TOOLS):
            await session.start(agent)

            # Greeting
            result1 = await session.run(user_input="Hello")
            result1.expect.next_event().is_message(role="assistant")

            # User gives a detailed answer
            result2 = await session.run(
                user_input="In my experience, the most effective way to scale a team is to invest heavily in documentation and onboarding processes early on, because without those, every new hire becomes a bottleneck instead of a multiplier"
            )

            await result2.expect.next_event().is_message(role="assistant").judge(
                llm,
                intent="""
                CRITICAL: The agent's response must NOT restate, paraphrase, or echo back the user's statement.
                Specifically, the agent should NOT say things like:
                - 'So you believe investing heavily in documentation and onboarding...'
                - 'That's interesting, documentation and onboarding processes being key to scaling...'
                - 'Right, so without good documentation, new hires become bottlenecks...'

                The agent SHOULD:
                - Acknowledge very briefly (a few words at most)
                - Then immediately ask a follow-up question that goes deeper or explores a new angle
                - Keep the response concise (1-3 sentences total)
                """,
            )


@pytest.mark.asyncio
async def test_does_not_repeat_across_multiple_turns():
    """Agent must not parrot the user across a multi-turn conversation."""
    async with (
        inference.LLM(model="openai/gpt-5.2-chat-latest") as llm,
        AgentSession(llm=llm, userdata=USER_PROFILE) as session,
    ):
        agent = _make_agent()

        with mock_tools(InterviewAgent, MOCKED_TOOLS):
            await session.start(agent)

            # Greeting
            result1 = await session.run(user_input="Hey there!")
            result1.expect.next_event().is_message(role="assistant")

            # Turn 2: user shares a topic
            result2 = await session.run(
                user_input="I've been working on improving developer productivity through better tooling"
            )
            await result2.expect.next_event().is_message(role="assistant").judge(
                llm,
                intent="""
                Does NOT repeat 'improving developer productivity through better tooling' back to the user.
                Acknowledges briefly and asks a probing question.
                """,
            )

            # Turn 3: user elaborates
            result3 = await session.run(
                user_input="The key insight is that most developers spend 60 percent of their time reading code, not writing it, so we should optimize for readability"
            )
            await result3.expect.next_event().is_message(role="assistant").judge(
                llm,
                intent="""
                Does NOT echo back that 'developers spend 60 percent of their time reading code'
                or restate the user's insight about optimizing for readability.
                Does NOT use the words 'readability', 'reading code', or '60 percent' from the user's statement.
                Uses a generic acknowledgment and a follow-up question without any of the user's specific terms.
                """,
            )


@pytest.mark.asyncio
async def test_no_paraphrasing_key_phrases():
    """Agent must not paraphrase or reference specific key phrases from the user's last message."""
    async with (
        inference.LLM(model="openai/gpt-5.2-chat-latest") as llm,
        AgentSession(llm=llm, userdata=USER_PROFILE) as session,
    ):
        agent = _make_agent()

        with mock_tools(InterviewAgent, MOCKED_TOOLS):
            await session.start(agent)

            result1 = await session.run(user_input="Hi there")
            result1.expect.next_event().is_message(role="assistant")

            # Give a very specific and distinctive statement
            result2 = await session.run(
                user_input="California is the best state to start a tech company because of the network effects and access to venture capital"
            )

            await result2.expect.next_event().is_message(role="assistant").judge(
                llm,
                intent="""
                CRITICAL: The agent must NOT include any of these phrases or close variations:
                - 'California'
                - 'best state'
                - 'network effects'
                - 'venture capital'
                - 'start a tech company'

                The agent should respond with a VERY brief acknowledgment (like 'Interesting' or 'Got it')
                followed by a question that explores WHY or asks for a deeper angle, WITHOUT restating
                any specific content from what the user just said.

                Example of a GOOD response: "What made you come to that conclusion?"
                Example of a BAD response: "That's a great point about California and network effects. What role does venture capital play?"
                """,
            )


@pytest.mark.asyncio
async def test_no_agreement_echo_pattern():
    """Agent must not use the 'I agree, [restatement]' or 'You're right, [restatement]' pattern."""
    async with (
        inference.LLM(model="openai/gpt-5.2-chat-latest") as llm,
        AgentSession(llm=llm, userdata=USER_PROFILE) as session,
    ):
        agent = _make_agent()

        with mock_tools(InterviewAgent, MOCKED_TOOLS):
            await session.start(agent)

            result1 = await session.run(user_input="Hello, excited to chat")
            result1.expect.next_event().is_message(role="assistant")

            # Ask a question first to set up context
            result2 = await session.run(
                user_input="Microservices are overhyped. Most teams would be better off with a well-structured monolith until they hit real scaling problems"
            )

            await result2.expect.next_event().is_message(role="assistant").judge(
                llm,
                intent="""
                CRITICAL: The agent must NOT:
                - Say 'I agree, microservices are overhyped' or any agreement + restatement
                - Say 'You're right, a monolith is better for most teams' or similar
                - Say 'Yes, microservices vs monolith is an interesting debate' (this still echoes the topic)
                - Paraphrase the user's opinion back to them in any way

                The agent SHOULD:
                - Use ONLY a 1-3 word acknowledgment like 'Interesting take' or 'Bold claim'
                - Then immediately ask a probing question that does NOT reference microservices, monoliths,
                  or scaling from the user's statement
                - For example: "What experience led you to that?" or "How did you come to that view?"
                """,
            )
