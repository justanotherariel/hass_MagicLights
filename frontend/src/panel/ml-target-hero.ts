// The room page's "now" card: an orb in what runs, one line of facts, the brightness bar
// and the actions that apply right now (Hold/Play only for moving colours, Stop only for
// the room's own saved scene, Reclaim only while lights are manual).

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { runningScene } from "../shared/status";
import type { HomeAssistant, TargetInfo } from "../shared/types";
import { defineElement, plural, toastOnError } from "../shared/util";
import { AMBER } from "./components/ml-value-bar";
import "./components/ml-value-bar";
import { heroText } from "./room-facts";
import type { MlStore } from "./store";
import { kelvinNow, renderTargetSwatch, targetVisuals, type TargetVisuals } from "./target-ui";

interface Action {
  label: string;
  icon: string;
  run: () => Promise<unknown>;
  failure: string;
  pressed?: boolean;
}

class MlTargetHero extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property({ type: Boolean, reflect: true }) narrow = false;

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    const visuals = targetVisuals(this.store, this.hass, target);
    const attrs = visuals.view?.attrs;
    const text = heroText(visuals.view, {
      names: this.store.statusNames,
      lights: target.lights.length,
      lightName: (id) => this.store.lightName(id),
      time: (iso) => new Date(iso).toLocaleTimeString(this.hass.language, { hour: "2-digit", minute: "2-digit" }),
      kelvinNow: kelvinNow(this.store, this.hass, visuals),
    });
    // A dark room has nothing to act on: no row of disabled buttons.
    const actions = visuals.on ? this._actions(target, visuals) : [];
    return html`
      <ha-card class="hero">
        <div class="summary">
          ${renderTargetSwatch(this.hass, target, visuals, 56)}
          <div class="info">
            <span class="title">${text.title}</span>
            <span class="facts">${text.facts.join(" · ")}</span>
          </div>
        </div>
        ${this._renderBrightness(visuals)}
        ${actions.length ? this._renderActions(actions) : nothing}
      </ha-card>
      ${
        attrs?.errors?.length
          ? html`<ha-alert alert-type="warning" title="Some layers do not run">${attrs.errors.join(" · ")}</ha-alert>`
          : nothing
      }
    `;
  }

  private _renderActions(actions: Action[]) {
    return html`<ha-control-button-group style=${`max-width: ${this.narrow ? 100 : Math.min(100, actions.length * 25)}%`}>
          ${actions.map(
            (a) => html`<ha-control-button
              class=${a.pressed ? "pressed" : ""}
              @click=${() => toastOnError(this, a.run(), a.failure)}
            >
              <span class="action"><ha-icon .icon=${a.icon}></ha-icon><span>${a.label}</span></span>
            </ha-control-button>`,
          )}
        </ha-control-button-group>`;
  }

  private _actions(target: TargetInfo, visuals: TargetVisuals): Action[] {
    const id = target.id;
    const attrs = visuals.view?.attrs;
    const actions: Action[] = [];
    const running = visuals.running;
    if (visuals.playable && running) {
      actions.push({
        label: visuals.moving ? "Hold colours" : "Play colours",
        icon: visuals.moving ? "mdi:pause" : "mdi:play",
        pressed: visuals.moving,
        run: () => this.store.toggleMotion(id, running.id),
        failure: "Could not change the motion",
      });
    }
    // A draft being tried has no scene id: the editor ends it, not Stop.
    if (attrs?.status === "scene" && !attrs.via && attrs.scene_id !== null) {
      actions.push({
        label: "Stop scene",
        icon: "mdi:hand-back-right-outline",
        run: () => this.store.release(id),
        failure: "Could not stop the scene",
      });
    }
    actions.push({
      label: "Turn off",
      icon: "mdi:power",
      run: () => this.store.turnOff(id),
      failure: "Could not turn off",
    });
    const manual = visuals.manual;
    if (manual.length) {
      actions.push({
        label: `Reclaim ${manual.length === 1 ? this.store.lightName(manual[0]) : plural(manual.length, "light")}`,
        icon: "mdi:backup-restore",
        run: () => this.store.reclaim(id),
        failure: "Could not reclaim the lights",
      });
    }
    return actions;
  }

  /** Level of the brightest light of the running scene, own or via. */
  private _renderBrightness(visuals: TargetVisuals) {
    const attrs = visuals.view?.attrs;
    const brightness = runningScene(attrs) ? attrs?.brightness : null;
    if (brightness == null) return nothing;
    return html`<ml-value-bar
      .value=${brightness}
      min="1"
      max="100"
      unit="%"
      label="Brightness"
      show-label
      icon="mdi:brightness-6"
      .color=${AMBER}
      @value-changed=${(ev: CustomEvent<{ value: number }>) => {
        if (ev.detail.value !== brightness) {
          toastOnError(this, this.store.setBrightness(this.targetId, ev.detail.value), "Could not set the brightness");
        }
      }}
    ></ml-value-bar>`;
  }

  static styles = css`
    :host {
      display: block;
    }
    .hero {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-4);
      padding: var(--ha-space-4);
      margin-bottom: var(--ha-space-2);
    }
    .summary {
      display: flex;
      align-items: center;
      gap: var(--ha-space-4);
      min-width: 0;
    }
    .info {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .title {
      font-size: var(--ha-font-size-xl);
      line-height: var(--ha-line-height-condensed);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .facts {
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    ha-control-button-group {
      --control-button-group-thickness: 48px;
      --control-button-group-spacing: var(--ha-space-3);
    }
    :host([narrow]) ha-control-button-group {
      --control-button-group-thickness: 60px;
      --control-button-group-spacing: var(--ha-space-2);
    }
    ha-control-button {
      --control-button-border-radius: var(--ha-border-radius-lg, 12px);
      --control-button-padding: var(--ha-space-1);
    }
    /* Tint and icon mark the toggle; the label keeps body-text contrast on the tint. */
    ha-control-button.pressed {
      --control-button-background-color: var(--primary-color);
    }
    ha-control-button.pressed ha-icon {
      color: var(--primary-color);
    }
    .action {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      font-size: var(--ha-font-size-m);
      min-width: 0;
    }
    .action span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    :host([narrow]) .action {
      flex-direction: column;
      gap: 2px;
      font-size: var(--ha-font-size-s);
    }
    ha-alert {
      display: block;
      margin-top: var(--ha-space-3);
    }
  `;
}

defineElement("ml-target-hero", MlTargetHero);
