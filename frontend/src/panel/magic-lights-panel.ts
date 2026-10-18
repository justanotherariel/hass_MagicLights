// Panel root: waits for HA's elements, owns the store, routes /magic_lights/* and
// renders the one top bar every page shares. Each route has its own bar and body block
// below, so a page's bar can change without touching the others.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { subscribeChanges } from "../shared/api";
import { ensureHaElements } from "../shared/ha-elements";
import type { HomeAssistant, PanelInfo, Route, Unsubscribe, Zone } from "../shared/types";
import { debounce, defineElement, errorMessage, navigate, type ConfirmRequest } from "../shared/util";
import { renderEditorBar, type EditorTitle } from "./editor/ml-editor-bar";
import {
  type HomeTab,
  type Page,
  type TargetTab,
  editorPath,
  go,
  goBack,
  homePath,
  parentPath,
  parseRoute,
  replaceWith,
  settingsPath,
  targetPath,
} from "./routes";
import { MlStore, type StoreHost } from "./store";

const HOME_TABS: Record<HomeTab, string> = { rooms: "Rooms", scenes: "Scenes" };

const TARGET_TABS: Record<TargetTab, string> = {
  scenes: "Scenes",
  lights: "Lights",
  settings: "Settings",
};

type PageOf<N extends Page["name"]> = Extract<Page, { name: N }>;

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

  /** Routes that only lead elsewhere: an old link, or a new scene that needs its draft first. */
  protected updated(): void {
    const page = this._page;
    if (page.name === "redirect") replaceWith(page.path);
    const store = this._store;
    if (page.name === "new_scene" && store && (!page.targetId || store.target(page.targetId))) {
      const draft = store.newScene(page.targetId);
      store.setDraft(draft.id, draft);
      replaceWith(editorPath(draft.id, page.targetId));
    }
  }

  storeChanged(store: MlStore): void {
    this._store = store;
    this._error = undefined;
  }

  private get _page(): Page {
    return parseRoute(this.route?.path);
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
    const page = this._page;
    const parent = parentPath(page);
    return html`
      <ha-top-app-bar-fixed .narrow=${this.narrow}>
        ${
          parent
            ? html`<ha-icon-button-arrow-prev
                slot="navigationIcon"
                @click=${() => goBack(parent)}
              ></ha-icon-button-arrow-prev>`
            : nothing
        }
        ${this._renderBar(page)}
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

  /** Title, actions and tabs of the top bar. */
  private _renderBar(page: Page) {
    switch (page.name) {
      case "home":
        return this._homeBar(page);
      case "settings":
        return html`<div slot="title">Magic Lights settings</div>`;
      case "target":
        return this._targetBar(page);
      case "editor":
      case "new_scene":
        return this._editorBar(page);
      default:
        return html`<div slot="title">Magic Lights</div>`;
    }
  }

  private _renderBody(page: Page) {
    if (this._error) {
      return html`<ha-alert alert-type="error" title="Magic Lights is not available">
        ${this._error}
        <ha-button slot="action" appearance="plain" @click=${this._load}>Retry</ha-button>
      </ha-alert>`;
    }
    const store = this._store;
    if (!store) return this._spinner();
    const target = "targetId" in page && page.targetId ? store.target(page.targetId) : undefined;
    if (page.name === "not_found" || ("targetId" in page && page.targetId && !target)) {
      return this._notFound();
    }
    switch (page.name) {
      case "home":
        return this._homeBody(store, page);
      case "settings":
        return html`<ml-settings .hass=${this.hass} .store=${store} .narrow=${this.narrow}></ml-settings>`;
      case "target":
        return this._targetBody(store, page);
      case "editor":
        return this._editorBody(store, page);
      default:
        // A redirect or a new scene, resolved in updated().
        return this._spinner();
    }
  }

  // ---- home: Rooms | Scenes

  private _homeBar(page: PageOf<"home">) {
    const tabs = html`<ha-tab-group
      class="home-tabs"
      slot=${this.narrow ? "subRow" : nothing}
      @wa-tab-show=${(ev: CustomEvent<{ name: string }>) => {
        const tab = ev.detail.name as HomeTab;
        if (tab && tab !== page.tab) replaceWith(homePath(tab));
      }}
    >
      ${(Object.keys(HOME_TABS) as HomeTab[]).map(
        (tab) =>
          html`<ha-tab-group-tab slot="nav" panel=${tab} .active=${page.tab === tab}>
            ${HOME_TABS[tab]}
          </ha-tab-group-tab>`,
      )}
    </ha-tab-group>`;
    const title = this.panel?.title ?? "Magic Lights";
    return html`
      ${
        this.narrow
          ? html`<div slot="title">${title}</div>
              ${tabs}`
          : html`<div slot="title" class="title-with-tabs"><span class="title-text">${title}</span>${tabs}</div>`
      }
      ${
        page.tab === "rooms"
          ? html`<ha-icon-button slot="actionItems" .label=${"New zone"} @click=${() => (this._zoneDialog = {})}>
              <ha-icon icon="mdi:plus"></ha-icon>
            </ha-icon-button>`
          : nothing
      }
      <ha-icon-button slot="actionItems" .label=${"Settings"} @click=${() => go(settingsPath())}>
        <ha-icon icon="mdi:cog"></ha-icon>
      </ha-icon-button>
    `;
  }

  private _homeBody(store: MlStore, page: PageOf<"home">) {
    return page.tab === "scenes"
      ? html`<ml-scenes .hass=${this.hass} .store=${store} .narrow=${this.narrow}></ml-scenes>`
      : html`<ml-home .hass=${this.hass} .store=${store} .narrow=${this.narrow}></ml-home>`;
  }

  // ---- room or zone

  private _targetBar(page: PageOf<"target">) {
    const target = this._store?.target(page.targetId);
    return html`
      <div slot="title">${target?.name ?? "Magic Lights"}</div>
      ${this._targetActions(page)}
      ${
        target
          ? html`<ha-tab-group
              slot="subRow"
              @wa-tab-show=${(ev: CustomEvent<{ name: string }>) => {
                const tab = ev.detail.name as TargetTab;
                if (tab && tab !== page.tab) replaceWith(targetPath(page.targetId, tab));
              }}
            >
              ${(Object.keys(TARGET_TABS) as TargetTab[]).map(
                (tab) =>
                  html`<ha-tab-group-tab slot="nav" panel=${tab} .active=${page.tab === tab}>
                    ${TARGET_TABS[tab]}
                  </ha-tab-group-tab>`,
              )}
            </ha-tab-group>`
          : nothing
      }
    `;
  }

  private _targetActions(page: PageOf<"target">) {
    const target = this._store?.target(page.targetId);
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

  private _targetBody(store: MlStore, page: PageOf<"target">) {
    return html`<ml-target-page
      .hass=${this.hass}
      .store=${store}
      .targetId=${page.targetId}
      .tab=${page.tab}
      .narrow=${this.narrow}
    ></ml-target-page>`;
  }

  // ---- scene editor (target-free; the route's room is where it previews and tries)

  private _editorBar(page: PageOf<"editor"> | PageOf<"new_scene">) {
    const store = this._store;
    let title = page.name === "new_scene" ? "New scene" : "Scene";
    let bar: EditorTitle | undefined;
    if (page.name === "editor") {
      const { sceneId } = page;
      if (this._editorTitle?.sceneId === sceneId) bar = this._editorTitle;
      else title = (store?.draft(sceneId) ?? store?.resolveScene(sceneId))?.name || title;
    }
    return renderEditorBar(bar, title, this.narrow);
  }

  private _editorBody(store: MlStore, page: PageOf<"editor">) {
    const preview = page.targetId ?? store.previewTarget(page.sceneId)?.id;
    return html`<ml-scene-editor
      .hass=${this.hass}
      .store=${store}
      .targetId=${preview ?? ""}
      .sceneId=${page.sceneId}
      .parent=${parentPath(page) ?? homePath("scenes")}
      .narrow=${this.narrow}
    ></ml-scene-editor>`;
  }

  // ---- shared

  private _spinner() {
    return html`<div class="center"><ha-spinner></ha-spinner></div>`;
  }

  private _notFound() {
    return html`<ha-alert alert-type="warning" title="Not found">
      This room, zone or page does not exist (any more).
      <ha-button slot="action" appearance="plain" @click=${() => navigate(homePath(), { replace: true })}>
        Home
      </ha-button>
    </ha-alert>`;
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
    /* Wide: the home tabs sit in the bar, centred in the space the title leaves. */
    .title-with-tabs {
      display: flex;
      align-items: center;
      gap: var(--ha-space-6);
      min-width: 0;
    }
    .title-text {
      flex: none;
    }
    .title-with-tabs .home-tabs {
      flex: 1;
      display: flex;
      justify-content: center;
      font-size: var(--ha-font-size-m);
      line-height: normal;
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
    .content.editor {
      max-width: 1280px;
    }
    :host([narrow]) .content {
      padding: var(--ha-space-3) var(--ha-space-4) var(--ha-space-10);
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
