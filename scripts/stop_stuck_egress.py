#!/usr/bin/env python3
"""
Quick script to stop stuck egress recordings.
Usage: uv run stop_stuck_egress.py EG_24jU9bpZanhD
"""

import asyncio
import os
import sys
from dotenv import load_dotenv
from livekit import api

# Load environment variables
load_dotenv(".env.local")

async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run stop_stuck_egress.py <egress_id>")
        sys.exit(1)
    
    egress_id = sys.argv[1]
    print(f"Stopping egress: {egress_id}")
    
    try:
        lkapi = api.LiveKitAPI()
        request = api.StopEgressRequest(egress_id=egress_id)
        await lkapi.egress.stop_egress(request)
        await lkapi.aclose()
        print(f"✅ Successfully stopped egress {egress_id}")
    except Exception as e:
        print(f"❌ Failed to stop egress {egress_id}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

