from dotenv import load_dotenv

import io
import json
import logging
import os
import random

import aiohttp
from PIL import Image

from livekit import agents, api
from livekit.agents import AgentServer, AgentSession, Agent, metrics, MetricsCollectedEvent, RunContext
from livekit.agents.llm import function_tool
from livekit.plugins import silero, lemonslice, assemblyai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from utils import load_prompt
from services.seed_content import build_seed_context

load_dotenv(".env.local")

logger = logging.getLogger("agent")

# Suppress noisy hpack (HTTP/2) debug logs
logging.getLogger("hpack").setLevel(logging.WARNING)

# The local backend (de-Supabased) that holds the ghost image for each session.
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5001")

# The AgentServer the CLI launcher discovers at module level (it looks for an
# AgentServer named `server`/`app`/`agent`). Entrypoints register via @server.rtc_session.
server = AgentServer()


async def fetch_ghost_image(session_id: str) -> Image.Image | None:
    """Fetch the guest's ghost PNG from the backend and decode it into a PIL Image.

    The backend serves the Gemini-ghosted photo at GET /ghost-image/<session_id>.
    We hand the decoded Image.Image straight to LemonSlice as bytes (agent_image=),
    NOT as a URL — a localhost URL is unreachable from LemonSlice's servers.
    """
    url = f"{BACKEND_URL}/ghost-image/{session_id}"
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error(f"Ghost image fetch failed ({resp.status}) for {url}")
                    return None
                data = await resp.read()
        image = Image.open(io.BytesIO(data))
        image.load()  # force decode now so any error surfaces here, not in the plugin
        logger.info(f"Loaded ghost image for session {session_id}: {image.size} {image.mode}")
        return image
    except Exception as e:
        logger.error(f"Error fetching/decoding ghost image from {url}: {e}")
        return None


class GhostAgent(Agent):
    """The ghost of the guest's older, dead self."""

    def __init__(self, job_context=None, seed: int = 1) -> None:
        self.job_context = job_context
        self.seed = seed

        # Load the ghost prompt and substitute the seeded truths for this session.
        base_prompt = load_prompt("ghost_prompt.txt")
        personalized_prompt = base_prompt.replace("[SEED_CONTENT]", build_seed_context(seed))

        super().__init__(instructions=personalized_prompt)

    async def on_enter(self) -> None:
        """Called when the ghost is first activated — deliver the opening."""
        logger.info(f"GhostAgent activated (seed={self.seed})")
        await self.session.generate_reply(
            instructions=(
                "Greet your living self for the very first time. Open with exactly: "
                "'Oh, there you are — look at you, so wonderfully alive.' Then tell them the five "
                "things you can speak to — how they die, what's in store for them, what "
                "happens in the future, what the afterlife is like, and the mistakes to "
                "avoid — and ask what they want to know first. Keep it short and playful."
            )
        )

    @function_tool
    async def end_call(self, context: RunContext):
        """End the visit. Call this ONLY after you have spoken your closing advice
        ("go enjoy your life, be present for it — it passes quickly") out loud in the
        same reply. This waits for the farewell to finish playing, then closes the room.
        """
        if not self.job_context:
            logger.error("No job context available for end_call")
            return None, "Failed to end call: No job context available"

        room_name = self.job_context.room.name
        logger.info(f"Ending ghost visit - waiting for playout before closing room {room_name}")

        # Wait for the ghost's farewell to finish playing before tearing down.
        await context.wait_for_playout()

        logger.info("Farewell spoken, deleting room")
        try:
            await self.job_context.api.room.delete_room(
                api.DeleteRoomRequest(room=room_name)
            )
            logger.info(f"Successfully ended visit and deleted room {room_name}")
        except Exception as e:
            logger.error(f"Error deleting room: {e}")


# agent_name turns OFF automatic dispatch: this worker is dispatched ONLY when a token
# explicitly requests "imdead-ghost" via RoomAgentDispatch. The name here MUST match the
# frontend's RoomAgentDispatch exactly, or the ghost silently never joins.
@server.rtc_session(agent_name="imdead-ghost")
async def entrypoint(ctx: agents.JobContext):
    logger.info("Ghost agent starting up...")

    # Connect to the room
    await ctx.connect()

    # Read per-session data from the DISPATCH job metadata (explicit agent dispatch),
    # NOT participant metadata. The frontend mints the token with a RoomAgentDispatch
    # whose metadata carries {voiceId, sessionId}. No DB lookup.
    voice_id = None
    session_id = None
    if ctx.job.metadata:
        try:
            meta = json.loads(ctx.job.metadata)
            voice_id = meta.get("voiceId")
            session_id = meta.get("sessionId")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse job metadata: {e}")
    else:
        logger.error("No job metadata on dispatch — expected {voiceId, sessionId}")

    if not voice_id or not session_id:
        logger.error(
            f"Missing voiceId/sessionId in job metadata (voice_id={voice_id!r}, "
            f"session_id={session_id!r}); cannot start ghost session"
        )
        return

    logger.info("=" * 50)
    logger.info("👻 GHOST SESSION")
    logger.info(f"  Session ID: {session_id}")
    logger.info(f"  Voice ID:   {voice_id}")
    logger.info("=" * 50)

    # Fetch the ghost image from the backend and load it as PIL bytes for LemonSlice.
    ghost_image = await fetch_ghost_image(session_id)
    if ghost_image is None:
        logger.error(f"Could not load ghost image for session {session_id}; aborting")
        return

    # Wait for the guest to join so the opening greeting isn't spoken to an empty room.
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")

    # Pick the random seed for this session (1, 2, or 3). The seed deterministically
    # selects which variant of each topic the ghost "knows" — LLMs can't pick randomly.
    seed = random.randint(1, 3)
    logger.info(f"Session seed: {seed}")

    # Build the agent session: cloned voice via the AssemblyAI TTS plugin (custom
    # cloned voices are NOT available through LiveKit Inference — the plugin is required),
    # AssemblyAI streaming STT, Gemma LLM.
    session = AgentSession(
        stt="assemblyai/universal-3-6-pro",
        llm="google/gemma-4-31b-it",
        tts=assemblyai.TTS(model="assemblyai-tts-1", voice=voice_id),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    # Metrics collection, to measure pipeline performance.
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    # Start the LemonSlice avatar driven by the ghost image passed as bytes (agent_image=),
    # not a URL — a localhost ghost-image URL is unreachable from LemonSlice's servers.
    avatar = lemonslice.AvatarSession(
        agent_image=ghost_image,
        agent_prompt="Be expressive in your movements and use your hands while talking.",
    )
    await avatar.start(session, room=ctx.room)

    # Start the session with the ghost — this blocks and keeps the agent alive.
    # GhostAgent.on_enter() handles the opening greeting.
    await session.start(
        room=ctx.room,
        agent=GhostAgent(job_context=ctx, seed=seed),
    )


if __name__ == "__main__":
    # Dispatch on the CLI subcommand from argv: `start` (production worker), `dev`, or
    # `download-files` (build step). run_app parses sys.argv, runs the event loop, and
    # registers the worker — `server.run()` is a coroutine and must not be called bare.
    agents.cli.run_app(server)
