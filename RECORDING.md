# Interview Recording Setup

⚠️ **CURRENTLY DISABLED** - Supabase Storage's S3-compatible API is not working reliably with LiveKit Egress.

## Status

Recording is currently disabled in the agent code. To enable it, you need to:

1. Set up AWS S3, Cloudflare R2, or another S3-compatible storage provider
2. Update the S3 credentials in `egress_service.py`
3. Uncomment the recording code in `agent.py`

## Alternative: Use LiveKit's Default Storage

You can also record to LiveKit's default storage and download files later. See the "Alternative Approach" section below.

## How It Works

The agent uses LiveKit's Egress service to record conversations and upload them to your Supabase storage bucket named `interview-recordings`. Recordings are automatically started when a participant joins and stopped when the session ends.

## Prerequisites

1. **Supabase Storage Bucket**: Create a bucket called `interview-recordings` in your Supabase project
   - Run the SQL in the Supabase SQL editor (see below)

2. **Environment Variables**: Ensure these are set in your `.env.local` file:
   ```bash
   # LiveKit credentials (for egress API)
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=your-api-key
   LIVEKIT_API_SECRET=your-api-secret
   
   # Supabase credentials
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_SERVICE_KEY=your-service-role-key
   ```

## Supabase Storage Setup

Run this SQL in the Supabase SQL editor to create the storage bucket with proper policies:

```sql
-- Create the storage bucket for interview recordings
INSERT INTO storage.buckets (id, name, public)
VALUES ('interview-recordings', 'interview-recordings', false)
ON CONFLICT (id) DO NOTHING;

-- Allow authenticated users to upload recordings
CREATE POLICY "Allow authenticated users to upload recordings"
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (bucket_id = 'interview-recordings');

-- Allow users to update their own recordings (if needed)
CREATE POLICY "Allow authenticated users to update recordings"
ON storage.objects
FOR UPDATE
TO authenticated
USING (bucket_id = 'interview-recordings')
WITH CHECK (bucket_id = 'interview-recordings');

-- Allow users to read recordings
CREATE POLICY "Allow authenticated users to read recordings"
ON storage.objects
FOR SELECT
TO authenticated
USING (bucket_id = 'interview-recordings');

-- Allow service role to manage all recordings (for agent uploads)
CREATE POLICY "Allow service role full access to recordings"
ON storage.objects
FOR ALL
TO service_role
USING (bucket_id = 'interview-recordings')
WITH CHECK (bucket_id = 'interview-recordings');

-- Allow users to delete recordings (if needed)
CREATE POLICY "Allow authenticated users to delete recordings"
ON storage.objects
FOR DELETE
TO authenticated
USING (bucket_id = 'interview-recordings');
```

## Recording Modes

### Mixed Audio Recording

Records all audio in the room mixed together in a single mono/stereo track.

```python
egress_id = await egress_service.start_room_recording(
    room_name=ctx.room.name,
    user_id=user_id,
    audio_only=True,
    audio_mixing=None,  # or api.AudioMixing.DEFAULT_MIXING
)
```

**File location**: `interview-recordings/interviews/{user_id}/{room_name}.ogg`

### Dual-Channel Recording (Recommended for Interviews)

Records agent audio on the **left channel** and user audio on the **right channel** in a single stereo file. This makes it easy to separate speakers in post-processing tools like Adobe Audition, Audacity, or FFmpeg.

```python
from livekit import api

egress_id = await egress_service.start_room_recording(
    room_name=ctx.room.name,
    user_id=user_id,
    audio_only=True,
    audio_mixing=api.AudioMixing.DUAL_CHANNEL_AGENT,  # Agent=L, User=R
)
```

**File location**: `interview-recordings/interviews/{user_id}/{room_name}.ogg` (stereo with L/R channels)

**Benefits**:
- Single file (easier to manage)
- Preserves sync automatically
- Easy to split in post-processing
- Agent on left channel, all other participants on right channel

### Alternate Channel Recording

Each new audio track alternates between left and right channels. Useful for multi-participant recordings.

```python
egress_id = await egress_service.start_room_recording(
    room_name=ctx.room.name,
    user_id=user_id,
    audio_only=True,
    audio_mixing=api.AudioMixing.DUAL_CHANNEL_ALTERNATE,
)
```

## Splitting Dual-Channel Recordings

If you recorded with `DUAL_CHANNEL_AGENT`, you can split the stereo file into separate mono files for each speaker using FFmpeg:

```bash
# Extract left channel (agent) only
ffmpeg -i recording.ogg -map_channel 0.0.0 agent.ogg

# Extract right channel (user) only
ffmpeg -i recording.ogg -map_channel 0.0.1 user.ogg

# Or split into two mono files at once
ffmpeg -i recording.ogg -filter_complex "[0:a]channelsplit=channel_layout=stereo[left][right]" \
  -map "[left]" agent.ogg -map "[right]" user.ogg
```

## Accessing Recordings

Recordings are stored in Supabase storage and can be accessed:

1. **Via Supabase Dashboard**: Navigate to Storage → interview-recordings
2. **Via Supabase API**: Use the Supabase client to list and download recordings
3. **Via Public URL**: If you make recordings public, you can generate signed URLs

Example code to access recordings:

```python
from supabase import create_client

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# List recordings for a user
files = supabase.storage.from_('interview-recordings').list(f'interviews/{user_id}')

# Get a signed URL for a recording (expires in 1 hour)
url = supabase.storage.from_('interview-recordings').create_signed_url(
    f'interviews/{user_id}/{room_name}.ogg',
    expires_in=3600
)
```

## Troubleshooting

### Recording not starting

- Check that all environment variables are set correctly
- Verify the `interview-recordings` bucket exists in Supabase
- Check agent logs for error messages starting with "❌" or "⚠️"

### Recording not appearing in Supabase

- Verify your Supabase service role key has proper permissions
- Check that the storage policies allow service role access
- Look for egress errors in LiveKit Cloud dashboard

### Large file sizes

- Audio-only recordings are typically small (a few MB for a 30-minute call)
- If recording video, consider using lower quality presets or audio-only mode
- You can adjust encoding presets in `egress_service.py` (e.g., `H264_720P_30` → `H264_480P_30`)

## Cost Considerations

- **LiveKit Egress**: Charged based on egress minutes (check LiveKit pricing)
- **Storage**: Depends on provider (AWS S3, Cloudflare R2, etc.)
- **Recommendation**: Use audio-only recording unless video is required to minimize costs

## Alternative Approach: Download and Upload

Since Supabase S3 API doesn't work directly, here's an alternative:

### 1. Record to AWS S3 or Cloudflare R2

Update the S3 configuration in `egress_service.py`:

```python
# For AWS S3
s3=api.S3Upload(
    endpoint="",  # Leave empty for AWS
    bucket="your-bucket-name",
    region="us-east-1",
    access_key="YOUR_AWS_ACCESS_KEY",
    secret="YOUR_AWS_SECRET_KEY",
    force_path_style=False,
)

# Or for Cloudflare R2 (cheaper, free egress)
s3=api.S3Upload(
    endpoint="https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com",
    bucket="interview-recordings",
    region="auto",
    access_key="YOUR_R2_ACCESS_KEY",
    secret="YOUR_R2_SECRET_KEY",
    force_path_style=True,
)
```

### 2. Use Webhooks to Copy to Supabase

Set up a webhook endpoint that:
1. Receives notification when egress completes
2. Downloads the file from S3
3. Uploads it to Supabase Storage

This is more reliable than trying to use Supabase's S3 API directly.

## Recommended: Cloudflare R2

**Why R2?**
- Free egress (vs AWS S3 charges for downloads)
- S3-compatible API that works perfectly with LiveKit
- Cheap storage ($0.015/GB/month)
- Easy to set up

**Setup:**
1. Create a Cloudflare account
2. Go to R2 → Create Bucket
3. Generate API tokens
4. Update credentials in `egress_service.py`
5. Uncomment recording code in `agent.py`

