// Lights tab: every light of the target with its state and power switch, Reclaim for
// manual lights, include/exclude for rooms and adding lights that have no area.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import { readableTint, rgbToHex, type Rgb } from "../shared/color";
import { ownerScene } from "../shared/status";
import type {
  HomeAssistant,
  LightStatus,
  SelectAttributes,
  TargetInfo,
  TargetOptions,
} from "../shared/types";
import { defineElement, fire, saveSwitch, showMoreInfo, toastOnError } from "../shared/util";
import type { MlStore } from "./store";
import { pageStyles } from "./styles";

const STATUS_TEXT: Record<LightStatus, string> = {
  scene: "In the scene",
  manual: "Manual",
  elsewhere: "Elsewhere",
  off: "Off",
  on: "On",
  unavailable: "Unavailable",
};

const MEMBERSHIP = {
  exclude: { icon: "mdi:minus-circle-outline", label: "Exclude from this room" },
  include: { icon: "mdi:plus-circle-outline", label: "Include in this room" },
  remove: { icon: "mdi:close", label: "Remove from this room" },
};

type Membership = keyof typeof MEMBERSHIP;

class MlLightList extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    const attrs = this.store.status(this.hass, target.id)?.attrs;
    const isRoom = target.kind === "room";
    const excluded = isRoom ? target.options.excluded_lights : [];
    const rows = [...target.lights, ...excluded.filter((e) => !target.lights.includes(e))].sort((a, b) =>
      this.store.lightName(a).localeCompare(this.store.lightName(b)),
    );
    return html`
      <ha-card>
        ${
          rows.length
            ? html`<ha-list-base aria-label="Lights">
                ${rows.map((entityId) => this._renderRow(target, entityId, attrs))}
              </ha-list-base>`
            : html`<p class="empty">No lights.</p>`
        }
        ${isRoom ? this._renderAdd(target) : this._renderZoneHint(target)}
      </ha-card>
    `;
  }

  private _renderRow(target: TargetInfo, entityId: string, attrs: SelectAttributes | undefined) {
    const included = target.lights.includes(entityId);
    const membership: Membership = target.options.extra_lights.includes(entityId)
      ? "remove"
      : included
        ? "exclude"
        : "include";
    const status = attrs?.lights?.[entityId];
    const state = this.hass.states[entityId];
    const on = state?.state === "on";
    const reachable = !!state && state.state !== "unavailable";
    const rgb = state?.attributes.rgb_color as Rgb | undefined;
    const tint = rgb && readableTint([rgbToHex(rgb)], this.hass.themes?.darkMode);
    const iconStyle = on ? { color: tint ?? "var(--state-light-active-color)" } : {};
    return html`<ha-list-item-base class=${included ? "" : "excluded"}>
      <ha-icon
        slot="start"
        class="bulb"
        style=${styleMap(iconStyle)}
        .icon=${
          this.hass.entities?.[entityId]?.icon ??
          state?.attributes.icon ??
          (on ? "mdi:lightbulb-on" : "mdi:lightbulb-outline")
        }
      ></ha-icon>
      <button slot="content" class="row-main" @click=${() => showMoreInfo(this, entityId)}>
        <span class="headline">${this.store.lightName(entityId)}</span>
        <span class="supporting">${included ? this._describe(entityId, status, attrs) : "Excluded"}</span>
      </button>
      <div slot="end" class="end">
        ${
          status === "manual"
            ? html`<ha-button
                size="s"
                appearance="plain"
                @click=${() =>
                  toastOnError(this, this.store.reclaim(target.id, entityId), "Could not reclaim the light")}
              >
                Reclaim
              </ha-button>`
            : nothing
        }
        <ha-switch
          .checked=${on}
          .disabled=${!reachable}
          aria-label=${`${this.store.lightName(entityId)} power`}
          @change=${(ev: Event) =>
            saveSwitch(this, ev, (checked) => this._power(entityId, checked), "Could not switch the light")}
        ></ha-switch>
        ${target.kind === "room" ? this._renderMembership(target, entityId, membership) : nothing}
      </div>
    </ha-list-item-base>`;
  }

  /** The room's membership of a light sits in a menu, apart from the power switch. */
  private _renderMembership(target: TargetInfo, entityId: string, action: Membership) {
    const { icon, label } = MEMBERSHIP[action];
    return html`<ha-dropdown
      placement="bottom-end"
      @wa-select=${() => this._membership(target, entityId, action)}
    >
      <ha-icon-button slot="trigger" .label=${`${this.store.lightName(entityId)} options`}>
        <ha-icon icon="mdi:dots-vertical"></ha-icon>
      </ha-icon-button>
      <ha-dropdown-item value=${action}>
        <ha-icon slot="icon" .icon=${icon}></ha-icon>
        ${label}
      </ha-dropdown-item>
    </ha-dropdown>`;
  }

  private _describe(entityId: string, status: LightStatus | undefined, attrs: SelectAttributes | undefined) {
    const state = this.hass.states[entityId];
    if (!status) return state ? this.hass.formatEntityState(state) : "";
    const owner = status === "elsewhere" ? attrs?.elsewhere?.[entityId] : undefined;
    if (!owner) return STATUS_TEXT[status];
    return `${this.store.targetName(owner.target_id)}: ${ownerScene(owner, this.store.statusNames)}`;
  }

  private _renderAdd(target: TargetInfo) {
    const candidates = this.store.config.lights
      .filter((l) => l.area_id === null && !target.lights.includes(l.entity_id))
      .map((l) => l.entity_id);
    if (!candidates.length) return nothing;
    return html`<div class="add">
      <ha-selector
        .hass=${this.hass}
        .selector=${{ entity: { include_entities: candidates } }}
        .label=${"Add a light without an area"}
        .required=${false}
        .value=${undefined}
        @value-changed=${(ev: CustomEvent<{ value?: string }>) => this._addExtra(target, ev)}
      ></ha-selector>
    </div>`;
  }

  private _renderZoneHint(target: TargetInfo) {
    const zone = this.store.zoneOf(target.id);
    return html`<div class="add zone">
      <span class="secondary">The zone's rooms and extra lights decide which lights it has.</span>
      <ha-button appearance="plain" size="s" @click=${() => fire(this, "ml-zone-dialog", { zone })}>
        Edit zone
      </ha-button>
    </div>`;
  }

  /** Switching a light in a scene by hand makes it manual. */
  private _power(entityId: string, on: boolean): Promise<unknown> {
    return this.hass.callService("light", on ? "turn_on" : "turn_off", {}, { entity_id: entityId });
  }

  private _membership(target: TargetInfo, entityId: string, action: Membership): void {
    const without = (ids: string[]) => ids.filter((e) => e !== entityId);
    switch (action) {
      case "exclude":
        return this._save(target, (o) => ({ excluded_lights: [...without(o.excluded_lights), entityId] }));
      case "include":
        return this._save(target, (o) => ({ excluded_lights: without(o.excluded_lights) }));
      case "remove":
        return this._save(
          target,
          (o) => ({ extra_lights: without(o.extra_lights) }),
          "Could not remove the light",
        );
    }
  }

  private _addExtra(target: TargetInfo, ev: CustomEvent<{ value?: string }>): void {
    const entityId = ev.detail.value;
    if (!entityId) return;
    (ev.target as HTMLElement & { value?: string }).value = undefined;
    this._save(
      target,
      (o) => ({ extra_lights: [...o.extra_lights.filter((e) => e !== entityId), entityId] }),
      "Could not add the light",
    );
  }

  private _save(
    target: TargetInfo,
    update: (options: TargetOptions) => Partial<TargetOptions>,
    failure = "Could not change the room",
  ): void {
    toastOnError(this, this.store.saveTargetOptions(target.id, (latest) => update(latest.options)), failure);
  }

  static styles = [
    pageStyles,
    css`
      ha-card {
        overflow: hidden;
      }
      ha-list-base {
        display: block;
        padding: var(--ha-space-2) 0;
      }
      .excluded .bulb,
      .excluded .headline {
        opacity: 0.55;
      }
      .bulb {
        color: var(--state-inactive-color, var(--disabled-text-color));
      }
      .row-main {
        display: flex;
        flex-direction: column;
        min-width: 0;
        width: 100%;
        margin: 0;
        padding: 0;
        border: none;
        background: none;
        color: inherit;
        font: inherit;
        text-align: start;
        cursor: pointer;
      }
      .row-main:focus-visible {
        outline: 2px solid var(--ha-color-focus, var(--primary-color));
        border-radius: var(--ha-border-radius-sm);
      }
      .headline,
      .supporting {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .supporting {
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
      }
      .end {
        display: flex;
        align-items: center;
        gap: var(--ha-space-1);
      }
      .add {
        padding: var(--ha-space-2) var(--ha-space-4) var(--ha-space-4);
        border-top: 1px solid var(--divider-color);
      }
      .add.zone {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: var(--ha-space-2);
        padding-top: var(--ha-space-3);
      }
      .empty {
        margin: 0;
        padding: var(--ha-space-4);
        color: var(--secondary-text-color);
      }
    `,
  ];
}

defineElement("ml-light-list", MlLightList);
