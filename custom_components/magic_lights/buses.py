"""Classify a light by the shared network ("bus") its commands travel over.

Lights on one bus share its rate limits. Bus ids look like
`zigbee:z2m:<ieee>`, `hue:<bridge id>`, `zwave:<home id>`, `thread:<xpan>`,
`ip:<domain>:<id>`, `bridge:…`, `yeelight:…`, `cloud:…` or `entity:<entity_id>`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import IntegrationNotLoaded, async_get_loaded_integration

from .models import Bus, BusLimits


class BusKind(StrEnum):
    """Kind of shared transport; selects the default rate limits."""

    ZIGBEE = "zigbee"  # ZHA, Zigbee2MQTT, deCONZ coordinator
    HUE = "hue"  # Hue bridge REST API (Zigbee behind it)
    ZWAVE = "zwave"
    THREAD = "thread"  # one Thread network (by extended PAN id)
    BRIDGE = "bridge"  # opaque vendor bridge (Matter/HomeKit/MQTT/other hub)
    IP = "ip"  # one Wi-Fi/Ethernet endpoint
    YEELIGHT = "yeelight"  # IP, but with a 60 commands/min quota
    BLE = "ble"
    CLOUD = "cloud"
    UNKNOWN = "unknown"


# Only the Zigbee figures are sourced (Z2M docs); the rest are conservative
# heuristics. Bursts hold about two seconds of traffic.
BUS_DEFAULTS: Final[Mapping[BusKind, BusLimits]] = {
    BusKind.ZIGBEE: BusLimits(rate=10.0, burst=20, min_interval=0.5),
    BusKind.HUE: BusLimits(rate=10.0, burst=20, min_interval=0.4),
    BusKind.ZWAVE: BusLimits(rate=4.0, burst=8, min_interval=1.0),
    BusKind.THREAD: BusLimits(rate=10.0, burst=20, min_interval=0.3),
    BusKind.BRIDGE: BusLimits(rate=10.0, burst=20, min_interval=0.5),
    BusKind.IP: BusLimits(rate=20.0, burst=40, min_interval=0.1),
    BusKind.YEELIGHT: BusLimits(rate=1.0, burst=2, min_interval=1.0),
    BusKind.BLE: BusLimits(rate=3.0, burst=6, min_interval=0.5),
    BusKind.CLOUD: BusLimits(rate=1.0, burst=2, min_interval=1.5),
    BusKind.UNKNOWN: BusLimits(rate=2.0, burst=4, min_interval=0.5),
}


def bus_kind(bus_id: str) -> BusKind:
    """Return the kind encoded in a bus id's prefix."""
    try:
        return BusKind(bus_id.split(":", 1)[0])
    except ValueError:
        return BusKind.UNKNOWN


def _bus(bus_id: str, kind: BusKind, label: str) -> Bus:
    return Bus(bus_id, kind, label, BUS_DEFAULTS[kind])


def _unknown(entity_id: str) -> Bus:
    return _bus(f"entity:{entity_id}", BusKind.UNKNOWN, entity_id)


def _ident(device: dr.DeviceEntry, domain: str, prefix: str = "") -> str | None:
    return next(
        (v for d, v in device.identifiers if d == domain and v.startswith(prefix)),
        None,
    )


def _root(dev_reg: dr.DeviceRegistry, device: dr.DeviceEntry) -> dr.DeviceEntry:
    """Follow via_device_id to the top-most parent (bridge or coordinator)."""
    seen: set[str] = set()
    while device.via_device_id and device.id not in seen:
        seen.add(device.id)
        if (parent := dev_reg.async_get(device.via_device_id)) is None:
            break
        device = parent
    return device


def _name(device: dr.DeviceEntry | None, fallback: str) -> str:
    if device is None:
        return fallback
    return device.name_by_user or device.name or fallback


@dataclass(slots=True)
class _Ctx:
    hass: HomeAssistant
    ent_reg: er.EntityRegistry
    dev_reg: dr.DeviceRegistry
    entity: er.RegistryEntry
    entry: ConfigEntry | None
    device: dr.DeviceEntry | None


# ---- rules for integrations whose network is known; all need a config entry


def _zha(c: _Ctx, entry: ConfigEntry) -> Bus:
    # unique_id is "epid=<extended pan id>"
    return _bus(
        f"zigbee:zha:{entry.unique_id or entry.entry_id}",
        BusKind.ZIGBEE,
        f"Zigbee (ZHA) - {entry.title}",
    )


def _deconz(c: _Ctx, entry: ConfigEntry) -> Bus:
    # unique_id is the gateway's bridge id
    return _bus(
        f"zigbee:deconz:{entry.unique_id or entry.entry_id}",
        BusKind.ZIGBEE,
        f"Zigbee (deCONZ) - {entry.title}",
    )


def _hue(c: _Ctx, entry: ConfigEntry) -> Bus:
    # unique_id is the bridge id
    return _bus(
        f"hue:{entry.unique_id or entry.entry_id}",
        BusKind.HUE,
        f"Hue bridge - {entry.title}",
    )


def _mqtt(c: _Ctx, entry: ConfigEntry) -> Bus:
    if (dev := c.device) is None:
        return _bus(f"ip:mqtt:{c.entity.entity_id}", BusKind.IP, c.entity.entity_id)
    root = _root(c.dev_reg, dev)
    if bridge := _ident(root, "mqtt", "zigbee2mqtt_bridge_"):
        ieee = bridge.removeprefix("zigbee2mqtt_bridge_")
        return _bus(
            f"zigbee:z2m:{ieee}", BusKind.ZIGBEE, f"Zigbee2MQTT - {_name(root, ieee)}"
        )
    if _ident(dev, "mqtt", "zigbee2mqtt_"):  # bridge device not linked (yet)
        return _bus("zigbee:z2m:unknown", BusKind.ZIGBEE, "Zigbee2MQTT")
    if zw := _ident(dev, "mqtt", "zwavejs2mqtt_"):  # Z-Wave JS UI discovery
        home = zw.removeprefix("zwavejs2mqtt_").split("_node", 1)[0]  # "0x<hex>"
        try:
            return _bus(f"zwave:{int(home, 16)}", BusKind.ZWAVE, f"Z-Wave {home}")
        except ValueError:
            pass
    if root.id != dev.id:
        return _bus(
            f"bridge:mqtt:{root.id}", BusKind.BRIDGE, _name(root, "MQTT bridge")
        )
    return _bus(f"ip:mqtt:{dev.id}", BusKind.IP, _name(dev, c.entity.entity_id))


def _zwave(c: _Ctx, entry: ConfigEntry) -> Bus:
    home = None
    if c.device and (ident := _ident(c.device, "zwave_js")):
        home = ident.split("-", 1)[0]  # "<home_id>-<node_id>[-mfr:type:id]"
    # The entry's unique_id is str(home_id), so Z-Wave JS UI lands on the same bus.
    home = home or entry.unique_id or entry.entry_id
    return _bus(f"zwave:{home}", BusKind.ZWAVE, f"Z-Wave - {entry.title}")


def _matter_network(c: _Ctx, device: dr.DeviceEntry) -> tuple[str, str | None]:
    """Return ("thread", xpan | None), ("ip", None) or ("unknown", None)."""
    try:
        return _matter_probe(c.hass, device)
    except Exception:  # noqa: BLE001 - Matter not loaded, server down, node unknown
        pass
    # Matter creates (disabled by default) diagnostic sensors per network type.
    for ent in er.async_entries_for_device(
        c.ent_reg, device.id, include_disabled_entities=True
    ):
        if ent.platform != "matter":
            continue
        if ent.translation_key in (
            "thread_channel",
            "thread_routing_role",
            "thread_network_name",
        ):
            return "thread", None
        if ent.translation_key == "wifi_rssi":
            return "ip", None
    return "unknown", None


def _matter_probe(
    hass: HomeAssistant, device: dr.DeviceEntry
) -> tuple[str, str | None]:
    """Read the network interfaces from the live node; raise when there is none."""
    from chip.clusters import Objects as clusters  # noqa: PLC0415

    from homeassistant.components.matter.helpers import (  # noqa: PLC0415
        get_node_from_device_entry,
    )

    if (node := get_node_from_device_entry(hass, device)) is None:
        raise LookupError(device.id)
    diag = clusters.GeneralDiagnostics
    iface_type = diag.Enums.InterfaceTypeEnum
    for iface in (
        node.get_attribute_value(0, None, diag.Attributes.NetworkInterfaces) or ()
    ):
        if not iface.isOperational:
            continue
        if iface.type == iface_type.kThread:
            xpan = node.get_attribute_value(
                0, None, clusters.ThreadNetworkDiagnostics.Attributes.ExtendedPanId
            )
            return "thread", (f"{xpan:016x}" if isinstance(xpan, int) else None)
        if iface.type in (iface_type.kWiFi, iface_type.kEthernet):
            return "ip", None
    commissioning = clusters.NetworkCommissioning
    feature = commissioning.Bitmaps.Feature
    fmap = node.get_attribute_value(0, None, commissioning.Attributes.FeatureMap) or 0
    if fmap & feature.kThreadNetworkInterface:
        return "thread", None
    if fmap & (feature.kWiFiNetworkInterface | feature.kEthernetNetworkInterface):
        return "ip", None
    raise LookupError(device.id)


def _thread_bus(xpan: str | None) -> Bus:
    xpan = xpan or "default"
    return _bus(f"thread:{xpan}", BusKind.THREAD, f"Thread {xpan}")


def _matter(c: _Ctx, entry: ConfigEntry) -> Bus:
    if (dev := c.device) is None:
        return _unknown(c.entity.entity_id)
    if dev.via_device_id and (bridge := c.dev_reg.async_get(dev.via_device_id)):
        # A bridged endpoint (Hue/Aqara/IKEA hub exposed via Matter) shares the
        # bridge's downstream network.
        return _bus(
            f"bridge:matter:{bridge.id}",
            BusKind.BRIDGE,
            f"Matter bridge - {_name(bridge, 'bridge')}",
        )
    net, xpan = _matter_network(c, dev)
    if net == "thread":
        return _thread_bus(xpan)
    kind = BusKind.IP if net == "ip" else BusKind.UNKNOWN
    return _bus(f"{kind}:matter:{dev.id}", kind, _name(dev, "Matter device"))


def _homekit_controller(c: _Ctx, entry: ConfigEntry) -> Bus:
    transport = entry.data.get("Connection", "IP")  # "IP" | "BLE" | "CoAP"
    if transport == "CoAP":  # Thread; HomeKit exposes no PAN id
        return _thread_bus(None)
    if transport == "BLE":
        return _bus(
            f"ble:homekit_controller:{entry.entry_id}", BusKind.BLE, entry.title
        )
    if c.device and c.device.via_device_id:  # accessory behind a HomeKit bridge
        return _bus(
            f"bridge:homekit_controller:{entry.entry_id}",
            BusKind.BRIDGE,
            f"HomeKit bridge - {entry.title}",
        )
    return _bus(f"ip:homekit_controller:{entry.entry_id}", BusKind.IP, entry.title)


def _yeelight(c: _Ctx, entry: ConfigEntry) -> Bus:
    if entry.options.get("use_music_mode"):  # music mode lifts the quota
        return _bus(f"ip:yeelight:{entry.entry_id}", BusKind.IP, entry.title)
    return _bus(f"yeelight:{entry.entry_id}", BusKind.YEELIGHT, entry.title)


def _switch_as_x(c: _Ctx, entry: ConfigEntry) -> Bus:
    wrapped = (c.entity.options.get("switch_as_x") or {}).get(ATTR_ENTITY_ID)
    if wrapped is None and (source := entry.options.get(ATTR_ENTITY_ID)):
        wrapped = er.async_resolve_entity_id(c.ent_reg, source)
    if wrapped:
        return async_classify(c.hass, wrapped)
    return _unknown(c.entity.entity_id)


def _generic(c: _Ctx) -> Bus:
    domain = c.entity.platform
    try:
        integration = async_get_loaded_integration(c.hass, domain)
    except IntegrationNotLoaded:
        iot_class, integration_type = "", None
    else:
        iot_class = integration.iot_class or ""
        integration_type = integration.integration_type
    entry = c.entry
    if entry and iot_class.startswith("cloud"):
        return _bus(
            f"cloud:{domain}:{entry.entry_id}", BusKind.CLOUD, f"{entry.title} (cloud)"
        )
    if (dev := c.device) is not None:
        connections = {kind for kind, _ in dev.connections}
        if dr.CONNECTION_ZIGBEE in connections and entry:
            return _bus(
                f"zigbee:{domain}:{entry.entry_id}", BusKind.ZIGBEE, entry.title
            )
        # Wi-Fi devices that also advertise over Bluetooth list a network MAC.
        if (
            dr.CONNECTION_BLUETOOTH in connections
            and dr.CONNECTION_NETWORK_MAC not in connections
        ):
            return _bus(f"ble:{domain}:{dev.id}", BusKind.BLE, _name(dev, domain))
        if integration_type == "device" and entry:  # one entry per physical device
            return _bus(f"ip:{domain}:{entry.entry_id}", BusKind.IP, entry.title)
        root = _root(c.dev_reg, dev)
        if root.id != dev.id:
            return _bus(
                f"bridge:{domain}:{root.id}", BusKind.BRIDGE, _name(root, domain)
            )
        if integration_type == "hub" and entry:
            return _bus(
                f"bridge:{domain}:{entry.entry_id}", BusKind.BRIDGE, entry.title
            )
        return _bus(
            f"ip:{domain}:{dev.id}",
            BusKind.IP,
            _name(dev, entry.title if entry else domain),
        )
    if entry:
        return _bus(f"ip:{domain}:{entry.entry_id}", BusKind.IP, entry.title)
    return _unknown(c.entity.entity_id)


_RULES: Final[dict[str, Callable[[_Ctx, ConfigEntry], Bus]]] = {
    "zha": _zha,
    "deconz": _deconz,
    "hue": _hue,
    "mqtt": _mqtt,
    "zwave_js": _zwave,
    "matter": _matter,
    "homekit_controller": _homekit_controller,
    "yeelight": _yeelight,
    "switch_as_x": _switch_as_x,
}


@callback
def async_classify(hass: HomeAssistant, entity_id: str) -> Bus:
    """Return the bus of one physical (non-group) light, with default limits."""
    ent_reg = er.async_get(hass)
    if (entity := ent_reg.async_get(entity_id)) is None:
        return _unknown(entity_id)  # YAML entity without unique_id
    dev_reg = dr.async_get(hass)
    entry = (
        hass.config_entries.async_get_entry(entity.config_entry_id)
        if entity.config_entry_id
        else None
    )
    device = dev_reg.async_get(entity.device_id) if entity.device_id else None
    ctx = _Ctx(hass, ent_reg, dev_reg, entity, entry, device)
    if entry is not None and (rule := _RULES.get(entry.domain)):
        return rule(ctx, entry)
    return _generic(ctx)
