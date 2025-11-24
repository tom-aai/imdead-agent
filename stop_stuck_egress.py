#!/usr/bin/env python3
"""
Quick script to stop stuck egress recordings.
Usage: uv run stop_stuck_egress.py EG_24jU9bpZanhD
"""

import asyncio
import sys
from dotenv import load_dotenv
from services.egress_service import EgressService

# Load environment variables
load_dotenv(".env.local")

async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run stop_stuck_egress.py <egress_id>")
        sys.exit(1)
    
    egress_id = sys.argv[1]
    print(f"Stopping egress: {egress_id}")
    
    egress_service = EgressService()
    success = await egress_service.stop_recording(egress_id)
    
    if success:
        print(f"✅ Successfully stopped egress {egress_id}")
    else:
        print(f"❌ Failed to stop egress {egress_id}")
    
    await egress_service.close()

if __name__ == "__main__":
    asyncio.run(main())

