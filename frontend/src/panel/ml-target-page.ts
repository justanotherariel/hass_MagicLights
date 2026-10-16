// Target screen: status header with brightness and quick actions, then the Scenes / Lights / Settings tab.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { runningScene } from "../shared/status";
import type { HomeAssistant, SceneInfo, SelectAttributes } from "../shared/types";
import { defineElement, plural, toastOnError } from "../shared/util";
import "./ml-light-list";
import "./ml-scene-gallery";
import "./ml-target-settings";
import type { TargetTab } from "./routes";
import { menuId, type MlStore } from "./store";
import { pageStyles, tileStyles } from "./styles";
import { renderStrip, renderTargetIcon, targetVisuals, type TargetVisuals } from "./target-ui";

type Slider = HTMLElement & { value: number };

class MlTargetPage extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property({ attribute: false }) tab: TargetTab = "scenes";

  @property({ type: Boolean }) narrow = false;

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    const visuals = targetVisuals(this.store, this.hass, target);
    const attrs = visuals.view?.attrs;
    const own = attrs?.status === "scene" && !attrs.via && attrs.scene_id !== null;
    return html`
      <ha-card class="hero">
        <div class="summary">
          ${renderTargetIcon(this.hass, target, visuals)}
          <div class="info">
            <span class="status-title">${visuals.line}</span>
            <span class="status">${this._lightsLine(target.lights.length, attrs)}</span>
          </div>
        </div>
        ${renderStrip(visuals)} ${this._renderBrightness(visuals)}
        <div class="actions">
          ${
            visuals.manual.length
              ? html`<ha-button appearance="filled" variant="warning" size="s" @click=${this._reclaim}>
                  <ha-icon slot="start" icon="mdi:backup-restore"></ha-icon>
                  Reclaim ${plural(visuals.manual.length, "light")}
                </ha-button>`
              : nothing
          }
          ${this._renderMotion(visuals)}
          ${
            own
              ? html`<ha-button
                  appearance="plain"
                  size="s"
                  title="Leaves the lights as they are"
                  @click=${this._release}
                >
                  <ha-icon slot="start" icon="mdi:hand-back-right-outline"></ha-icon>
                  Stop scene
                </ha-button>`
              : nothing
          }
          <ha-button appearance="plain" size="s" .disabled=${!visuals.on} @click=${this._turnOff}>
            <ha-icon slot="start" icon="mdi:power"></ha-icon>
            Turn off
          </ha-button>
        </div>
      </ha-card>
      ${
        attrs?.errors?.length
          ? html`<ha-alert alert-type="warning" title="Some layers do not run">
              ${attrs.errors.join(" · ")}
            </ha-alert>`
          : nothing
      }
      ${this._renderTab()}
    `;
  }

  private _renderMotion(visuals: TargetVisuals) {
    const running = visuals.running;
    if (!visuals.playable || !running) return nothing;
    return html`<ha-button appearance="plain" size="s" @click=${() => this._toggleMotion(running)}>
      <ha-icon slot="start" .icon=${visuals.moving ? "mdi:pause" : "mdi:play"}></ha-icon>
      ${visuals.moving ? "Hold colours" : "Play colours"}
    </ha-button>`;
  }

  /** Level of the brightest light of the running scene, own or via. */
  private _renderBrightness(visuals: TargetVisuals) {
    const attrs = visuals.view?.attrs;
    const brightness = runningScene(attrs) ? attrs?.brightness : null;
    if (brightness == null) return nothing;
    return html`<div class="brightness">
      <ha-icon icon="mdi:brightness-6"></ha-icon>
      <ha-slider
        min="1"
        max="100"
        .value=${brightness}
        aria-label="Brightness"
        @change=${(ev: Event) => this._setBrightness(ev.target as Slider, brightness)}
      ></ha-slider>
      <span class="value">${brightness}%</span>
    </div>`;
  }

  /** "7 lights", or "4 of 7 lights" while the scene covers only some of them. */
  private _lightsLine(total: number, attrs: SelectAttributes | undefined): string {
    if (!attrs?.partial) return plural(total, "light");
    const running = Object.values(attrs.lights).filter((status) => status === "scene").length;
    return `${running} of ${plural(total, "light")}`;
  }

  private _renderTab() {
    switch (this.tab) {
      case "lights":
        return html`<ml-light-list
          .hass=${this.hass}
          .store=${this.store}
          .targetId=${this.targetId}
        ></ml-light-list>`;
      case "settings":
        return html`<ml-target-settings
          .hass=${this.hass}
          .store=${this.store}
          .targetId=${this.targetId}
          .narrow=${this.narrow}
        ></ml-target-settings>`;
      default:
        return html`<ml-scene-gallery
          .hass=${this.hass}
          .store=${this.store}
          .targetId=${this.targetId}
        ></ml-scene-gallery>`;
    }
  }

  private _toggleMotion(scene: SceneInfo): void {
    toastOnError(this, this.store.toggleMotion(this.targetId, menuId(scene)), "Could not change the motion");
  }

  private async _setBrightness(slider: Slider, current: number): Promise<void> {
    const value = slider.value;
    if (value === current) return;
    const saved = this.store.setBrightness(this.targetId, value);
    const ok = await toastOnError(this, saved, "Could not set the brightness");
    if (!ok) slider.value = current;
  }

  private _reclaim(): void {
    toastOnError(this, this.store.reclaim(this.targetId), "Could not reclaim the lights");
  }

  private _release(): void {
    toastOnError(this, this.store.release(this.targetId), "Could not stop the scene");
  }

  private _turnOff(): void {
    toastOnError(this, this.store.turnOff(this.targetId), "Could not turn off");
  }

  static styles = [
    pageStyles,
    tileStyles,
    css`
      .hero {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-3);
        padding: var(--ha-space-4);
        margin-bottom: var(--ha-space-4);
      }
      .summary {
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
      }
      .target-icon {
        width: 48px;
        height: 48px;
        --mdc-icon-size: 28px;
      }
      .info {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .status-title {
        font-size: var(--ha-font-size-xl);
        font-weight: var(--ha-font-weight-medium);
        line-height: var(--ha-line-height-condensed);
      }
      .strip {
        height: 8px;
      }
      .brightness {
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        color: var(--secondary-text-color);
        --mdc-icon-size: 20px;
      }
      .brightness ha-slider {
        flex: 1;
        width: auto;
        min-width: 0;
      }
      .brightness .value {
        min-width: 40px;
        text-align: end;
        font-variant-numeric: tabular-nums;
      }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2);
        margin-inline-start: calc(-1 * var(--ha-space-2));
      }
    `,
  ];
}

defineElement("ml-target-page", MlTargetPage);
