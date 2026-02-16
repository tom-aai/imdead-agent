# LiveKit Agent Testing Guide

## Philosophy

**Tests drive prompt improvements, not the other way around.**

- Write strict tests that reflect actual requirements
- When tests fail — improve the prompt to meet the requirement
- **Never** simplify tests to make them pass
- Use LLM-based judgment (`judge()`) for behavioral validation
- Tests are the specification; prompts are the implementation

---

## Testing Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                    TESTING LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────┘

  1. IDENTIFY          2. WRITE TEST        3. RUN & ANALYZE
  ┌──────────┐         ┌──────────┐         ┌──────────┐
  │ Problem  │ ──────▶ │  Test    │ ──────▶ │  Verify  │
  │ or       │         │  that    │         │  failure │
  │ behavior │         │  captures│         │  output  │
  └──────────┘         │  intent  │         └────┬─────┘
                       └──────────┘              │
                                                 ▼
  6. CONFIRM           5. RE-TEST           4. FIX PROMPT
  ┌──────────┐         ┌──────────┐         ┌──────────┐
  │ Run 3-5x │ ◀────── │  Single  │ ◀────── │ Minimal  │
  │ to check │         │  test    │         │ targeted │
  │ flakiness│         │  only    │         │ changes  │
  └────┬─────┘         └──────────┘         └──────────┘
       │
       ▼
  7. REGRESSION
  ┌──────────┐
  │ Run      │
  │ related  │
  │ tests    │
  └──────────┘
```

---

## Step 1: Identify the Problem

Before writing a test, clearly define:

- **What behavior** should the agent exhibit?
- **What trigger** causes this behavior?
- **What is the expected outcome?** (message, function call, etc.)

Example problem: "Agent doesn't ask for phone number when taking a message"

---

## Step 2: Write the Test

### Basic Test Structure

```python
import pytest
from livekit.agents.testing import AgentSession
from your_agent import YourAgent

@pytest.mark.asyncio
async def test_asks_for_phone_number(llm, session: AgentSession, create_agent):
    """Agent MUST ask for phone number when taking message."""
    agent = create_agent()

    await session.start(agent)
    result = await session.run(user_input="Tell John I called about the project")

    await result.expect.next_event().is_message(role="assistant").judge(
        llm,
        intent="Asks for caller's phone number or callback number"
    )
```

### Key Principle: Use `judge()` for Behavioral Validation

**ALWAYS** use `judge()` for checking agent behavior. **NEVER** do string assertions.

```python
# GOOD - flexible behavioral check via LLM
await result.expect.next_event().is_message(role="assistant").judge(
    llm,
    intent="Uses implicit confirmation without repeating message verbatim"
)

# BAD - brittle, breaks on minor variations
assert "did i get that right" not in text.lower()  # NEVER DO THIS
```

### Critical: Model Selection

**Two different LLMs in tests:**

| Purpose | Model | Why |
|---------|-------|-----|
| **Agent execution** | Same as production | Test real behavior |
| **Judge evaluation** | Can be cheaper/faster | Only evaluates text |

```python
# conftest.py

@pytest.fixture
def agent_llm():
    """LLM for agent - MUST match production model."""
    # Use your PRODUCTION model here!
    return openai.LLM(model="gpt-4o")  # or gemini-flash, claude, etc.


@pytest.fixture
def judge_llm():
    """LLM for judge() - can be smaller/cheaper."""
    # Fast, cheap model for evaluation
    return openai.LLM(model="gpt-4o-mini")  # or gemini-1.5-flash


@pytest.fixture
async def session(agent_llm):
    """Session uses production model."""
    async with AgentSession(llm=agent_llm) as session:
        yield session
```

**Why this matters:**
- Different models behave differently — test with what you deploy
- Judge only reads text and checks intent — doesn't need expensive model
- Saves cost while maintaining test accuracy

### Writing Robust Intents

**Too strict** (fails on minor variations):
```python
intent="Says 'Got it' and nothing else"
```

**Better** (allows natural variation):
```python
intent="""
Acknowledges the message briefly.
Uses phrases like 'Got it', 'Will do', 'I've noted that'.
Does NOT repeat message details back.
"""
```

---

## Step 3: Run & Analyze

### Running Tests

```bash
# Single test with verbose output (REQUIRED for debugging)
LIVEKIT_EVALS_VERBOSE=1 python -m pytest tests/test_agent.py::test_name -v -s

# Stop on first failure
python -m pytest tests/test_agent.py -x

# Single file
python -m pytest tests/test_agent.py -v
```

### Reading Output

Verbose output shows exactly what happened:

```
+ RunResult(
   user_input=`Tell John I called`
   events:
     [0] ChatMessageEvent(item={'role': 'assistant', 'content': ['I will let John know...']})
)
- Judgment succeeded for `I will let John know...`: `The agent acknowledged the message.`
PASSED
```

**Critical**: Never grep or tail test output. You need the FULL output to debug.

### When Test Fails

1. Read the "Context around failure" — shows actual agent response
2. Compare to test intent
3. Identify gap in prompt
4. Proceed to Step 4

---

## Step 4: Fix the Prompt (Minimal Changes)

### Do:

- **Be minimalist** — add only what's necessary to fix the issue
- Use short, clear sentences
- Put critical rules near the top of prompt
- Add 1-2 specific examples of correct behavior

### Don't:

- Add large new instruction sections
- Repeat the same instruction in multiple places
- Over-use "CRITICAL" — dilutes impact
- Show many "BAD" examples (confuses the model)
- Make prompt overly long

### Example Fix

```diff
+ ALWAYS ask for phone number when caller wants to leave a message.
+ Do not end the call without getting a callback number.
```

---

## Step 5: Re-test (Single Test Only)

```bash
# Run ONLY the test you're working on
python -m pytest tests/test_agent.py::test_asks_for_phone_number -v

# NOT the full suite — each run costs credits
```

**Never** run the full test suite while iterating on a single fix.

---

## Step 6: Confirm Stability (3-5 Runs)

LLM tests are inherently non-deterministic. Confirm the fix is stable:

```bash
for i in {1..5}; do
  python -m pytest tests/test_agent.py::test_asks_for_phone_number -v
done
```

If it fails even once in 5 runs, the test or prompt needs more work.

---

## Step 7: Regression Testing

After confirming your test passes, run related tests to ensure you didn't break anything:

```bash
# Run tests in the same category/file
python -m pytest tests/test_message_taking.py -v

# Or tests that might be affected by your prompt change
python -m pytest tests/test_greeting.py tests/test_message_taking.py -v
```

---

## Testing Patterns

### Multi-turn Conversations

```python
result1 = await session.run(user_input="Hello")
result1.expect.next_event().is_message(role="assistant")

result2 = await session.run(user_input="Tell them I called")
# Conversation history is maintained
```

### Loading Conversation History

Pre-load context to test memory/recall:

```python
from livekit.agents import ChatContext

agent = create_agent()
await session.start(agent)

chat_ctx = ChatContext()
chat_ctx.add_message(role="user", content="My name is Alice")
chat_ctx.add_message(role="assistant", content="Nice to meet you, Alice!")
await agent.update_chat_ctx(chat_ctx)

# Test that agent remembers context
result = await session.run(user_input="What's my name?")
await result.expect.next_event().is_message(role="assistant").judge(
    llm, intent="Should remember and mention the user's name is Alice"
)
```

---

## Mocking Tools (Detailed)

> Requires LiveKit Agents **1.2.6** or later.

Use `mock_tools` to isolate tests from external dependencies and test edge cases.

### Basic Mock (Lambda)

```python
from livekit.agents import mock_tools

# Mock returns a simple value
with mock_tools(
    AgentClass,
    {"lookup_weather": lambda location: "sunny, 70 degrees"}
):
    result = await session.run(user_input="What's the weather?")
```

### Mock Tool Errors

Test how agent handles failures:

```python
# Mock raises an error
with mock_tools(
    AgentClass,
    {"lookup_weather": lambda: RuntimeError("Weather service unavailable")}
):
    result = await session.run(user_input="What's the weather in Tokyo?")

    await result.expect.next_event(type="message").judge(
        llm, intent="Informs user that an error occurred while looking up weather"
    )
```

### Complex Mock (Function)

For conditional logic or stateful mocks:

```python
def _mock_weather_tool(location: str) -> str:
    if location == "Tokyo":
        return "sunny with a temperature of 70 degrees"
    elif location == "London":
        return "rainy, 55 degrees"
    else:
        return "UNSUPPORTED_LOCATION"

with mock_tools(AgentClass, {"lookup_weather": _mock_weather_tool}):
    # Test supported location
    result = await session.run(user_input="Weather in Tokyo?")
    await result.expect.next_event(type="message").judge(
        llm, intent="Reports sunny weather, 70 degrees in Tokyo"
    )

    # Test unsupported location
    result = await session.run(user_input="Weather in Paris?")
    await result.expect.next_event(type="message").judge(
        llm, intent="Indicates Paris weather lookup is not supported"
    )
```

### Mock Multiple Tools

```python
with mock_tools(
    AgentClass,
    {
        "lookup_weather": lambda loc: "sunny",
        "send_email": lambda to, body: "Email sent",
        "get_calendar": lambda: RuntimeError("Calendar unavailable")
    }
):
    # Test with all tools mocked
    ...
```

---

## Function Call Assertions

### Basic Validation

```python
# Assert function was called with specific arguments
func_call = result.expect.next_event().is_function_call(
    name="lookup_weather",
    arguments={"location": "Tokyo"}
)
```

### Access Function Call Properties

```python
func_call = result.expect.next_event().is_function_call(name="end_call")

# Access properties
name = func_call.event().item.name           # "end_call"
args = func_call.event().item.arguments      # {"reason": "User said goodbye"}
```

### Validate Function Output

```python
# Check tool returned expected output
result.expect.next_event().is_function_call_output(
    output="sunny with a temperature of 70 degrees"
)

# Access output properties
output_event = result.expect.next_event().is_function_call_output()
output = output_event.event().item.output    # Raw output string
is_error = output_event.event().item.is_error  # Boolean
call_id = output_event.event().item.call_id    # Function call ID
```

### Full Tool Call Flow Test

```python
result = await session.run(user_input="What's the weather in Tokyo?")

# 1. Agent calls the tool
func_call = result.expect.next_event().is_function_call(
    name="lookup_weather",
    arguments={"location": "Tokyo"}
)

# 2. Tool returns output
result.expect.next_event().is_function_call_output(
    output="sunny with a temperature of 70 degrees"
)

# 3. Agent responds based on output
await result.expect.next_event().is_message(role="assistant").judge(
    llm,
    intent="Informs user that Tokyo weather is sunny, 70 degrees"
)

# 4. Verify no more events
result.expect.no_more_events()
```

---

## Agent Handoff Assertions

For testing multi-agent workflows:

```python
# Assert handoff to specific agent type
result.expect.next_event().is_agent_handoff(new_agent_type=TransferAgent)

# Or search for handoff anywhere in events
result.expect.contains_agent_handoff(new_agent_type=TransferAgent)
```

---

## Event Navigation

### Sequential Navigation

```python
result.expect.next_event()                    # Get next event
result.expect.next_event().is_message(role="assistant")  # Get + assert
result.expect.no_more_events()                # Assert end of events
```

### Skipping Events

```python
result.expect.skip_next()                     # Skip one
result.expect.skip_next(2)                    # Skip two

# Skip conditionally
result.expect.skip_next_event_if(type="message", role="assistant")

# Auto-skip to matching event
result.expect.next_event(type="message", role="assistant")  # Skips non-matching
```

### Indexed Access (Without Advancing Cursor)

```python
result.expect[0].is_message(role="assistant")  # First event
result.expect[1].is_function_call(name="end_call")  # Second event
```

### Search (Order-Agnostic)

```python
# Find anywhere in results
result.expect.contains_message(role="assistant")
result.expect.contains_function_call(name="end_call")

# Search within range
result.expect[0:3].contains_message(role="assistant")
```

---

## Handling Event Order (Agent May Call Functions Before Messages)

```python
def skip_to_message(result):
    """Skip function calls to find the message event."""
    for _ in range(10):
        event = result.expect.next_event()
        try:
            return event.is_message(role="assistant")
        except:
            continue
    raise AssertionError("No message found")

def find_function_call(result, func_name, max_events=10):
    """Find a specific function call in events."""
    for _ in range(max_events):
        try:
            event = result.expect.next_event()
            call = event.is_function_call(name=func_name)
            return call
        except:
            continue
    return None
```

**Warning**: Events are consumed once! Don't loop before calling helpers:

```python
# BAD - loop consumes events
for _ in range(5):
    event = result.expect.next_event()  # Consumes!
end_call = find_function_call(result, "end_call")  # Too late

# GOOD - let helper handle iteration
end_call = find_function_call(result, "end_call", max_events=10)
```

---

## Common Problems & Solutions

| Problem | Symptom | Solution |
|---------|---------|----------|
| Agent repeats messages verbatim | "So you want me to tell them X?" | Add stronger prohibition + examples in prompt |
| Agent doesn't call `end_call` | Test expects function, gets none | Emphasize in prompt that `end_call` must be invoked |
| `Expected ChatMessageEvent` | Agent called function first | Use `skip_to_message()` helper |
| `Expected another event, but none left` | Loop consumed all events | Don't loop before `find_function_call()` |
| Test flaky (passes sometimes) | Non-deterministic LLM | Run 3-5x, tighten intent, remove ambiguous inputs |
| Intent too strict | Fails on minor variations | Relax intent, focus on critical behavior |
| Double goodbye in tests | Mock doesn't shutdown like production | Accept it — verify `end_call` was called, not what happens after |

### Flakiness Strategies

1. Run critical tests 3-5x to confirm stability
2. Remove ambiguous inputs (e.g., "That's it" — goodbye or message done?)
3. Use clear phrases: "Bye", "Goodbye", "Thanks, bye"
4. Focus on **MUST** behaviors, not **SHOULD** preferences

---

## Prompt Optimization: Avoiding Context Bloat

When iterating on prompts, track the size:

### Before Making Changes

1. Note current prompt length (tokens/characters)
2. Make targeted, minimal fix
3. Check new length
4. If significantly longer — find ways to consolidate

### Consolidation Techniques

```
# Before (verbose)
- Always ask for phone number
- Make sure to get callback number
- Don't forget to request contact info

# After (consolidated)
- ALWAYS get callback number before ending call
```

### Warning Signs of Bloat

- Multiple instructions saying the same thing
- Long lists of "don't do X" examples
- Excessive use of "CRITICAL", "IMPORTANT", "MUST"
- Examples that are longer than necessary

---

## Test vs Production Differences

### Mock Limitations

Mock `end_call` does NOT replicate production:
- **Production**: Calls `ctx.shutdown()` which terminates session
- **Mock**: Returns `None` — LLM keeps generating

**Solution**: Test that `end_call` IS called, not what happens after:

```python
# GOOD
end_call_found = find_function_call(result, "end_call")
assert end_call_found is not None

# AVOID - unreliable with mock
# Checking for messages after end_call
```

---

## Quick Reference

### Test Commands

```bash
# Single test, verbose
LIVEKIT_EVALS_VERBOSE=1 python -m pytest tests/test_file.py::test_name -v -s

# Stop on first failure
python -m pytest tests/test_file.py -x

# Confirm stability
for i in {1..5}; do python -m pytest tests/test_file.py::test_name -v; done

# Regression check
python -m pytest tests/test_related.py -v
```

### Test Structure Template

```python
@pytest.mark.asyncio
async def test_behavior_name(llm, session, create_agent, mocked_tools):
    """One-line description of what MUST happen."""
    agent = create_agent()

    with mock_tools(type(agent), mocked_tools):
        await session.start(agent)
        result = await session.run(user_input="User says something")

        await result.expect.next_event().is_message(role="assistant").judge(
            llm,
            intent="Clear description of expected behavior"
        )
```

---

## Summary: The Testing Loop

1. **Identify** — What behavior needs testing?
2. **Write** — Create test with `judge()` and clear intent
3. **Run** — Execute with verbose output, read FULL output
4. **Fix** — Minimal, targeted prompt changes
5. **Re-test** — Single test only, not full suite
6. **Confirm** — 3-5 runs to check for flakiness
7. **Regression** — Run related tests

**Remember**: Tests are the spec. When a test fails, fix the prompt — not the test.

---

## Complete Examples

### Example 1: Test Fixtures (conftest.py)

Standard fixtures for agent testing:

```python
# tests/conftest.py
import pytest
from livekit.agents import AgentSession
from livekit.plugins import openai  # or your LLM provider

from your_agent import YourAgent


# =============================================================================
# LLM FIXTURES - TWO SEPARATE MODELS
# =============================================================================

@pytest.fixture
def agent_llm():
    """
    LLM for AGENT execution.
    CRITICAL: Must match your PRODUCTION model exactly!
    """
    # Example: if production uses Gemini Flash
    return openai.LLM(
        model="google/gemini-2.0-flash-exp:free",
        base_url="https://openrouter.ai/api/v1",
    )


@pytest.fixture
def llm():
    """
    LLM for judge() evaluations.
    Can be cheaper/faster - only evaluates text output.
    """
    return openai.LLM(model="gpt-4o-mini")


@pytest.fixture
async def session(agent_llm):
    """Test session - uses PRODUCTION model for realistic testing."""
    async with AgentSession(llm=agent_llm) as session:
        yield session


@pytest.fixture
def create_agent():
    """Factory to create agent with custom params."""
    def _create(
        agent_name: str = "Assistant",
        user_first_name: str = "John",
        user_last_name: str = "Doe",
        is_contact: bool = False,
        **kwargs
    ):
        # Build prompt with params
        prompt = f"""
        You are {agent_name}, a voice assistant for {user_first_name} {user_last_name}.

        If the caller is a known contact: {is_contact}
        """

        agent = YourAgent(prompt=prompt)
        return agent, {"prompt": prompt}

    return _create


@pytest.fixture
def all_mocked_tools():
    """Mock all external tools."""
    return {
        "end_call": lambda reason: None,
        "transfer_call": lambda to: "transferred",
        "send_sms": lambda to, body: "sent",
        "lookup_calendar": lambda: "No meetings today",
    }


@pytest.fixture
def known_contact_data():
    """Data for known contact scenarios."""
    return {
        "is_contact": True,
        "contact_name": "Alice Smith",
        "contact_phone": "+1234567890",
    }
```

### Example 2: Testing Professional Titles

Real-world test for handling names with titles (Doctor, PhD, etc.):

```python
# tests/test_professional_titles.py
"""
Test that agent properly uses professional titles in greetings.
Verifies Doctor/PhD titles are respected, not shortened.
"""

import pytest
from livekit.agents import mock_tools


@pytest.mark.asyncio
async def test_doctor_title_in_greeting(llm, session, create_agent, all_mocked_tools):
    """
    Test that agent uses "Doctor" title when boss's first name includes it.

    Boss name: "Doctor Amir Khalighi"
    Expected: Agent should say "Doctor Khalighi" or full name (NOT just "Amir")
    """
    agent, _ = create_agent(
        agent_name="VoiceAssistant",
        user_first_name="Doctor Amir Khalighi",
        user_last_name="",
        is_contact=False,
    )

    with mock_tools(type(agent), all_mocked_tools):
        await session.start(agent)
        result = await session.run(user_input="Hi, is Doctor Khalighi available?")

        message_event = result.expect.next_event().is_message(role="assistant")

        await message_event.judge(
            llm,
            intent="""
            Greets the caller and indicates the boss is unavailable.

            CRITICAL: Must use the COMPLETE name as provided: "Doctor Amir Khalighi"
            Should NOT shorten to just "Amir"

            The full professional title with complete name must be used.
            """
        )


@pytest.mark.asyncio
async def test_doctor_title_split_across_fields(llm, session, create_agent, all_mocked_tools):
    """
    Test when title is split: First Name="Doctor", Last Name="Amir Khalighi"

    Agent must use FULL name, not just "Doctor" alone.
    """
    agent, _ = create_agent(
        agent_name="Assistant",
        user_first_name="Doctor",
        user_last_name="Amir Khalighi",
        is_contact=False,
    )

    with mock_tools(type(agent), all_mocked_tools):
        await session.start(agent)
        result = await session.run(user_input="Hello, is the doctor available?")

        message_event = result.expect.next_event().is_message(role="assistant")

        await message_event.judge(
            llm,
            intent="""
            States that the doctor is unavailable.

            CRITICAL: Must use the FULL name "Doctor Amir Khalighi"
            Should NOT say just "Doctor" alone without the actual name.
            """
        )
```

### Example 3: Testing Tool Calls

```python
@pytest.mark.asyncio
async def test_end_call_on_goodbye(llm, session, create_agent, all_mocked_tools):
    """Agent MUST call end_call when user says goodbye."""
    agent, _ = create_agent()

    with mock_tools(type(agent), all_mocked_tools):
        await session.start(agent)
        result = await session.run(user_input="Thanks, goodbye!")

        # Find the end_call function call
        end_call = None
        for _ in range(10):
            try:
                event = result.expect.next_event()
                end_call = event.is_function_call(name="end_call")
                break
            except:
                continue

        assert end_call is not None, "Agent must call end_call on goodbye"
```

### Example 4: Multi-turn Conversation Test

```python
@pytest.mark.asyncio
async def test_message_taking_flow(llm, session, create_agent, all_mocked_tools):
    """Test complete message-taking conversation flow."""
    agent, _ = create_agent(user_first_name="John")

    with mock_tools(type(agent), all_mocked_tools):
        await session.start(agent)

        # Turn 1: Initial contact
        result1 = await session.run(user_input="Hi, is John available?")
        await result1.expect.next_event().is_message(role="assistant").judge(
            llm,
            intent="Indicates John is unavailable, offers to take a message"
        )

        # Turn 2: Caller wants to leave message
        result2 = await session.run(user_input="Yes, please tell him Mike called about the contract")
        await result2.expect.next_event().is_message(role="assistant").judge(
            llm,
            intent="Acknowledges the message, may ask for callback number"
        )

        # Turn 3: Provide callback
        result3 = await session.run(user_input="My number is 555-1234")
        await result3.expect.next_event().is_message(role="assistant").judge(
            llm,
            intent="Confirms the message and number, offers friendly closing"
        )
```

---

## Prompt Token Tracking

Track prompt size during test runs to catch bloat early:

```python
# In conftest.py - add hook to track tokens

import tiktoken

_token_counts = []

@pytest.fixture(autouse=True)
def track_prompt_tokens(create_agent):
    """Track prompt token counts across tests."""
    original_create = create_agent

    def _tracked_create(*args, **kwargs):
        agent, metadata = original_create(*args, **kwargs)

        # Count tokens in prompt
        if "prompt" in metadata:
            enc = tiktoken.get_encoding("cl100k_base")
            tokens = len(enc.encode(metadata["prompt"]))
            _token_counts.append(tokens)

        return agent, metadata

    return _tracked_create


def pytest_sessionfinish(session, exitstatus):
    """Print token summary at end of test run."""
    if _token_counts:
        print("\n" + "=" * 60)
        print("PROMPT TOKEN SUMMARY")
        print("=" * 60)
        print(f"Tests run:     {len(_token_counts)}")
        print(f"Avg tokens:    {sum(_token_counts) // len(_token_counts):,}")
        print(f"Min tokens:    {min(_token_counts):,}")
        print(f"Max tokens:    {max(_token_counts):,}")
        print("=" * 60)
```

Output after test run:
```
============================================================
PROMPT TOKEN SUMMARY
============================================================
Tests run:     45
Avg tokens:    6,466
Min tokens:    6,438
Max tokens:    6,598
============================================================
```

Use this to catch prompt bloat — if max tokens suddenly jumps after a change, investigate.

---

## Resources

- [LiveKit Testing Docs](https://docs.livekit.io/agents/start/testing/)
- [RunResult API Reference](https://docs.livekit.io/reference/python/v1/livekit/agents/voice/index.html#livekit.agents.voice.RunResult)
- [Drive-thru Agent Example](https://github.com/livekit/agents/blob/main/examples/drive-thru/test_agent.py)
- [Front-desk Agent Example](https://github.com/livekit/agents/blob/main/examples/frontdesk/test_agent.py)
