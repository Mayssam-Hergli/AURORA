"""Thin MQTT wrapper (paho-mqtt v2) for every AURORA process.

    bus = Bus("cassandra-core", allowed_subscriptions=topics.CORE_ALLOWED_SUBSCRIPTIONS)
    bus.subscribe(topics.CORE_ALLOWED_SUBSCRIPTIONS[0], on_tm)      # handler(topic, model)
    bus.start()                                                      # connects; reconnects forever
    bus.publish(topics.state("B"), NodeStateMsg(...))                # QoS / retain from the contract

- Reconnects automatically and re-subscribes after every reconnect; logs each disconnect.
- QoS 1 publishes made while disconnected are queued by paho and sent after reconnecting
  (never silently dropped). QoS 0 telemetry published while disconnected is dropped and counted.
- Handlers run on paho's network thread: keep them short (append to a queue).
- Messages that fail validation go to on_invalid(topic, payload, error) and are counted, so a
  malformed command stays visible as evidence instead of vanishing.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable

import paho.mqtt.client as mqtt

from aurora_common import schemas, topics

log = logging.getLogger("aurora.bus")

Handler = Callable[[str, object], None]
InvalidHandler = Callable[[str, bytes, Exception], None]


class Bus:
    def __init__(
        self,
        client_id: str,
        host: str = "localhost",
        port: int = 1883,
        *,
        transport: str = "tcp",
        allowed_subscriptions: Iterable[str] | None = None,
        on_invalid: InvalidHandler | None = None,
        keepalive: int = 10,
    ):
        self.host, self.port, self.keepalive = host, port, keepalive
        self.allowed = None if allowed_subscriptions is None else tuple(allowed_subscriptions)
        self.on_invalid = on_invalid
        self.handlers: list[tuple[str, Handler, bool]] = []  # (filter, handler, parse)
        self.stats: Counter = Counter()
        self.connected = threading.Event()
        self._lock = threading.Lock()
        self._stopping = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, transport=transport)
        self.client.reconnect_delay_set(min_delay=1, max_delay=8)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ------------------------------------------------------------------ lifecycle
    def start(self, wait_s: float = 5.0) -> bool:
        """Connect in the background; returns True if connected within wait_s (it keeps trying anyway)."""
        self.client.connect_async(self.host, self.port, keepalive=self.keepalive)
        self.client.loop_start()
        ok = self.connected.wait(wait_s)
        if not ok:
            log.warning("broker %s:%s not reachable yet; retrying in the background", self.host, self.port)
        return ok

    def stop(self) -> None:
        self._stopping = True
        self.client.disconnect()
        self.client.loop_stop()

    @property
    def subscriptions(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(dict.fromkeys(f for f, _, _ in self.handlers))

    # ------------------------------------------------------------------ subscribe / publish
    def subscribe(self, filt: str, handler: Handler, *, parse: bool = True) -> None:
        """handler(topic, model) with parse=True, handler(topic, raw_bytes) with parse=False."""
        if self.allowed is not None and filt not in self.allowed:
            raise PermissionError(f"subscription {filt!r} is not in this process's allowed list")
        with self._lock:
            self.handlers.append((filt, handler, parse))
        if self.connected.is_set():
            self.client.subscribe(filt, qos=1)

    def publish(self, topic: str, msg, *, qos: int | None = None, retain: bool | None = None):
        """Publish a schema model (validated against the topic) or raw bytes."""
        if isinstance(msg, (bytes, str)):
            payload = msg.encode() if isinstance(msg, str) else msg
        else:
            payload = schemas.dump(msg)
            schemas.parse(topic, payload)  # never put a message on the bus that its topic rejects
        qos = topics.qos_of(topic) if qos is None else qos
        retain = topics.retain_of(topic) if retain is None else retain
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        if info.rc == mqtt.MQTT_ERR_NO_CONN and qos == 0:
            self.stats["dropped_qos0"] += 1
        else:
            self.stats["published"] += 1
        return info

    # ------------------------------------------------------------------ paho callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("connect refused by %s:%s: %s", self.host, self.port, reason_code)
            return
        self.stats["connects"] += 1
        for filt in self.subscriptions:
            client.subscribe(filt, qos=1)
        self.connected.set()
        log.info("connected to %s:%s (%d subscriptions)", self.host, self.port, len(self.subscriptions))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        if self._stopping:
            return
        self.stats["disconnects"] += 1
        log.warning("disconnected from %s:%s (%s); reconnecting", self.host, self.port, reason_code)

    def _on_message(self, client, userdata, message):
        self.stats["received"] += 1
        with self._lock:
            targets = [(h, p) for f, h, p in self.handlers if topics.matches(f, message.topic)]
        parsed, error = None, None
        for handler, want_parsed in targets:
            if not want_parsed:
                handler(message.topic, message.payload)
                continue
            if parsed is None and error is None:
                try:
                    parsed = schemas.parse(message.topic, message.payload)
                except (ValueError, KeyError) as exc:
                    error = exc
                    self.stats["invalid"] += 1
                    log.debug("invalid message on %s: %s", message.topic, exc)
                    if self.on_invalid:
                        self.on_invalid(message.topic, message.payload, exc)
            if parsed is not None:
                handler(message.topic, parsed)
