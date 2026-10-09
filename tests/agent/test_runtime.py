import asyncio

import pytest

from pybot.services.agent.runtime import AgentRuntime


@pytest.mark.asyncio
async def test_same_user_serialized_other_user_can_progress_and_queue_is_cleaned():
    runtime = AgentRuntime()
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first():
        async with runtime.for_user(11):
            order.append("first-start")
            entered.set()
            await release.wait()
            order.append("first-response-sent")

    async def second():
        async with runtime.for_user(11):
            order.append("second-start")

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(second())
    async with runtime.for_user(12):
        order.append("other-user")
    assert order == ["first-start", "other-user"]
    release.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first-start", "other-user", "first-response-sent", "second-start"]
    assert runtime.queues == {}


@pytest.mark.asyncio
async def test_cancelled_waiter_and_exception_release_queue():
    runtime = AgentRuntime()
    async with runtime.for_user(11):
        waiter = asyncio.create_task(_enter(runtime))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    assert runtime.queues == {}
    with pytest.raises(RuntimeError):
        async with runtime.for_user(11):
            raise RuntimeError("failed")
    assert runtime.queues == {}


async def _enter(runtime):
    async with runtime.for_user(11):
        pass
