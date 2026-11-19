import asyncio
from types import SimpleNamespace

from core.routines.host import JarvisRoutineHost


class FakeSession:
    def __init__(self): self.sent=[]
    async def send_client_content(self, **kwargs): self.sent.append(kwargs)


def test_host_ready_and_idle():
    assistant=SimpleNamespace(session=object(), _awake=True, ui=SimpleNamespace(auth_locked=False), _last_user_speech=0)
    host=JarvisRoutineHost(assistant, loop_getter=lambda: None)
    assert host.ready()
    assert host.idle_seconds() >= 0
    assistant.ui.auth_locked=True
    assert not host.ready()


def test_host_delivers_on_event_loop_thread():
    async def run():
        session=FakeSession()
        assistant=SimpleNamespace(session=session, _awake=True, ui=SimpleNamespace(auth_locked=False), _last_user_speech=0)
        host=JarvisRoutineHost(assistant, loop_getter=lambda: asyncio.get_running_loop())
        result=await asyncio.to_thread(host.prompt, "routine prompt")
        assert result is None
        assert len(session.sent)==1
        assert session.sent[0]["turns"]["parts"][0]["text"]=="routine prompt"
    asyncio.run(run())
