import anyio
from supabase import create_client, Client
from app.config import settings

# Unified admin level coordinator client
supabase_client: Client = create_client(
    settings.SUPABASE_URL,
    settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
)


class AsyncDBPool:
    """Thread containment layer isolating blocking database operational 
    roundtrips cleanly from the async engine thread loop.
    """

    @staticmethod
    async def execute(func, *args, **kwargs):
        return await anyio.to_thread.run_sync(func, *args, **kwargs)


db = AsyncDBPool()
