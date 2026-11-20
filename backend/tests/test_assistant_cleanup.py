"""Assistant housekeeping: old screenshots leave the DB, idle chat locks go."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from tests.test_assistant_b2 import _enable, _mk


def test_old_screenshots_are_removed_and_idle_locks_pruned(client, admin_user, auth_headers, session):
    from app.assistant import agent, cleanup
    from app.models import AssistantConversation, AssistantMessage
    _enable(client, auth_headers)
    assert client.patch("/api/assistant/settings", json={"screenshot_days": 0},
                        headers=auth_headers).status_code == 400
    assert client.patch("/api/assistant/settings", json={"screenshot_days": 3},
                        headers=auth_headers).json()["screenshot_days"] == 3
    conv = _mk(session, AssistantConversation, title="c")
    img = [{"media_type": "image/png", "data": "AAAA"}]
    old = _mk(session, AssistantMessage, conversation_id=conv.id, role="user",
              content=json.dumps({"role": "user", "content": "old", "images": img * 2}),
              created_at=datetime.now(tz=timezone.utc) - timedelta(days=5))
    new = _mk(session, AssistantMessage, conversation_id=conv.id, role="user",
              content=json.dumps({"role": "user", "content": "new", "images": img}))

    agent._lock(conv.id)            # idle lock
    busy = agent._lock(10_000)

    async def hold_and_run():
        async with busy:
            return await cleanup.run_once()
    res = asyncio.run(hold_and_run())
    assert res["screenshots_removed_from"] == 1
    assert conv.id not in agent._locks and 10_000 in agent._locks
    agent._locks.pop(10_000, None)

    session.expire_all()
    assert "images" not in json.loads(session.get(AssistantMessage, old.id).content)
    assert json.loads(session.get(AssistantMessage, new.id).content)["images"] == img
    items = client.get(f"/api/assistant/conversations/{conv.id}", headers=auth_headers).json()["items"]
    assert items[0]["images_removed"] == 2 and "images" not in items[0] and items[1]["images"]
