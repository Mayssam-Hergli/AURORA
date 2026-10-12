"""Bus round trip through a real broker. Skipped when no broker listens on localhost:1883 (run `make up`)."""

import socket
import threading
import time
import uuid

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from aurora_common.bus import Bus


def _broker_up(host="localhost", port=1883) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _broker_up(), reason="no MQTT broker on localhost:1883 (make up)")


def test_publish_subscribe_and_invalid_messages_are_reported():
    got, invalid, done = [], [], threading.Event()
    rx = Bus(
        f"test-rx-{uuid.uuid4().hex[:6]}",
        allowed_subscriptions=T.CORE_ALLOWED_SUBSCRIPTIONS,
        on_invalid=lambda topic, payload, exc: invalid.append(topic),
    )
    rx.subscribe(T.CORE_ALLOWED_SUBSCRIPTIONS[1], lambda topic, msg: (got.append(msg), done.set()))
    with pytest.raises(PermissionError):
        rx.subscribe(T.GT_EVENT, lambda *_: None)  # runtime half of gt isolation
    assert rx.start()
    assert rx.subscriptions == (T.CORE_ALLOWED_SUBSCRIPTIONS[1],)
    tx = Bus(f"test-tx-{uuid.uuid4().hex[:6]}")
    assert tx.start()
    tx.publish(T.tc("K"), b'{"v":1,"id":"x","t":1.0,"sat":"K","origin":"martian","cmd":"PING","args":{}}')
    tc = S.Telecommand(id=uuid.uuid4().hex, t=time.time(), sat="K", origin="core", cmd="SAFE_MODE")
    tx.publish(T.tc("K"), tc)
    assert done.wait(5)
    time.sleep(0.3)
    rx.stop()
    tx.stop()
    assert got == [tc]
    assert invalid == [T.tc("K")]


def test_publish_refuses_a_model_its_topic_rejects():
    tx = Bus(f"test-tx-{uuid.uuid4().hex[:6]}")
    msg = S.NodeStateMsg(sat="B", t=1.0, state="nominal")
    with pytest.raises(ValueError):
        tx.publish(T.state("C"), msg)
