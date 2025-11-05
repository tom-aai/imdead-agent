# Expert Avatar Interview

A multi-agent voice AI interview platform built with LiveKit Agents, Next.js, and Supabase. Users authenticate via LinkedIn OAuth and interact with AI-powered avatar agents for conversational interviews.

## Architecture

This project consists of three main components:

1. **Frontend (`expertavatar-interview-frontend/`)** - Next.js app with LiveKit integration and Supabase authentication
2. **Agent (`expertavatar-interview-agent/`)** - Python-based LiveKit voice agent with avatar support
3. **Backend (`expert-avatar-backend/`)** - Flask API server for additional backend operations

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   Frontend  │────────▶│   Supabase  │         │  LiveKit    │
│   Next.js   │         │   Auth +    │         │   Cloud     │
│             │◀────────│   Storage   │         │             │
└──────┬──────┘         └─────────────┘         └──────┬──────┘
       │                                                 │
       │                                                 │
       │                ┌─────────────┐                 │
       └───────────────▶│   Agent     │◀────────────────┘
                        │   Python    │
                        └─────────────┘
```

## Prerequisites

Before setting up this project, ensure you have:

- **Node.js** (v18+) and **pnpm** installed
- **Python** 3.13+ and **uv** package manager (`pip install uv`)
- **LiveKit Cloud** account ([livekit.io](https://livekit.io))
- **Supabase** account ([supabase.com](https://supabase.com))
- **LinkedIn Developer** account for OAuth setup
- **OpenAI API** key (for GPT-4.1)
- **Hedra API** key (for avatar sessions)
- **Cartesia API** key (for TTS)
- **AssemblyAI API** key (for STT)

## Supabase Setup

### 1. Create a Supabase Project

1. Go to [supabase.com](https://supabase.com) and create a new project
2. Note down your:
   - **Project URL** (found in Settings → API)
   - **Anon/Public Key** (found in Settings → API)
   - **Service Role Key** (found in Settings → API - keep this secret!)

### 2. Configure LinkedIn OAuth

#### Step 1: Create LinkedIn OAuth App

1. Go to [LinkedIn Developers](https://www.linkedin.com/developers/apps)
2. Click **Create app**
3. Fill in the required information:
   - **App name**: Expert Avatar Interview (or your preferred name)
   - **LinkedIn Page**: Your company page
   - **Privacy policy URL**: Your privacy policy
   - **App logo**: Upload a logo (optional)
4. Click **Create app**
5. Navigate to the **Auth** tab
6. Note your **Client ID** and **Client Secret**

#### Step 2: Configure OAuth Redirect URLs

In your LinkedIn app's **Auth** tab, add the following **Authorized redirect URLs**:

```
https://YOUR_SUPABASE_PROJECT_ID.supabase.co/auth/v1/callback
```

For local development, also add:

```
http://localhost:54321/auth/v1/callback
```

**Important**: Replace `YOUR_SUPABASE_PROJECT_ID` with your actual Supabase project reference ID (found in your project settings).

#### Step 3: Request OAuth Scopes

In the **Products** tab of your LinkedIn app:
1. Click **Request access** for "Sign In with LinkedIn using OpenID Connect"
2. This will give you access to the following scopes:
   - `openid`
   - `profile`
   - `email`

#### Step 4: Configure LinkedIn Provider in Supabase

1. In your Supabase project dashboard, go to **Authentication** → **Providers**
2. Scroll down and find **LinkedIn**
3. Enable the LinkedIn provider
4. Enter your LinkedIn app credentials:
   - **Client ID**: From your LinkedIn app
   - **Client Secret**: From your LinkedIn app
5. **Callback URL** should auto-populate (it should match what you configured in LinkedIn)
6. Click **Save**

#### Step 5: Update Allowed Redirect URLs (Frontend)

In Supabase, go to **Authentication** → **URL Configuration**:

1. Add your frontend URL to **Site URL**:
   ```
   http://localhost:3000
   ```

2. Add to **Redirect URLs**:
   ```
   http://localhost:3000/**
   http://localhost:3000/auth/callback
   ```

For production, add your production domain:
   ```
   https://yourdomain.com
   https://yourdomain.com/**
   https://yourdomain.com/auth/callback
   ```

### 3. Configure Database Tables

#### Create the `calls` Table

Run this SQL in the Supabase SQL Editor (**SQL Editor** → **New query**):

```sql
-- Create calls table for storing conversation history
CREATE TABLE IF NOT EXISTS public.calls (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
  session_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  picture_id TEXT DEFAULT '',
  user_id UUID REFERENCES auth.users(id),
  user_firstname TEXT DEFAULT '',
  user_lastname TEXT DEFAULT '',
  timestamp TEXT NOT NULL,
  usage JSONB DEFAULT '{}'::jsonb,
  conversation JSONB DEFAULT '{}'::jsonb
);

-- Create index on user_id for faster lookups
CREATE INDEX IF NOT EXISTS idx_calls_user_id ON public.calls(user_id);

-- Create index on session_id
CREATE INDEX IF NOT EXISTS idx_calls_session_id ON public.calls(session_id);

-- Create index on created_at for sorting
CREATE INDEX IF NOT EXISTS idx_calls_created_at ON public.calls(created_at DESC);

-- Enable Row Level Security
ALTER TABLE public.calls ENABLE ROW LEVEL SECURITY;

-- Create policy: Users can read their own calls
CREATE POLICY "Users can view their own calls"
  ON public.calls
  FOR SELECT
  USING (auth.uid() = user_id);

-- Create policy: Service role can insert (agent uses service key)
CREATE POLICY "Service role can insert calls"
  ON public.calls
  FOR INSERT
  WITH CHECK (true);

-- Grant permissions
GRANT SELECT ON public.calls TO authenticated;
GRANT INSERT ON public.calls TO service_role;
```

#### Create Storage Bucket for Avatar Images

Run this SQL to create the storage bucket:

```sql
-- Create a storage bucket for avatar images
INSERT INTO storage.buckets (id, name, public)
VALUES ('avatar-images', 'avatar-images', true)
ON CONFLICT (id) DO NOTHING;

-- Allow authenticated users to upload their own avatar images
CREATE POLICY "Authenticated users can upload avatar images"
  ON storage.objects
  FOR INSERT
  TO authenticated
  WITH CHECK (bucket_id = 'avatar-images');

-- Allow authenticated users to read avatar images
CREATE POLICY "Anyone can view avatar images"
  ON storage.objects
  FOR SELECT
  TO public
  USING (bucket_id = 'avatar-images');

-- Allow authenticated users to update their own avatar images
CREATE POLICY "Authenticated users can update avatar images"
  ON storage.objects
  FOR UPDATE
  TO authenticated
  USING (bucket_id = 'avatar-images')
  WITH CHECK (bucket_id = 'avatar-images');
```

Alternatively, you can create the bucket manually:
1. Go to **Storage** in the Supabase dashboard
2. Click **New bucket**
3. Name it `avatar-images`
4. Set it as **Public bucket**
5. Click **Create bucket**

Then configure the policies in the **Policies** tab.

## Environment Variables

### Frontend (`expertavatar-interview-frontend/.env.local`)

Create a `.env.local` file in the frontend directory:

```bash
# Supabase
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key

# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Agent Configuration
NEXT_PUBLIC_DEFAULT_AGENT_NAME=your-agent-name
```

### Agent (`expertavatar-interview-agent/.env.local`)

Create a `.env.local` file in the agent directory:

```bash
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Supabase (for storing conversation history)
SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
SUPABASE_SERVICE_KEY=your_supabase_service_role_key

# OpenAI
OPENAI_API_KEY=your_openai_api_key

# Hedra (for avatar)
HEDRA_API_KEY=your_hedra_api_key

# Cartesia (for TTS)
CARTESIA_API_KEY=your_cartesia_api_key

# AssemblyAI (for STT)
ASSEMBLYAI_API_KEY=your_assemblyai_api_key
```

### Backend (`expert-avatar-backend/.env.local`)

Create a `.env.local` file in the backend directory:

```bash
# Server Configuration
PORT=5001

# Add any additional backend-specific environment variables here
```

## Installation

### 1. Install Frontend Dependencies

```bash
cd expertavatar-interview-frontend
pnpm install
```

### 2. Install Agent Dependencies

```bash
cd ../expertavatar-interview-agent
uv sync
```

### 3. Install Backend Dependencies

```bash
cd ../expert-avatar-backend
uv sync
```

## Running the Application

You'll need three terminal windows to run all components:

### Terminal 1: Frontend

```bash
cd expertavatar-interview-frontend
pnpm dev
```

The frontend will be available at [http://localhost:3000](http://localhost:3000)

### Terminal 2: Agent

```bash
cd expertavatar-interview-agent
uv run python agent.py dev
```

The agent will connect to LiveKit and wait for participant connections.

### Terminal 3: Backend (Optional)

```bash
cd expert-avatar-backend
uv run python app.py
```

The backend will be available at [http://localhost:5001](http://localhost:5001)

## Testing the Setup

### 1. Verify Supabase Authentication

1. Open [http://localhost:3000](http://localhost:3000)
2. Click **Sign in with LinkedIn**
3. Complete the OAuth flow
4. You should be redirected back to the app and see your profile information

### 2. Test Voice Agent Connection

1. After logging in, the dashboard should load
2. Click to start a conversation with the agent
3. Grant microphone permissions when prompted
4. You should see the avatar and be able to have a conversation

### 3. Verify Data Storage

1. After a conversation, check the Supabase dashboard
2. Go to **Table Editor** → **calls**
3. You should see a new row with your conversation data
4. Verify that `user_id`, `user_firstname`, and `user_lastname` are populated

### 4. Check Local Logs

In the agent terminal, you should see logs like:

```
==================================================
👤 USER CONNECTED TO AGENT
==================================================
User ID: abc123-def456-ghi789
Name: John Doe
First Name: John
Last Name: Doe
Email: john@example.com
==================================================
```

## Project Structure

```
expertavatar-interview/
├── README.md                           # This file
├── USER_DATA_FLOW.md                   # Documentation on user data flow
├── AGENTS.md                           # LiveKit Agents development guide
│
├── expertavatar-interview-frontend/    # Next.js Frontend
│   ├── app/                           # Next.js app directory
│   │   ├── api/                      # API routes
│   │   │   └── connection-details/   # LiveKit connection token generation
│   │   ├── (app)/                    # Main app pages
│   │   ├── auth/                     # Auth callback handling
│   │   └── login/                    # Login page
│   ├── components/                    # React components
│   │   ├── livekit/                  # LiveKit-specific components
│   │   └── ui/                       # Reusable UI components
│   ├── hooks/                         # Custom React hooks
│   ├── lib/                           # Utilities
│   │   ├── supabase.ts               # Supabase client
│   │   └── types.ts                  # TypeScript types
│   └── .env.local                     # Environment variables (create this)
│
├── expertavatar-interview-agent/       # Python Agent
│   ├── agent.py                       # Main agent entry point
│   ├── agents/                        # LiveKit Agents SDK (submodule)
│   ├── prompts/                       # System prompts
│   │   ├── onboarding_prompt.txt     # Main agent instructions
│   │   └── welcome_prompt.txt        # Welcome message
│   ├── chat_history/                  # Saved conversation logs (created automatically)
│   ├── utils.py                       # Utility functions
│   ├── pyproject.toml                 # Python dependencies
│   └── .env.local                     # Environment variables (create this)
│
└── expert-avatar-backend/              # Flask Backend
    ├── app.py                         # Main Flask application
    ├── pyproject.toml                 # Python dependencies
    └── .env.local                     # Environment variables (create this)
```

## Development Tips

### Formatting and Linting

**Frontend:**
```bash
cd expertavatar-interview-frontend
pnpm format          # Format with Prettier
pnpm lint            # Lint with ESLint
```

**Agent:**
```bash
cd expertavatar-interview-agent
uv run ruff format   # Format Python code
uv run ruff check    # Lint Python code
```

### Testing the Agent

```bash
cd expertavatar-interview-agent
uv run pytest        # Run agent tests
```

### Viewing Logs

- **Frontend**: Check browser console and terminal
- **Agent**: All logs appear in the terminal where you ran `agent.py`
- **Backend**: Check the terminal where you ran `app.py`

### Common Issues

#### LinkedIn OAuth Not Working

- Verify that your redirect URLs in LinkedIn match exactly with Supabase
- Check that you have the correct Client ID and Secret in Supabase
- Ensure "Sign In with LinkedIn using OpenID Connect" is approved

#### Agent Not Receiving User Data

- Check that the `Authorization` header is being sent from frontend
- Verify Supabase session is active (check browser console)
- Look for authentication errors in the agent terminal

#### Database Insert Failing

- Verify that your `SUPABASE_SERVICE_KEY` is correct (not the anon key)
- Check that the `calls` table exists and has the correct schema
- Review RLS policies to ensure service role can insert

#### Avatar Images Not Uploading

- Verify the `avatar-images` bucket exists in Supabase Storage
- Check that the bucket is set to **public**
- Review storage policies to ensure authenticated users can upload

## LiveKit Agents Documentation

For more information about building with LiveKit Agents, you can use the LiveKit Documentation MCP server:

[![Install MCP Server](https://cursor.com/deeplink/mcp-install-light.svg)](https://cursor.com/en-US/install-mcp?name=livekit-docs&config=eyJ1cmwiOiJodHRwczovL2RvY3MubGl2ZWtpdC5pby9tY3AifQ%3D%3D)

Or visit the [LiveKit Agents documentation](https://docs.livekit.io/agents/).

## Resources

- [LiveKit Documentation](https://docs.livekit.io/)
- [Supabase Documentation](https://supabase.com/docs)
- [Next.js Documentation](https://nextjs.org/docs)
- [LinkedIn OAuth Documentation](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authentication)

## License

See individual component licenses in their respective directories.

