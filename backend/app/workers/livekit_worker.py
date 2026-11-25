"""Process entry for the LiveKit worker. Run: python -m app.workers.livekit_worker dev"""

from app.voice.livekit_agent import main

if __name__ == "__main__":
    main()
