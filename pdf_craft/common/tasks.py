"""Small structured joins for independently ready business operations."""
import asyncio

from ..concurrency import fatal_error_boundary, settle_tasks


@fatal_error_boundary
async def joined(*operations):
    """Fail promptly and settle siblings, without wrapping the original error."""
    tasks = [asyncio.create_task(operation) for operation in operations]
    try:
        # gather observes failures while another branch is still waiting for IO.
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await settle_tasks(tasks)
