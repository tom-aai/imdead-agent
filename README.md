# Expert Avatar Interview Agent

A voice AI platform that enables experts to create interactive AI avatars of themselves. Experts conduct voice interviews to capture their knowledge, which is then transformed into a shareable avatar that seekers can chat with to learn from their expertise.

## Overview

Expert Avatar consists of three main components:

- **Next.js Frontend** - Dashboard for managing sessions, viewing avatars, and initiating calls
- **Flask Backend** - API and background worker for processing calls and generating avatars
- **LiveKit Agents** - Real-time voice AI agents that conduct interviews and power avatar chats

## System Architecture

```mermaid
flowchart TB
    subgraph frontend [Next.js Frontend]
        Dashboard[Dashboard]
        SessionView[Session View]
        WelcomeScreen[Welcome Screen]
    end
    
    subgraph livekit [LiveKit Cloud]
        direction TB
        Room[Room Service]
        InterviewAgent[Interview Agent]
        ChatAgent[Chat Agent]
        HedraAvatar[Hedra Avatar]
        STT[AssemblyAI STT]
        TTS[Inworld TTS]
        LLM[OpenAI GPT-4.1]
    end
    
    subgraph backend [Flask Backend]
        API[Flask API Server]
        Worker[Background Worker]
    end
    
    subgraph external [External Services]
        Anthropic[Anthropic Claude]
        HedraAPI[Hedra API]
        Inworld[Inworld Voice]
        Resend[Resend Email]
    end
    
    subgraph supabase [Supabase]
        DB[(PostgreSQL)]
        Storage[(Object Storage)]
        Auth[Auth]
    end
    
    Dashboard --> Room
    SessionView --> Room
    Room --> InterviewAgent
    Room --> ChatAgent
    InterviewAgent --> HedraAvatar
    ChatAgent --> HedraAvatar
    InterviewAgent --> STT
    InterviewAgent --> TTS
    InterviewAgent --> LLM
    
    InterviewAgent --> API
    ChatAgent --> API
    API --> Worker
    Worker --> Anthropic
    Worker --> HedraAPI
    Worker --> Inworld
    Worker --> Resend
    Worker --> DB
    Worker --> Storage
    
    Dashboard --> Auth
    Dashboard --> DB
```

## Agent Architecture

The interview agent uses a multi-agent handoff pattern to handle different conversation phases efficiently. This minimizes latency by keeping each agent's context focused.

```mermaid
flowchart LR
    subgraph agents [Interview Agent Service]
        direction TB
        Entrypoint[Entrypoint]
        Onboarding[OnboardingAgent]
        Interview[InterviewAgent]
        Mail[MailAgent]
    end
    
    Entrypoint -->|new user| Onboarding
    Entrypoint -->|returning user| Interview
    Entrypoint -->|resume session| Interview
    Interview -->|check messages| Mail
    Mail -->|continue interview| Interview
```

| Agent | Purpose |
|-------|---------|
| **OnboardingAgent** | Conducts initial interview with new experts to capture their expertise, background, and unique perspectives |
| **InterviewAgent** | Handles returning users for deeper interviews, with personalized prompts based on their profile |
| **MailAgent** | Presents pending messages from seekers and handles responses (answer questions, approve/decline intros) |

### Chat Agent (Separate Service)

The `expertavatar-chat-agent` is a separate LiveKit agent that powers seeker-facing avatar sessions. It loads the expert's profile, transcript, and cloned voice to create an interactive avatar experience.

```mermaid
flowchart LR
    Seeker[Seeker] --> ChatAgent[ChatAvatarAgent]
    ChatAgent --> Avatar[Expert's Avatar]
    ChatAgent --> Voice[Cloned Voice]
    ChatAgent --> DB[(Session Data)]
```

## User Flows

### Flow 1: New Expert Onboarding

A new expert creates their first avatar session through an onboarding interview.

```mermaid
sequenceDiagram
    participant E as Expert
    participant F as Frontend
    participant L as LiveKit
    participant A as OnboardingAgent
    participant B as Backend API
    participant W as Worker
    participant S as Supabase

    E->>F: Start new session
    F->>L: Create room + join
    L->>A: Activate OnboardingAgent
    A->>E: Greet and explain process
    
    loop Interview Objectives
        A->>E: Ask about expertise
        E->>A: Share knowledge
    end
    
    A->>A: Call end_call tool
    A->>L: Delete room
    L->>S: Save transcript to interview_calls
    L->>B: POST /post-call-orchestrator
    B->>S: Queue post_call job
    
    W->>S: Claim job
    W->>W: Process transcript with LLM
    W->>W: Aggregate user profile
    W->>W: Cartoonize avatar image
    W->>W: Generate Hedra avatar
    W->>W: Clone voice from recording
    W->>S: Update interview_call_analysis
    W->>S: Update user_profiles
```

**Backend Processing Tasks:**
1. **Transcript Processing** - Extracts main topic, hot takes, key insights using Claude
2. **Profile Aggregation** - Builds comprehensive user profile from all sessions
3. **Image Cartoonization** - Transforms photo to editorial illustration style
4. **Hedra Avatar Generation** - Creates animated video avatar from cartoon
5. **Voice Cloning** - Clones expert's voice from call recording

### Flow 2: Expert Resume Session (Update Content)

An expert returns to add more content to an existing avatar session.

```mermaid
sequenceDiagram
    participant E as Expert
    participant F as Frontend
    participant L as LiveKit
    participant A as InterviewAgent
    participant B as Backend API
    participant W as Worker
    participant S as Supabase

    E->>F: Click "Resume" on session
    F->>S: Fetch conversation summary
    F->>L: Join room with resume metadata
    L->>A: Activate InterviewAgent
    Note over A: Loaded with previous context
    A->>E: Welcome back, continue discussion
    
    loop Continued Interview
        A->>E: Explore topic deeper
        E->>A: Provide more insights
    end
    
    L->>S: Save new transcript
    L->>B: POST /resume-session-orchestrator
    B->>S: Queue resume_session job
    
    W->>S: Claim job
    W->>W: Append transcript to original
    W->>W: Re-analyze combined transcript
    W->>S: Update interview_call_analysis
```

### Flow 3: Expert Reviews Messages (MailAgent Handoff)

An expert checks and responds to messages from seekers who chatted with their avatar.

```mermaid
sequenceDiagram
    participant E as Expert
    participant L as LiveKit
    participant I as InterviewAgent
    participant M as MailAgent
    participant B as Backend API
    participant W as Worker
    participant S as Supabase

    I->>S: Check pending message count
    I->>E: You have N new messages
    E->>I: I want to check them
    I->>I: Call check_messages tool
    I->>M: Handoff to MailAgent
    
    M->>E: Present first message
    E->>M: Answer/Approve/Decline/Skip
    M->>S: Update seeker_messages status
    
    loop More Messages
        M->>E: Present next message
        E->>M: Take action
        M->>S: Update status
    end
    
    alt Continue Interview
        M->>I: Handoff back to InterviewAgent
    else End Session
        M->>B: POST /mail-session-orchestrator
        B->>S: Queue mail_session job
        W->>W: Send answer emails to seekers
        W->>W: Send intro emails for approved intros
    end
```

### Flow 4: Seeker Chats with Expert Avatar

A seeker interacts with an expert's shareable avatar to learn from their expertise.

```mermaid
sequenceDiagram
    participant Se as Seeker
    participant F as Frontend
    participant L as LiveKit
    participant C as ChatAvatarAgent
    participant H as Hedra Avatar
    participant B as Backend API
    participant W as Worker
    participant S as Supabase

    Se->>F: Open share link
    F->>S: Fetch session data
    F->>L: Join chat room
    L->>C: Activate ChatAvatarAgent
    C->>S: Load expert profile + transcript
    C->>H: Start expert's Hedra avatar
    Note over C: Uses expert's cloned voice
    
    C->>Se: Greet as expert persona
    
    loop Conversation
        Se->>C: Ask question
        C->>Se: Respond as expert
    end
    
    alt Seeker wants to contact real expert
        Se->>C: Ask to leave question
        C->>C: Call create_message tool
        C->>S: Insert to seeker_messages
        C->>Se: I'll pass that to the real expert
    end
    
    L->>S: Save chat transcript
    L->>B: POST /chat-session-orchestrator
    B->>S: Queue message_notification job
    W->>W: Email expert about new messages
```

### Flow 5: Dashboard Settings

Users manage their account settings from the dashboard.

```mermaid
flowchart TB
    subgraph dashboard [Dashboard Actions]
        Email[Update Email Settings]
        Voice[Re-clone Voice]
        Avatar[Update Avatar Image]
    end
    
    subgraph email_flow [Email Settings]
        Email --> DB1[(user_profiles)]
    end
    
    subgraph voice_flow [Voice Re-clone]
        Voice --> Recorder[Voice Recorder Component]
        Recorder --> API1[POST /reclone-voice]
        API1 --> Job1[voice_reclone job]
        Job1 --> Inworld[Inworld Voice Clone]
        Inworld --> DB2[(user_profiles.voice_id)]
    end
    
    subgraph avatar_flow [Avatar Update]
        Avatar --> Upload[Upload New Image]
        Upload --> Storage[(avatar-images-bucket)]
        Upload --> API2[POST /update-session-avatar]
        API2 --> Job2[avatar_update job]
        Job2 --> Cartoon[Cartoonize Image]
        Cartoon --> Hedra[Generate Hedra Avatar]
        Hedra --> DB3[(interview_call_analysis)]
    end
```

### Flow 6: Video Generation and Sharing

Experts generate shareable videos and avatar session links from the dashboard.

```mermaid
flowchart TB
    subgraph generation [Content Generation]
        HotTake[Generate Hot Take Video]
        ShareLink[Create Share Link]
    end
    
    subgraph hottake_flow [Hot Take Video]
        HotTake --> Edit[Edit hot take text]
        Edit --> API1[POST /generate-hot-take-video]
        API1 --> Job1[hot_take job]
        Job1 --> TTS[Generate audio with cloned voice]
        TTS --> Hedra[Render with Hedra avatar]
        Hedra --> Captions[Add captions]
        Captions --> Storage[(hot-take-videos bucket)]
    end
    
    subgraph share_flow [Share Link]
        ShareLink --> Token[Generate share token]
        Token --> DB[(share_links table)]
        DB --> URL[Shareable URL]
        URL --> Seeker[Seeker accesses avatar]
    end
```

## Backend Job Queue

The backend uses a Supabase-based job queue with a polling worker for async processing.

```mermaid
flowchart LR
    subgraph api [Flask API]
        Endpoint[Orchestrator Endpoints]
    end
    
    subgraph queue [Job Queue]
        Table[(post_call_jobs)]
    end
    
    subgraph worker [Background Worker]
        Poller[Job Poller]
        Processor[Job Processor]
    end
    
    Endpoint -->|INSERT job| Table
    Poller -->|claim_pending_job| Table
    Table -->|job data| Processor
    Processor -->|UPDATE status| Table
```

**Job Types:**

| Job Type | Description |
|----------|-------------|
| `post_call` | Full post-call processing (transcript, profile, avatar, voice) |
| `resume_session` | Append new conversation to existing session |
| `mail_session` | Process answered questions and approved intros |
| `message_notification` | Notify experts about new seeker messages |
| `hot_take` | Generate hot take video with avatar |
| `avatar_update` | Re-cartoonize and regenerate Hedra avatar |
| `voice_reclone` | Clone voice from new audio recording |

## Key Services

| Service | Location | Purpose |
|---------|----------|---------|
| `TranscriptProcessor` | `expert-avatar-backend/services/` | LLM analysis of call transcripts |
| `ProfileAggregator` | `expert-avatar-backend/services/` | Builds comprehensive user profiles |
| `VoiceCloningService` | `expert-avatar-backend/services/` | Clones voices via Inworld/Cartesia |
| `HedraAvatarGenerator` | `expert-avatar-backend/services/` | Creates video avatars via Hedra API |
| `CartoonGenerator` | `expert-avatar-backend/services/` | Transforms photos to illustrations |
| `EmailService` | `expert-avatar-backend/services/` | Sends notification emails via Resend |
| `MessageOrchestrator` | `expert-avatar-backend/services/` | Routes Q&A and intro requests |

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- LiveKit Cloud account
- Supabase project

### Environment Variables

Create a `.env.local` file with:

```bash
# LiveKit
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LIVEKIT_URL=

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
SUPABASE_S3_ACCESS_KEY_ID=
SUPABASE_S3_SECRET_ACCESS_KEY=

# AI Services
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Avatar & Voice
HEDRA_API_KEY=
INWORLD_API_KEY=
INWORLD_WORKSPACE_ID=

# Backend
BACKEND_URL=http://localhost:5001
BACKEND_API_KEY=
```

### Running Locally

```bash
# Install dependencies
uv sync

# Run the interview agent
uv run python agent.py dev

# In another terminal, run the backend
cd ../expert-avatar-backend
uv run python app.py

# Run the worker
uv run python worker.py
```

### Running Tests

```bash
uv run pytest
```

## Project Structure

```
expertavatar-interview-agent/
├── agent.py                 # Main agent entrypoint
├── prompts/
│   ├── interview_prompt.txt # Interview agent instructions
│   ├── mail_prompt.txt      # Mail agent instructions
│   └── onboarding_prompt.txt # Onboarding agent instructions
├── services/
│   ├── gender_detection.py  # Name-based gender detection for avatar selection
│   ├── message_data.py      # Seeker message data access
│   └── user_data.py         # User profile data access
└── utils.py                 # Prompt loading utilities
```

## Related Projects

- `expertavatar-interview-frontend/` - Next.js dashboard and session UI
- `expertavatar-chat-agent/` - Seeker-facing avatar chat agent
- `expert-avatar-backend/` - Flask API and background worker
