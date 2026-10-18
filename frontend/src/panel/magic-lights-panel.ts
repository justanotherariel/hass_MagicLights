// Panel root: waits for HA's elements, owns the store, routes /magic_lights/* and
// renders the one top bar every page shares.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { subscribeChanges } from "../shared/api";
import { ensureHaElements } from "../shared/ha-elements";
import type { HomeAssistant, PanelInfo, Route, Unsubscribe, Zone } from "../shared/types";
import { debounce, defineElement, errorMessage, navigate, type ConfirmRequest } from "../shared/util";
import {
  type Page,
  type TargetTab,
  go,
  goBack,
  homePath,
  parseRoute,
  replaceWith,
  settingsPath,
  targetPath,
} from "./routes";
import type { EditorTitle } from "./editor/ml-scene-editor";
import { MlStore, type StoreHost } from "./store";

const TAB_LABELS: Record<TargetTab, string> = {
  scenes: "Scenes",
  lights: "Lights",
  settings: "Settings",
};

interface ZoneDialogRequest {
  zone?: Zone;
}

class MagicLightsPanel extends LitElement implements StoreHost {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ type: Boolean, reflect: true }) narrow = false;

  @property({ attribute: false }) route?: Route;

  @property({ attribute: false }) panel?: PanelInfo;

  @state() private _store?: MlStore;

  /** HA elements that failed to load; undefined while loading. */
  @state() private _missing?: string[];

  @state() private _error?: string;

  @state() private _confirm?: ConfirmRequest;

  @state() private _confirmOpen = false;

  @state() private _zoneDialog?: ZoneDialogRequest;

  @state() private _editorTitle?: EditorTitle;

  private _unsubscribe?: Promise<Unsubscribe | undefined>;

  private _refresh = debounce(() => {
    this._store?.refresh().catch((err) => (this._error = errorMessage(err)));
  }, 250);

  connectedCallback(): void {
    super.connectedCallback();
    ensureHaElements().then((missing) => (this._missing = missing));
    this.addEventListener("ml-confirm", this._onConfirm as EventListener);
    this.addEventListener("ml-zone-dialog", this._onZoneDialog as EventListener);
    this.addEventListener("ml-editor-title", this._onEditorTitle as EventListener);
    if (this.hass) this._connect();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener("ml-confirm", this._onConfirm as EventListener);
    this.removeEventListener("ml-zone-dialog", this._onZoneDialog as EventListener);
    this.removeEventListener("ml-editor-title", this._onEditorTitle as EventListener);
    this._refresh.cancel();
    this._unsubscribe?.then((unsub) => unsub?.());
    this._unsubscribe = undefined;
  }

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("hass") && this.hass && !this._unsubscribe && this.isConnected) this._connect();
  }

  storeChanged(store: MlStore): void {
    this._store = store;
    this._error = undefined;
  }

  private _connect(): void {
    this._unsubscribe = subscribeChanges(this.hass, () => this._refresh()).catch(() => undefined);
    this._load();
  }

  private async _load(): Promise<void> {
    this._error = undefined;
    try {
      this._store = await MlStore.load(this);
    } catch (err) {
      this._error = errorMessage(err);
    }
  }

  protected render() {
    if (this._missing === undefined) {
      return html`<div class="center"><ha-spinner></ha-spinner></div>`;
    }
    const page = parseRoute(this.route?.path);
    return html`
      <ha-top-app-bar-fixed .narrow=${this.narrow}>
        ${this._renderNavigation(page)}
        <div slot="title">${this._title(page)}</div>
        ${this._renderActions(page)} ${this._renderTabs(page)}
        <div class="content ${page.name}">
          ${
            this._missing.length
              ? html`<ha-alert alert-type="warning" title="Some Home Assistant elements did not load">
                  Reload the page if parts of this panel look empty (${this._missing.join(", ")}).
                </ha-alert>`
              : nothing
          }
          ${this._renderBody(page)}
        </div>
      </ha-top-app-bar-fixed>
      ${this._renderConfirm()}
      ${
        this._zoneDialog && this._store
          ? html`<ml-zone-dialog
              .hass=${this.hass}
              .store=${this._store}
              .zone=${this._zoneDialog.zone}
              @ml-dialog-closed=${() => (this._zoneDialog = undefined)}
            ></ml-zone-dialog>`
          : nothing
      }
    `;
  }

  private _title(page: Page): string {
    const store = this._store;
    switch (page.name) {
      case "home":
        return this.panel?.title ?? "Magic Lights";
      case "settings":
        return "Settings";
      case "target":
        return store?.target(page.targetId)?.name ?? "Magic Lights";
      case "editor": {
        const { sceneId } = page;
        if (this._editorTitle?.sceneId === sceneId) return this._editorTitle.title || "New scene";
        const scene = store?.draft(sceneId) ?? store?.resolveScene(sceneId);
        return scene?.name || "New scene";
      }
      default:
        return "Magic Lights";
    }
  }

  private _parentPath(page: Page): string | undefined {
    switch (page.name) {
      case "target":
      case "settings":
      case "not_found":
        return homePath();
      case "editor":
        return targetPath(page.targetId);
      default:
        return undefined;
    }
  }

  private _renderNavigation(page: Page) {
    const parent = this._parentPath(page);
    return parent
      ? html`<ha-icon-button-arrow-prev
          slot="navigationIcon"
          @click=${() => goBack(parent)}
        ></ha-icon-button-arrow-prev>`
      : nothing;
  }

  private _renderActions(page: Page) {
    if (page.name === "home") {
      return html`
        <ha-icon-button slot="actionItems" .label=${"New zone"} @click=${() => (this._zoneDialog = {})}>
          <ha-icon icon="mdi:plus-box-multiple-outline"></ha-icon>
        </ha-icon-button>
        <ha-icon-button slot="actionItems" .label=${"Settings"} @click=${() => go(settingsPath())}>
          <ha-icon icon="mdi:cog-outline"></ha-icon>
        </ha-icon-button>
      `;
    }
    const target = page.name === "target" ? this._store?.target(page.targetId) : undefined;
    if (target?.kind === "zone") {
      const zone = this._store?.zoneOf(target.id);
      return html`<ha-icon-button
        slot="actionItems"
        .label=${"Edit zone"}
        @click=${() => (this._zoneDialog = { zone })}
      >
        <ha-icon icon="mdi:pencil-outline"></ha-icon>
      </ha-icon-button>`;
    }
    if (target?.area_id) {
      return html`<ha-icon-button
        slot="actionItems"
        .label=${"Edit area"}
        @click=${() => navigate(`/config/areas/area/${target.area_id}`)}
      >
        <ha-icon icon="mdi:pencil-outline"></ha-icon>
      </ha-icon-button>`;
    }
    return nothing;
  }

  private _renderTabs(page: Page) {
    if (page.name !== "target" || !this._store?.target(page.targetId)) return nothing;
    return html`<ha-tab-group slot="subRow" @wa-tab-show=${(ev: CustomEvent<{ name: string }>) =>
      this._tabShown(page, ev.detail.name as TargetTab)}>
      ${(Object.keys(TAB_LABELS) as TargetTab[]).map(
        (tab) =>
          html`<ha-tab-group-tab slot="nav" panel=${tab} .active=${page.tab === tab}>
            ${TAB_LABELS[tab]}
          </ha-tab-group-tab>`,
      )}
    </ha-tab-group>`;
  }

  private _tabShown(page: Page & { name: "target" }, tab: TargetTab): void {
    if (tab && tab !== page.tab) replaceWith(targetPath(page.targetId, tab));
  }

  private _renderBody(page: Page) {
    if (this._error) {
      return html`<ha-alert alert-type="error" title="Magic Lights is not available">
        ${this._error}
        <ha-button slot="action" appearance="plain" @click=${this._load}>Retry</ha-button>
      </ha-alert>`;
    }
    const store = this._store;
    if (!store) return html`<div class="center"><ha-spinner></ha-spinner></div>`;
    const gone = (page.name === "target" || page.name === "editor") && !store.target(page.targetId);
    if (page.name === "not_found" || gone) {
      return html`<ha-alert alert-type="warning" title="Not found">
        This room, zone or page does not exist (any more).
        <ha-button
          slot="action"
          appearance="plain"
          @click=${() => navigate(homePath(), { replace: true })}
        >
          Home
        </ha-button>
      </ha-alert>`;
    }
    switch (page.name) {
      case "home":
        return html`<ml-home .hass=${this.hass} .store=${store} .narrow=${this.narrow}></ml-home>`;
      case "settings":
        return html`<ml-settings .hass=${this.hass} .store=${store} .narrow=${this.narrow}></ml-settings>`;
      case "target":
        return html`<ml-target-page
          .hass=${this.hass}
          .store=${store}
          .targetId=${page.targetId}
          .tab=${page.tab}
          .narrow=${this.narrow}
        ></ml-target-page>`;
      case "editor":
        return html`<ml-scene-editor
          .hass=${this.hass}
          .store=${store}
          .targetId=${page.targetId}
          .sceneId=${page.sceneId}
          .narrow=${this.narrow}
        ></ml-scene-editor>`;
    }
  }

  private _onConfirm = (ev: CustomEvent<ConfirmRequest>): void => {
    ev.stopPropagation();
    this._confirm?.resolve(false);
    this._confirm = ev.detail;
    this._confirmOpen = true;
  };

  private _onEditorTitle = (ev: CustomEvent<EditorTitle>): void => {
    ev.stopPropagation();
    this._editorTitle = ev.detail;
  };

  private _onZoneDialog = (ev: CustomEvent<ZoneDialogRequest>): void => {
    ev.stopPropagation();
    this._zoneDialog = ev.detail;
  };

  private _renderConfirm() {
    const confirm = this._confirm;
    if (!confirm) return nothing;
    return html`<ha-dialog
      .open=${this._confirmOpen}
      type="alert"
      width="small"
      .headerTitle=${confirm.title}
      @closed=${(ev: Event) => {
        if (ev.target !== ev.currentTarget) return;
        confirm.resolve(false);
        this._confirm = undefined;
      }}
    >
      <p class="confirm-text">${confirm.text}</p>
      <ha-dialog-footer slot="footer">
        <ha-button slot="secondaryAction" appearance="plain" data-dialog="close">Cancel</ha-button>
        <ha-button
          slot="primaryAction"
          variant=${confirm.destructive ? "danger" : "brand"}
          @click=${() => {
            confirm.resolve(true);
            this._confirmOpen = false;
          }}
        >
          ${confirm.confirmText}
        </ha-button>
      </ha-dialog-footer>
    </ha-dialog>`;
  }

  static styles = css`
    :host {
      display: block;
      background-color: var(--primary-background-color);
      color: var(--primary-text-color);
      font-family: var(--ha-font-family-body);
      -webkit-font-smoothing: var(--ha-font-smoothing);
    }
    ha-tab-group {
      --ha-tab-active-text-color: var(--app-header-text-color, white);
      --ha-tab-indicator-color: var(--app-header-text-color, white);
      --ha-tab-track-color: transparent;
    }
    .content {
      box-sizing: border-box;
      max-width: 1040px;
      margin: 0 auto;
      padding: var(--ha-space-4) var(--ha-space-4) var(--ha-space-10);
    }
    .content.home {
      max-width: 1440px;
    }
    :host([narrow]) .content {
      padding: var(--ha-space-3) var(--ha-space-3) var(--ha-space-10);
    }
    .center {
      display: flex;
      justify-content: center;
      padding: var(--ha-space-10) 0;
    }
    .confirm-text {
      margin: 0;
    }
    ha-alert {
      display: block;
      margin-bottom: var(--ha-space-4);
    }
  `;
}

defineElement("magic-lights-panel", MagicLightsPanel);
